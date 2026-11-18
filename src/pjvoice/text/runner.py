"""Text runner: the same stage graph and tools over an OpenAI-compatible chat API.

Used for SMS/chat turns, the ``pjvoice chat`` CLI, and evals (SPEC.md §7).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..engine.config import AssistantConfig
from ..engine.stages import HANDOFF_PREFIX, HOP_REFUSED, StageGraph
from ..engine.tools import CallContext, HttpToolExecutor, ToolCallError

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    async def complete(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Return an assistant message dict: {"content": str|None, "tool_calls": [...]}"""


def _model_id(model: str) -> str:
    """'openai/gpt-4.1-mini' → 'gpt-4.1-mini' unless PJVOICE_KEEP_MODEL_PREFIX is set
    (OpenRouter-style gateways want the prefix)."""
    if os.environ.get("PJVOICE_KEEP_MODEL_PREFIX"):
        return model
    return model.split("/", 1)[1] if "/" in model else model


class OpenAIChatClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            base_url=base_url or os.environ.get("PJVOICE_LLM_BASE_URL") or None,
            api_key=api_key or os.environ.get("PJVOICE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        )

    async def complete(self, model, messages, tools):
        kwargs: dict[str, Any] = {"model": _model_id(model), "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = await self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return {
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
                for tc in (msg.tool_calls or [])
            ],
        }


@dataclass
class TurnResult:
    reply: str
    ended: bool = False
    transfer_to: Optional[str] = None
    stage: str = ""


@dataclass
class Conversation:
    assistant: AssistantConfig
    ctx: CallContext
    graph: StageGraph
    messages: list[dict[str, Any]] = field(default_factory=list)
    ended: bool = False
    transfer_to: Optional[str] = None


class TextRunner:
    def __init__(self, chat: Optional[ChatClient] = None, tools: Optional[HttpToolExecutor] = None) -> None:
        self.chat = chat or OpenAIChatClient()
        self.tools = tools or HttpToolExecutor()

    def start(self, assistant: AssistantConfig, session_id: str, caller_phone: str = "") -> Conversation:
        graph = StageGraph(assistant)
        ctx = CallContext(session_id=session_id, caller_phone=caller_phone, current_stage=graph.current)
        ctx.stage_history = graph.history
        convo = Conversation(assistant=assistant, ctx=ctx, graph=graph)
        if assistant.greeting:
            convo.messages.append({"role": "assistant", "content": assistant.greeting})
        return convo

    async def _complete(self, convo: Conversation, messages, tools) -> dict[str, Any]:
        stage_llm = convo.graph.stage.llm or convo.assistant.models.llm
        try:
            return await self.chat.complete(stage_llm, messages, tools)
        except Exception:
            fallback = convo.assistant.models.fallback_llm
            if not fallback:
                raise
            logger.warning("primary LLM %s failed; using fallback %s", stage_llm, fallback, exc_info=True)
            return await self.chat.complete(fallback, messages, tools)

    async def turn(self, convo: Conversation, user_text: str) -> TurnResult:
        if convo.ended:
            return TurnResult(reply="", ended=True, stage=convo.graph.current)
        convo.messages.append({"role": "user", "content": user_text})
        spoken: list[str] = []
        max_steps = convo.assistant.limits.max_tool_steps

        for _ in range(max_steps + 1):
            system = {"role": "system", "content": convo.graph.instructions()}
            msg = await self._complete(convo, [system, *convo.messages], convo.graph.openai_tools())
            content = (msg.get("content") or "").strip()
            calls = msg.get("tool_calls") or []
            entry: dict[str, Any] = {"role": "assistant", "content": content or None}
            if calls:
                entry["tool_calls"] = [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls
                ]
            convo.messages.append(entry)
            if content:
                spoken.append(content)
                convo.graph.note_speech()
            if not calls:
                break
            for c in calls:
                result = await self._run_tool(convo, c["name"], c["arguments"])
                convo.messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
            if convo.ended or convo.transfer_to:
                break
        else:
            logger.warning("tool step cap reached session=%s", convo.ctx.session_id)
            if not spoken:
                spoken.append("Sorry, I'm having trouble with that. Let me get someone to help you.")

        return TurnResult(
            reply=" ".join(spoken),
            ended=convo.ended,
            transfer_to=convo.transfer_to,
            stage=convo.graph.current,
        )

    async def _run_tool(self, convo: Conversation, name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args) if raw_args else {}
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            return "Error: arguments were not valid JSON. Call the tool again with a JSON object."

        if name.startswith(HANDOFF_PREFIX):
            target = name[len(HANDOFF_PREFIX):]
            if convo.graph.try_handoff(target):
                convo.ctx.current_stage = target
                return f"Now in the {target} step. Continue the conversation; do not announce the switch."
            return HOP_REFUSED
        if name == "end_call":
            convo.ended = True
            convo.ctx.tool_log.append({"tool": "end_call", "arguments": args})
            return "Call ended."
        if name == "transfer_call":
            target = args.get("target_id")
            valid = {t.id for t in convo.assistant.transfer.targets}
            if target not in valid:
                return f"Error: unknown target. Choose one of: {', '.join(sorted(valid))}."
            convo.transfer_to = target
            convo.ctx.tool_log.append({"tool": "transfer_call", "arguments": args})
            return "Transferring now."

        spec = convo.assistant.tool(name)
        if spec is None or spec not in convo.graph.tools():
            return f"Error: the tool {name} is not available in this step."
        try:
            return await self.tools.call(spec, args, convo.ctx)
        except ToolCallError as e:
            return f"Error: {e}"
