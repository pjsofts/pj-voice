"""Simulated-caller evals (SPEC.md §9).

Agent and an LLM-played caller talk through the text runner, against an
in-process booking tool server with a fresh demo backend per scenario.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
import yaml
from pydantic import BaseModel, Field

from ..booking.app import create_app as create_tools_app
from ..booking.backend import MemoryBackend, demo_backend
from ..engine.config import AssistantConfig
from ..engine.tools import HttpToolExecutor
from ..text.runner import ChatClient, OpenAIChatClient, TextRunner

CALLER_DONE = "<hangup>"


class Validator(BaseModel):
    type: Literal["tool_called", "tool_not_called", "appointment_count", "transcript_includes", "transcript_excludes", "ended"]
    tool: Optional[str] = None
    equals: Optional[int] = None
    pattern: Optional[str] = None
    weight: float = 1.0


class Scenario(BaseModel):
    name: str
    persona: str
    caller_phone: str = "+15550142"
    max_turns: int = 14
    pass_score: float = 0.8
    validators: list[Validator] = Field(min_length=1)

    @classmethod
    def from_file(cls, path: str | Path) -> "Scenario":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


@dataclass
class ScenarioResult:
    name: str
    score: float
    passed: bool
    checks: list[tuple[str, bool, float]] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)


def check(v: Validator, tools_used: list[str], transcript: str, backend: Optional[MemoryBackend], ended: bool) -> bool:
    if v.type == "tool_called":
        return v.tool in tools_used
    if v.type == "tool_not_called":
        return v.tool not in tools_used
    if v.type == "appointment_count":
        if backend is None:  # custom tool server: no backend state to inspect
            return False
        return sum(1 for a in backend.all_appointments() if a.status == "booked") == v.equals
    if v.type == "transcript_includes":
        return re.search(v.pattern or "", transcript, re.I) is not None
    if v.type == "transcript_excludes":
        return re.search(v.pattern or "", transcript, re.I) is None
    if v.type == "ended":
        return ended
    return False


async def run_scenario(
    assistant: AssistantConfig,
    scenario: Scenario,
    agent_chat: Optional[ChatClient] = None,
    caller_chat: Optional[ChatClient] = None,
    caller_model: str = "openai/gpt-4.1-mini",
    backend: Optional[MemoryBackend] = None,
    tools_app: Any = None,
) -> ScenarioResult:
    """Run one scenario. ``tools_app`` (any ASGI app) replaces the demo booking server."""
    if tools_app is None:
        backend = backend or demo_backend(assistant.timezone)
        tools_app = create_tools_app(backend, api_key="")
    else:
        backend = None
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=tools_app), base_url="http://tools")
    # Point every tool at the in-process server.
    assistant = assistant.model_copy(
        update={"tools": [t.model_copy(update={"url": "http://tools/tools", "headers": {}}) for t in assistant.tools]}
    )
    runner = TextRunner(chat=agent_chat or OpenAIChatClient(), tools=HttpToolExecutor(client))
    caller_chat = caller_chat or agent_chat or OpenAIChatClient()
    convo = runner.start(assistant, f"eval-{uuid.uuid4().hex[:8]}", scenario.caller_phone)

    transcript: list[tuple[str, str]] = []
    if assistant.greeting:
        transcript.append(("agent", assistant.greeting))
    caller_system = (
        f"You are a person phoning a business. {scenario.persona}\n"
        "Speak naturally and briefly, like on a phone call. Answer only what you are asked. "
        f"When your goal is done or the agent says goodbye, reply with exactly {CALLER_DONE}."
    )
    try:
        for _ in range(scenario.max_turns):
            # The caller sees the agent as "user" and itself as "assistant".
            history = [{"role": "system", "content": caller_system}] + [
                {"role": "user" if who == "agent" else "assistant", "content": text} for who, text in transcript
            ]
            if not transcript:
                history.append({"role": "user", "content": "(the call connects)"})
            msg = await caller_chat.complete(caller_model, history, [])
            said = (msg.get("content") or "").strip()
            if not said or CALLER_DONE in said:
                break
            transcript.append(("caller", said))
            result = await runner.turn(convo, said)
            if result.reply:
                transcript.append(("agent", result.reply))
            if result.ended or result.transfer_to:
                break
    finally:
        await client.aclose()

    tools_used = [e["tool"] for e in convo.ctx.tool_log] + [f"to_{s}" for s in convo.graph.history[1:]]
    agent_text = "\n".join(t for who, t in transcript if who == "agent")
    checks = []
    for v in scenario.validators:
        label = f"{v.type}" + (f":{v.tool}" if v.tool else "") + (f"={v.equals}" if v.equals is not None else "") + (f" /{v.pattern}/" if v.pattern else "")
        checks.append((label, check(v, tools_used, agent_text, backend, convo.ended), v.weight))
    total = sum(w for _, _, w in checks)
    score = sum(w for _, ok, w in checks if ok) / total if total else 0.0
    return ScenarioResult(scenario.name, round(score, 3), score >= scenario.pass_score, checks, transcript)


def format_result(r: ScenarioResult, verbose: bool = False) -> str:
    lines = [f"{'PASS' if r.passed else 'FAIL'}  {r.score:.2f}  {r.name}"]
    for label, ok, w in r.checks:
        lines.append(f"    {'✓' if ok else '✗'} {label} (w={w:g})")
    if verbose or not r.passed:
        lines += [f"    {who:>6}: {text}" for who, text in r.transcript]
    return "\n".join(lines)


def load_scenarios(path: str | Path) -> list[Scenario]:
    p = Path(path)
    files = sorted(p.glob("*.yaml")) if p.is_dir() else [p]
    return [Scenario.from_file(f) for f in files]


def summarize(results: list[ScenarioResult]) -> dict[str, Any]:
    return {
        "scenarios": len(results),
        "passed": sum(r.passed for r in results),
        "mean_score": round(sum(r.score for r in results) / len(results), 3) if results else 0.0,
    }
