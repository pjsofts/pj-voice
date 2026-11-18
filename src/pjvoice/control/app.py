"""Control plane API (SPEC.md §8).

Owns routing, storage and reliability. It never writes prompts: tenants own
their assistant config, optionally serving it live from a config webhook.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ValidationError

from ..engine.config import AssistantConfig, normalize_e164
from ..text.runner import Conversation, TextRunner
from .store import Store

logger = logging.getLogger(__name__)


class ConfigResolver:
    """Resolve the config to run: live tenant config if reachable, else last known good."""

    def __init__(self, store: Store, client: Optional[httpx.AsyncClient] = None) -> None:
        self.store = store
        self.client = client or httpx.AsyncClient()

    async def resolve(self, assistant_id: str, phone: str = "", caller: str = "") -> Optional[AssistantConfig]:
        found = self.store.get_assistant(assistant_id)
        if found is None:
            return None
        base, last_good = found
        hook = base.config_webhook
        if hook is None:
            return base
        try:
            resp = await self.client.get(
                hook.url,
                params={"assistant_id": assistant_id, "phone": phone, "caller": caller},
                headers=hook.headers,
                timeout=hook.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            # The tenant can't re-route numbers or point at another assistant.
            data.update({"id": base.id, "phone_numbers": base.phone_numbers, "config_webhook": None})
            live = AssistantConfig.model_validate(data)
            self.store.save_live_config(assistant_id, live)
            return live
        except (httpx.HTTPError, ValueError, ValidationError) as e:
            logger.warning("config webhook failed for %s (%s); serving fallback", assistant_id, e)
            return last_good or base.model_copy(update={"config_webhook": None})


class TurnIn(BaseModel):
    session_id: str
    text: str
    caller_phone: str = ""


class SessionLogIn(BaseModel):
    session_id: str
    assistant_id: Optional[str] = None
    channel: str = "voice"
    data: dict[str, Any]


def create_app(
    store: Optional[Store] = None,
    runner: Optional[TextRunner] = None,
    resolver: Optional[ConfigResolver] = None,
    api_key: Optional[str] = None,
    agent_key: Optional[str] = None,
) -> FastAPI:
    store = store or Store(os.environ.get("PJVOICE_DB", "pjvoice.db"))
    resolver = resolver or ConfigResolver(store)
    api_key = api_key if api_key is not None else os.environ.get("PJVOICE_API_KEY", "")
    agent_key = agent_key if agent_key is not None else os.environ.get("PJVOICE_AGENT_KEY", "")
    conversations: dict[tuple[str, str], tuple[Conversation, float]] = {}
    convo_locks: dict[tuple[str, str], asyncio.Lock] = {}
    convo_ttl = 6 * 3600
    state: dict[str, Any] = {"runner": runner}

    def get_runner() -> TextRunner:
        if state["runner"] is None:
            state["runner"] = TextRunner()
        return state["runner"]

    def check(key: str, authorization: str) -> None:
        if key and authorization != f"Bearer {key}":
            raise HTTPException(status_code=401, detail="unauthorized")

    def api_auth(authorization: str = Header(default="")) -> None:
        check(api_key, authorization)

    def agent_auth(authorization: str = Header(default="")) -> None:
        check(agent_key, authorization)

    app = FastAPI(title="PJ Voice control plane")
    app.state.store = store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ── management API ────────────────────────────────────────────────────
    @app.put("/v1/assistants/{assistant_id}", dependencies=[Depends(api_auth)])
    def put_assistant(assistant_id: str, cfg: AssistantConfig) -> AssistantConfig:
        if cfg.id != assistant_id:
            raise HTTPException(status_code=400, detail="id in body must match the URL")
        try:
            store.put_assistant(cfg)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return cfg

    @app.get("/v1/assistants", dependencies=[Depends(api_auth)])
    def list_assistants() -> list[AssistantConfig]:
        return store.list_assistants()

    @app.get("/v1/assistants/{assistant_id}", dependencies=[Depends(api_auth)])
    def get_assistant(assistant_id: str) -> AssistantConfig:
        found = store.get_assistant(assistant_id)
        if not found:
            raise HTTPException(status_code=404, detail="assistant not found")
        return found[0]

    @app.delete("/v1/assistants/{assistant_id}", dependencies=[Depends(api_auth)])
    def delete_assistant(assistant_id: str) -> dict[str, bool]:
        if not store.delete_assistant(assistant_id):
            raise HTTPException(status_code=404, detail="assistant not found")
        return {"deleted": True}

    @app.post("/v1/assistants/{assistant_id}/messages", dependencies=[Depends(api_auth)])
    async def text_turn(assistant_id: str, body: TurnIn) -> dict[str, Any]:
        key = (assistant_id, body.session_id)
        lock = convo_locks.setdefault(key, asyncio.Lock())
        async with lock:  # one turn at a time per conversation
            now = time.time()
            for k, (_, seen) in list(conversations.items()):
                if now - seen > convo_ttl:
                    conversations.pop(k, None)
                    convo_locks.pop(k, None)
            entry = conversations.get(key)
            if entry is None:
                cfg = await resolver.resolve(assistant_id, caller=body.caller_phone)
                if cfg is None:
                    raise HTTPException(status_code=404, detail="assistant not found")
                convo = get_runner().start(cfg, body.session_id, normalize_e164(body.caller_phone))
            else:
                convo = entry[0]
            result = await get_runner().turn(convo, body.text)
            conversations[key] = (convo, now)
            if result.ended or result.transfer_to:
                conversations.pop(key, None)
                store.add_session_log(
                    body.session_id, assistant_id, "text",
                    {"messages": convo.messages, "tools": convo.ctx.tool_log,
                     "stages": convo.graph.history, "end": "transfer" if result.transfer_to else "ended"},
                )
        return {"reply": result.reply, "ended": result.ended, "transfer_to": result.transfer_to, "stage": result.stage}

    @app.get("/v1/session-logs", dependencies=[Depends(api_auth)])
    def session_logs(assistant_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        return store.list_session_logs(assistant_id, min(max(limit, 1), 500))

    # ── worker-facing API ─────────────────────────────────────────────────
    @app.get("/agent/assistant", dependencies=[Depends(agent_auth)])
    async def assistant_by_phone(phone: str, caller: str = "") -> AssistantConfig:
        assistant_id = store.assistant_for_phone(phone)
        if not assistant_id:
            raise HTTPException(status_code=404, detail="no assistant for this number")
        cfg = await resolver.resolve(assistant_id, phone=normalize_e164(phone), caller=normalize_e164(caller))
        if cfg is None:
            raise HTTPException(status_code=404, detail="assistant not found")
        return cfg

    @app.get("/agent/assistant/{assistant_id}", dependencies=[Depends(agent_auth)])
    async def assistant_by_id(assistant_id: str, caller: str = "") -> AssistantConfig:
        cfg = await resolver.resolve(assistant_id, caller=normalize_e164(caller))
        if cfg is None:
            raise HTTPException(status_code=404, detail="assistant not found")
        return cfg

    @app.post("/agent/session-logs", status_code=201, dependencies=[Depends(agent_auth)])
    def post_session_log(body: SessionLogIn) -> dict[str, int]:
        return {"id": store.add_session_log(body.session_id, body.assistant_id, body.channel, body.data)}

    return app
