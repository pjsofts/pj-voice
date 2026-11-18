"""Keeps the tutorial honest: the restaurant example must work as documented."""

import httpx
import pytest

from examples.restaurant import server
from pjvoice.engine.config import AssistantConfig
from pjvoice.engine.tools import HttpToolExecutor
from pjvoice.text.runner import TextRunner

from conftest import ROOT, ScriptedChat, call, say


@pytest.fixture(autouse=True)
def reset():
    server.bookings.clear()
    server.sessions.clear()


async def test_tutorial_flow():
    cfg = AssistantConfig.from_file(ROOT / "examples/restaurant/assistant.yaml")
    cfg = cfg.model_copy(update={"tools": [t.model_copy(update={"url": "http://r/tools"}) for t in cfg.tools]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://r") as client:
        chat = ScriptedChat([
            call("check_tables", date="2026-11-20", party_size=4),
            say("I have 5, 5:30 or 6. Which would you like?"),
        ])
        runner = TextRunner(chat=chat, tools=HttpToolExecutor(client))
        convo = runner.start(cfg, "r1", "+15550142")
        await runner.turn(convo, "table for four Friday the 20th")
        assert "option_id opt1" in convo.messages[-2]["content"]

        chat.script += [call("reserve", option_id="opt2", name="Dana"), say("Booked for 5:30.")]
        await runner.turn(convo, "5:30 under Dana")
        (b,) = server.bookings.values()
        assert (b.party, b.table, b.phone, b.start.hour, b.start.minute) == (4, "t4a", "+15550142", 17, 30)

        chat.script += [call("reserve", option_id="opt2", name="Dana"), say("You're all set.")]
        await runner.turn(convo, "did it go through?")
        assert len(server.bookings) == 1


def test_guards():
    s = server.Session()
    with pytest.raises(server.ToolError, match="not offered"):
        server.reserve(s, {"option_id": "opt9", "name": "X Y"})
    with pytest.raises(server.ToolError, match="1 to 6"):
        server.check_tables(s, {"date": "2026-11-20", "party_size": 9})
    first = server.Session()
    server.check_tables(first, {"date": "2026-11-20", "party_size": 6})
    server.reserve(first, {"option_id": "opt1", "name": "Big Party"})  # the only 6-top, 5:00-6:30 PM
    # A second party of 6 can't overlap that sitting; the first offer is 6:30 PM.
    assert server.check_tables(server.Session(), {"date": "2026-11-20", "party_size": 6}).split(": ")[1].startswith("6:30 PM")


async def test_eval_harness_with_custom_tools_app():
    from pjvoice.evals.harness import Scenario, run_scenario

    cfg = AssistantConfig.from_file(ROOT / "examples/restaurant/assistant.yaml")
    agent = ScriptedChat([
        call("check_tables", date="2026-11-20", party_size=4),
        say("I have 5, 5:30 or 6."),
        call("reserve", option_id="opt1", name="Sam"),
        {"content": "Booked, see you then!", "tool_calls": [{"id": "e", "name": "end_call", "arguments": "{}"}]},
    ])
    caller = ScriptedChat([say("Table for four Friday"), say("5 is fine, name Sam")])
    sc = Scenario.from_file(ROOT / "examples/restaurant/evals/books-table.yaml")
    r = await run_scenario(cfg, sc, agent_chat=agent, caller_chat=caller, tools_app=server.app)
    assert r.passed and r.score == 1.0, r.checks
