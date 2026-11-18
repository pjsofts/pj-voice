"""HTTP tool client: the agent side of the tool protocol (SPEC.md §4)."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from .config import ToolSpec
from .guards import NoProgressTracker

logger = logging.getLogger(__name__)


class ToolCallError(Exception):
    """A tool failed in a way the model should see and recover from."""


@dataclass
class CallContext:
    """Per-conversation state shared by every tool call in one session."""

    session_id: str
    caller_phone: str = ""
    called_phone: str = ""
    current_stage: str = ""
    stage_history: list[str] = field(default_factory=list)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    no_progress: NoProgressTracker = field(default_factory=NoProgressTracker)

    def injected_value(self, source: str) -> Optional[str]:
        return {
            "caller_phone": self.caller_phone,
            "called_phone": self.called_phone,
            "session_id": self.session_id,
            "current_stage": self.current_stage,
        }.get(source) or None


def new_tool_call_id(name: str) -> str:
    return f"call_{name}_{int(time.time() * 1_000_000)}_{random.randint(1000, 9999)}"


def parse_tool_response(data: Any, name: str) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("results"), list) or not data["results"]:
        raise ToolCallError(f"Tool '{name}' returned an invalid response.")
    first = data["results"][0]
    if not isinstance(first, dict):
        raise ToolCallError(f"Tool '{name}' returned an invalid result.")
    if first.get("error"):
        raise ToolCallError(str(first["error"]))
    result = first.get("result")
    if result is None:
        raise ToolCallError(f"Tool '{name}' returned no result.")
    return result if isinstance(result, str) else json.dumps(result)


class HttpToolExecutor:
    """Executes ToolSpecs over HTTP. One instance per worker process is fine."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def call(self, spec: ToolSpec, model_args: dict[str, Any], ctx: CallContext) -> str:
        """Run one tool call. Returns text for the model; raises ToolCallError."""
        args = {k: v for k, v in (model_args or {}).items() if v is not None}
        injected_names: list[str] = []
        for param, source in spec.injected().items():
            value = ctx.injected_value(source)
            # Runtime value wins over anything the model emitted for this field.
            args.pop(param, None)
            if value is not None:
                args[param] = value
                injected_names.append(param)

        tool_call_id = new_tool_call_id(spec.name)
        body: dict[str, Any] = {
            "type": "tool_call",
            "tool_call_id": tool_call_id,
            "session_id": ctx.session_id,
            "name": spec.name,
            "arguments": args,
        }
        if injected_names:
            body["injected"] = sorted(injected_names)
        headers = {
            **spec.resolved_headers(),
            "Content-Type": "application/json",
            "X-Call-Id": ctx.session_id,
            "X-Tool-Call-Id": tool_call_id,
            "X-Tool-Name": spec.name,
        }

        started = time.monotonic()
        entry: dict[str, Any] = {"tool": spec.name, "arguments": args}
        try:
            resp = await self.client.request(
                spec.method.upper(), spec.resolved_url(), json=body, headers=headers, timeout=spec.timeout_s
            )
            resp.raise_for_status()
            result = parse_tool_response(resp.json(), spec.name)
        except httpx.TimeoutException:
            entry["error"] = "timeout"
            raise ToolCallError(
                f"The {spec.name} system did not answer in time. Apologise briefly and try once more, "
                "or offer to have someone call back."
            )
        except httpx.HTTPError as e:
            entry["error"] = f"http: {e}"
            raise ToolCallError(f"The {spec.name} system is unavailable right now.")
        except ToolCallError as e:
            entry["error"] = str(e)
            raise
        except ValueError:
            entry["error"] = "invalid json"
            raise ToolCallError(f"Tool '{spec.name}' returned an invalid response.")
        finally:
            entry["ms"] = round((time.monotonic() - started) * 1000)
            ctx.tool_log.append(entry)

        result = ctx.no_progress.annotate(spec.name, args, result)
        entry["result"] = result[:500]
        logger.info("tool %s ok in %sms session=%s", spec.name, entry["ms"], ctx.session_id)
        return result
