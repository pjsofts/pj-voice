"""Framework-agnostic stage graph (SPEC.md §3.1).

Both the voice worker and the text runner drive this object. It decides which
instructions and tools are active, turns transitions into ``to_<stage>`` tools,
and enforces the hop caps that stop a model ping-ponging between stages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional

from .config import AssistantConfig, Stage, StageGraphConfig, ToolSpec

logger = logging.getLogger(__name__)

HANDOFF_PREFIX = "to_"
BUILTIN_TOOLS = ("end_call", "transfer_call")

HOP_REFUSED = (
    "Stay in the current step and keep helping the caller here; do not switch steps "
    "again. Answer what they just said, and if this step does not cover it, say so "
    "and ask what they need."
)

DEFAULT_RULES = (
    "You are speaking on the phone. Keep replies to one or two short sentences. "
    "Never state a price, time, date, name or policy unless a tool result gave it to you. "
    "Before any booking, change or cancellation, read the details back and get a clear yes."
)


@dataclass(frozen=True)
class Handoff:
    name: str
    target: str
    description: str


class StageGraph:
    def __init__(self, assistant: AssistantConfig) -> None:
        self.assistant = assistant
        self.config: StageGraphConfig = assistant.flow or StageGraphConfig(
            initial="main",
            stages=[
                Stage(
                    name="main",
                    instructions=assistant.instructions,
                    tools=[t.name for t in assistant.tools],
                )
            ],
        )
        self.current: str = self.config.initial
        self.history: list[str] = [self.current]
        self.hops = 0
        self.silent_hops = 0
        known = {t.name for t in assistant.tools} | set(BUILTIN_TOOLS)
        for s in self.config.stages:
            missing = [n for n in s.tools if n not in known]
            if missing:
                logger.warning("stage %r references unknown tools %s (skipped)", s.name, missing)

    # ── state ──────────────────────────────────────────────────────────────
    @property
    def stage(self) -> Stage:
        return self.config.get(self.current)

    def note_speech(self) -> None:
        """The agent said something to the caller: it isn't spinning."""
        self.silent_hops = 0

    def try_handoff(self, target: str) -> bool:
        """Switch stage if allowed. False if a hop cap refused it."""
        allowed = {t.to for t in self.stage.transitions}
        if target not in allowed:
            return False
        limits = self.assistant.limits
        if self.hops >= limits.max_stage_hops or self.silent_hops >= limits.max_silent_hops:
            logger.warning("handoff %s -> %s refused (hop cap)", self.current, target)
            return False
        self.hops += 1
        self.silent_hops += 1
        logger.info("handoff %s -> %s", self.current, target)
        self.current = target
        self.history.append(target)
        return True

    # ── what the model sees ────────────────────────────────────────────────
    def instructions(self, stage: Optional[Stage] = None) -> str:
        stage = stage or self.stage
        now = datetime.now(ZoneInfo(self.assistant.timezone))
        parts = [DEFAULT_RULES, f"Today is {now.strftime('%A %-d %B %Y')}, {now.strftime('%-I:%M %p')} ({self.assistant.timezone})."]
        if self.config.global_instructions.strip():
            parts.append(self.config.global_instructions.strip())
        if stage.instructions.strip():
            parts.append(stage.instructions.strip())
        if self.assistant.transfer.mode == "static" and self.assistant.transfer.targets:
            lines = [f"- {t.id}: {t.name}. {t.description}".rstrip() for t in self.assistant.transfer.targets]
            parts.append("You can transfer the caller with transfer_call to:\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def tools(self, stage: Optional[Stage] = None) -> list[ToolSpec]:
        stage = stage or self.stage
        out = []
        for name in stage.tools:
            spec = self.assistant.tool(name)
            if spec is not None:
                out.append(spec)
        return out

    def handoffs(self, stage: Optional[Stage] = None) -> list[Handoff]:
        stage = stage or self.stage
        return [
            Handoff(
                name=f"{HANDOFF_PREFIX}{t.to}",
                target=t.to,
                description=(t.when or f"Switch to the {t.to} step.").strip(),
            )
            for t in stage.transitions
        ]

    def builtin_names(self) -> list[str]:
        names = ["end_call"]
        if self.assistant.transfer.mode == "static" and self.assistant.transfer.targets:
            names.append("transfer_call")
        return names

    def openai_tools(self, stage: Optional[Stage] = None) -> list[dict[str, Any]]:
        """Chat-Completions tool definitions for the active stage."""
        defs: list[dict[str, Any]] = [
            _fn(t.name, t.description, t.model_schema()) for t in self.tools(stage)
        ]
        defs += [_fn(h.name, h.description, {"type": "object", "properties": {}}) for h in self.handoffs(stage)]
        defs.append(
            _fn(
                "end_call",
                "End the conversation after you have said goodbye, or when the caller asks to hang up.",
                {"type": "object", "properties": {"reason": {"type": "string"}}},
            )
        )
        if "transfer_call" in self.builtin_names():
            ids = [t.id for t in self.assistant.transfer.targets]
            defs.append(
                _fn(
                    "transfer_call",
                    "Transfer the caller to a human. Only use a listed target.",
                    {
                        "type": "object",
                        "properties": {"target_id": {"type": "string", "enum": ids}},
                        "required": ["target_id"],
                    },
                )
            )
        return defs


def _fn(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}
