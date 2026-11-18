"""LiveKit voice worker (SPEC.md §6).

Run:  pjvoice worker dev   (or: python -m pjvoice.voice.worker dev)

Needs LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET, plus PJVOICE_CONTROL_URL
and PJVOICE_AGENT_KEY to reach the control plane.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterable
from typing import Any, Optional

import httpx
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    ModelSettings,
    RunContext,
    cli,
    function_tool,
    inference,
    llm as lk_llm,
)
from livekit.plugins import silero

from ..engine.config import AssistantConfig, ToolSpec, normalize_e164
from ..engine.guards import RepeatSuppressor
from ..engine.stages import HOP_REFUSED, Handoff, StageGraph
from ..engine.tools import CallContext, HttpToolExecutor, ToolCallError

logger = logging.getLogger("pjvoice.worker")

CONTROL_URL = os.environ.get("PJVOICE_CONTROL_URL", "http://localhost:8000")
AGENT_KEY = os.environ.get("PJVOICE_AGENT_KEY", "")
AGENT_NAME = os.environ.get("PJVOICE_AGENT_NAME", "pjvoice")
INBOUND_ECHO_WARMUP_S = 3.0

_executor = HttpToolExecutor()


class CallState:
    """Session userdata: everything one call needs, shared across stage agents."""

    def __init__(self, job: JobContext, assistant: AssistantConfig, ctx: CallContext, sip_identity: str = "") -> None:
        self.job = job
        self.assistant = assistant
        self.ctx = ctx
        self.graph = StageGraph(assistant)
        self.sip_identity = sip_identity
        self.end_reason = ""
        self.ending = False


# ── config loading ─────────────────────────────────────────────────────────
async def load_assistant(*, phone: str = "", assistant_id: str = "", caller: str = "") -> AssistantConfig:
    headers = {"Authorization": f"Bearer {AGENT_KEY}"} if AGENT_KEY else {}
    async with httpx.AsyncClient(base_url=CONTROL_URL, timeout=5.0, headers=headers) as client:
        if assistant_id:
            resp = await client.get(f"/agent/assistant/{assistant_id}", params={"caller": caller})
        else:
            resp = await client.get("/agent/assistant", params={"phone": phone, "caller": caller})
        resp.raise_for_status()
        return AssistantConfig.model_validate(resp.json())


async def post_session_log(state: CallState, channel: str, transcript: list[dict[str, Any]]) -> None:
    headers = {"Authorization": f"Bearer {AGENT_KEY}"} if AGENT_KEY else {}
    body = {
        "session_id": state.ctx.session_id,
        "assistant_id": state.assistant.id,
        "channel": channel,
        "data": {
            "caller": state.ctx.caller_phone,
            "transcript": transcript,
            "tools": state.ctx.tool_log,
            "stages": state.graph.history,
            "end": state.end_reason or "caller_hangup",
        },
    }
    try:
        async with httpx.AsyncClient(base_url=CONTROL_URL, timeout=10.0, headers=headers) as client:
            (await client.post("/agent/session-logs", json=body)).raise_for_status()
    except httpx.HTTPError:
        logger.exception("failed to post session log")


# ── models ─────────────────────────────────────────────────────────────────
def build_llm(model: str, fallback: Optional[str]) -> lk_llm.LLM:
    primary = inference.LLM(model=model)
    if not fallback or fallback == model:
        return primary
    return lk_llm.FallbackAdapter([primary, inference.LLM(model=fallback)])


def build_tts(assistant: AssistantConfig):
    m = assistant.models
    return inference.TTS(model=m.tts, voice=m.voice) if m.voice else inference.TTS(model=m.tts)


# ── tools ──────────────────────────────────────────────────────────────────
def http_tool(spec: ToolSpec):
    async def handler(raw_arguments: dict[str, object], context: RunContext[CallState]) -> str:
        state = context.userdata
        try:
            return await _executor.call(spec, dict(raw_arguments), state.ctx)
        except ToolCallError as e:
            raise lk_llm.ToolError(str(e))

    return function_tool(
        handler,
        raw_schema={"name": spec.name, "description": spec.description, "parameters": spec.model_schema()},
    )


def handoff_tool(h: Handoff):
    async def handler(raw_arguments: dict[str, object], context: RunContext[CallState]):
        state = context.userdata
        if not state.graph.try_handoff(h.target):
            return HOP_REFUSED
        state.ctx.current_stage = h.target
        # Returning an Agent = silent handoff; chat history carries over.
        return PJAgent(state, h.target, chat_ctx=context.session.current_agent.chat_ctx)

    return function_tool(
        handler, raw_schema={"name": h.name, "description": h.description, "parameters": {"type": "object", "properties": {}}}
    )


@function_tool
async def end_call(context: RunContext[CallState], reason: str = "caller finished") -> str:
    """End the call after you have said goodbye, or when the caller asks to hang up.

    Args:
        reason: Short reason for ending the call.
    """
    state = context.userdata
    if state.ending:
        return "Already ending."
    state.ending = True
    state.end_reason = f"agent_end: {reason}"
    # Let the goodbye finish playing before hanging up.
    await context.wait_for_playout()
    asyncio.create_task(_hangup(state.job))
    return "Call ended."


async def _hangup(job: JobContext) -> None:
    await asyncio.sleep(0.5)
    try:
        await job.api.room.delete_room(api.DeleteRoomRequest(room=job.room.name))
    except Exception:
        logger.exception("delete_room failed")
    job.shutdown(reason="end_call")


def transfer_tool(assistant: AssistantConfig):
    targets = {t.id: t for t in assistant.transfer.targets}

    async def handler(raw_arguments: dict[str, object], context: RunContext[CallState]) -> str:
        state = context.userdata
        target = targets.get(str(raw_arguments.get("target_id", "")))
        if target is None:
            raise lk_llm.ToolError(f"Unknown target. Choose one of: {', '.join(targets)}.")
        if not state.sip_identity:
            raise lk_llm.ToolError("Transfers only work on phone calls. Offer to take a message instead.")
        await context.wait_for_playout()
        state.end_reason = f"transfer: {target.id}"
        state.ctx.tool_log.append({"tool": "transfer_call", "arguments": {"target_id": target.id}})
        await state.job.api.sip.transfer_sip_participant(
            api.TransferSIPParticipantRequest(
                room_name=state.job.room.name,
                participant_identity=state.sip_identity,
                transfer_to=f"tel:{target.phone}",
                play_dialtone=True,
            )
        )
        return f"Transferred to {target.name}."

    return function_tool(
        handler,
        raw_schema={
            "name": "transfer_call",
            "description": "Transfer the caller to a human. Say one short line first, then call this.",
            "parameters": {
                "type": "object",
                "properties": {"target_id": {"type": "string", "enum": list(targets)}},
                "required": ["target_id"],
            },
        },
    )


# ── agent ──────────────────────────────────────────────────────────────────
class PJAgent(Agent):
    """One stage of the assistant. Single-stage assistants use one of these for the whole call."""

    def __init__(self, state: CallState, stage_name: str, *, chat_ctx: Optional[lk_llm.ChatContext] = None, entry: bool = False) -> None:
        graph = state.graph
        stage = graph.config.get(stage_name)
        tools: list[Any] = [http_tool(t) for t in graph.tools(stage)]
        tools += [handoff_tool(h) for h in graph.handoffs(stage)]
        tools.append(end_call)
        if "transfer_call" in graph.builtin_names():
            tools.append(transfer_tool(state.assistant))
        llm_override = None
        if stage.llm:
            llm_override = build_llm(stage.llm, state.assistant.models.fallback_llm)
        kwargs: dict[str, Any] = {"instructions": graph.instructions(stage), "tools": tools, "chat_ctx": chat_ctx}
        if llm_override is not None:
            kwargs["llm"] = llm_override
        super().__init__(**kwargs)
        self._state = state
        self._entry = entry

    async def on_enter(self) -> None:
        # Entry stage stays silent: the entrypoint speaks the greeting.
        if not self._entry:
            self.session.generate_reply()

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        suppressor = RepeatSuppressor()
        state = self._state

        async def filtered() -> AsyncIterable[str]:
            async for chunk in text:
                out = suppressor.push(chunk)
                if out:
                    state.graph.note_speech()
                    yield out
            tail = suppressor.flush()
            if tail:
                yield tail

        async for frame in Agent.default.tts_node(self, filtered(), model_settings):
            yield frame


# ── entrypoint ─────────────────────────────────────────────────────────────
def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(job: JobContext) -> None:
    await job.connect()
    room = job.room.name
    channel = "web" if room.startswith("web-") else "phone"

    participant = await job.wait_for_participant()
    attrs = participant.attributes or {}
    is_sip = participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    caller = normalize_e164(attrs.get("sip.phoneNumber", "")) if is_sip else ""
    called = normalize_e164(attrs.get("sip.trunkPhoneNumber", "")) if is_sip else ""

    # web-<assistant_id>-<random> rooms name their assistant; SIP calls route by dialled number.
    assistant_id = ""
    if channel == "web":
        parts = room.split("-")
        assistant_id = "-".join(parts[1:-1]) if len(parts) > 2 else ""
    try:
        assistant = await load_assistant(phone=called, assistant_id=assistant_id, caller=caller)
    except Exception:
        logger.exception("could not load assistant (room=%s called=%s)", room, called)
        job.shutdown(reason="no_assistant")
        return

    ctx = CallContext(session_id=room, caller_phone=caller, called_phone=called)
    state = CallState(job, assistant, ctx, sip_identity=participant.identity if is_sip else "")
    ctx.current_stage = state.graph.current
    m = assistant.models

    session: AgentSession[CallState] = AgentSession(
        userdata=state,
        vad=job.proc.userdata["vad"],
        stt=inference.STT(model=m.stt, language=m.language),
        llm=build_llm(m.llm, m.fallback_llm),
        tts=build_tts(assistant),
        max_tool_steps=assistant.limits.max_tool_steps,
        user_away_timeout=assistant.limits.user_away_timeout_s,
        # No echo canceller on the SIP leg: ignore barge-in while the greeting plays.
        aec_warmup_duration=INBOUND_ECHO_WARMUP_S if is_sip else None,
    )

    away_prompts = {"n": 0}

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        if ev.new_state == "away" and not state.ending:
            away_prompts["n"] += 1
            if away_prompts["n"] > assistant.limits.away_prompts:
                state.end_reason = "inactivity"
                session.generate_reply(instructions="Say a brief goodbye; the caller seems to have gone. Then call end_call.")
            else:
                session.generate_reply(instructions="The caller has been silent. Briefly ask if they are still there.")
        elif ev.new_state == "speaking":
            away_prompts["n"] = 0

    async def _on_shutdown() -> None:
        transcript = [
            {"role": item.role, "text": item.text_content}
            for item in session.history.items
            if getattr(item, "type", "") == "message" and item.text_content
        ]
        await post_session_log(state, channel, transcript)

    job.add_shutdown_callback(_on_shutdown)

    await session.start(agent=PJAgent(state, state.graph.current, entry=True), room=job.room)
    if assistant.greeting:
        await session.say(assistant.greeting)


def main() -> None:
    cli.run_app(server)


if __name__ == "__main__":
    main()
