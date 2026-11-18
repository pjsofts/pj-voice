from pjvoice.engine.tools import HttpToolExecutor
from pjvoice.text.runner import TextRunner

from conftest import ScriptedChat, call, say


def booking_text(chat):
    """Text of the last tool result the model was shown."""
    return chat.calls[-1]["messages"][-1]["content"]


async def test_new_customer_books_through_stages(salon, tools_client, backend):
    chat = ScriptedChat([
        call("find_customer"),
        say("Hi! I don't see you yet. What's your name?"),
        call("to_booking"),
        call("create_customer", name="Dana Kim"),
        call("check_availability", service_id="haircut", date="2026-11-17"),
        say("I have 9 AM with Sam or 9:30. Which works?"),
    ])
    runner = TextRunner(chat=chat, tools=HttpToolExecutor(tools_client))
    convo = runner.start(salon, "s1", "+15550142")

    r1 = await runner.turn(convo, "Hi, I'd like a haircut tomorrow")
    assert r1.stage == "triage"
    # caller_phone was injected, never shown to the model
    assert convo.ctx.tool_log[0]["arguments"] == {"caller_phone": "+15550142"}
    assert "find_customer" in chat.calls[0]["tools"] and "book_appointment" not in chat.calls[0]["tools"]

    r2 = await runner.turn(convo, "Dana Kim")
    assert r2.stage == "booking"
    assert "book_appointment" in chat.calls[-1]["tools"]
    offered = [s for s in convo.messages if s["role"] == "tool"][-1]["content"]
    slot_id = offered.split("slot_id ")[1].split(")")[0]

    chat.script += [call("book_appointment", slot_id=slot_id), say("You're booked for 9 AM tomorrow.")]
    await runner.turn(convo, "9 is great")
    booked = [a for a in backend.all_appointments() if a.status == "booked"]
    assert len(booked) == 1 and backend.get_customer(booked[0].customer_id).name == "Dana Kim"

    # idempotent repeat does not double-book
    chat.script += [call("book_appointment", slot_id=slot_id), say("All set.")]
    await runner.turn(convo, "did that go through?")
    assert len([a for a in backend.all_appointments() if a.status == "booked"]) == 1


async def test_invented_slot_and_unidentified_caller_are_refused(salon, tools_client, backend):
    chat = ScriptedChat([
        call("to_booking"),
        call("book_appointment", slot_id="haircut|sam|20261117T1400Z"),
        say("Let me look you up first."),
    ])
    runner = TextRunner(chat=chat, tools=HttpToolExecutor(tools_client))
    convo = runner.start(salon, "s2", "+15550111")
    await runner.turn(convo, "book me at 9")
    tool_msgs = [m["content"] for m in convo.messages if m["role"] == "tool"]
    assert "No customer is identified" in tool_msgs[-1]
    assert backend.all_appointments() == []

    chat.script += [
        call("create_customer", name="Jordan Lee"),
        call("book_appointment", slot_id="haircut|sam|20261117T1400Z"),
        say("I need to check times first."),
    ]
    await runner.turn(convo, "it's Jordan")
    tool_msgs = [m["content"] for m in convo.messages if m["role"] == "tool"]
    assert "already exists" in tool_msgs[-2]  # existing profile reused, not duplicated
    assert "not offered on this call" in tool_msgs[-1]
    assert backend.all_appointments() == []


async def test_tool_outside_stage_and_transfer(salon, tools_client):
    chat = ScriptedChat([
        call("cancel_appointment", appointment_id="A0001"),
        call("transfer_call", target_id="front_desk"),
        say("Connecting you now."),
    ])
    runner = TextRunner(chat=chat, tools=HttpToolExecutor(tools_client))
    convo = runner.start(salon, "s3", "+15550111")
    r = await runner.turn(convo, "cancel and get me a human")
    tool_msgs = [m["content"] for m in convo.messages if m["role"] == "tool"]
    assert "not available in this step" in tool_msgs[0]
    assert r.transfer_to == "front_desk"


async def test_fallback_llm_used_on_primary_failure(salon, tools_client):
    chat = ScriptedChat([RuntimeError("provider down"), say("Hello! How can I help?")])
    runner = TextRunner(chat=chat, tools=HttpToolExecutor(tools_client))
    convo = runner.start(salon, "s4")
    r = await runner.turn(convo, "hi")
    assert r.reply == "Hello! How can I help?"
    assert chat.calls[-1]["model"] == salon.models.fallback_llm


async def test_end_call(salon, tools_client):
    chat = ScriptedChat([{"content": "Bye now!", "tool_calls": [{"id": "x", "name": "end_call", "arguments": "{}"}]}])
    runner = TextRunner(chat=chat, tools=HttpToolExecutor(tools_client))
    convo = runner.start(salon, "s5")
    r = await runner.turn(convo, "that's all, bye")
    assert r.ended and r.reply == "Bye now!"
