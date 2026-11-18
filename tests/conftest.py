from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from pjvoice.booking.app import create_app as create_tools_app
from pjvoice.booking.backend import demo_backend
from pjvoice.engine.config import AssistantConfig

ROOT = Path(__file__).resolve().parents[1]
# A fixed "now": Monday 16 Nov 2026, 08:00 New York.
NOW = datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc)


class ScriptedChat:
    """Fake LLM: returns queued messages in order and records what it was sent."""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[dict] = []

    async def complete(self, model, messages, tools):
        self.calls.append({"model": model, "messages": messages, "tools": [t["function"]["name"] for t in tools]})
        if not self.script:
            return {"content": "Is there anything else?", "tool_calls": []}
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def say(text: str) -> dict:
    return {"content": text, "tool_calls": []}


_n = {"i": 0}


def call(_tool: str, **args) -> dict:
    _n["i"] += 1
    return {"content": None, "tool_calls": [{"id": f"tc{_n['i']}", "name": _tool, "arguments": json.dumps(args)}]}


@pytest.fixture
def backend():
    return demo_backend("America/New_York", now=lambda: NOW)


@pytest.fixture
async def tools_client(backend):
    app = create_tools_app(backend, api_key="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tools") as c:
        yield c


@pytest.fixture
def salon() -> AssistantConfig:
    cfg = AssistantConfig.from_file(ROOT / "examples" / "demo-salon.yaml")
    return cfg.model_copy(
        update={"tools": [t.model_copy(update={"url": "http://tools/tools", "headers": {}}) for t in cfg.tools]}
    )
