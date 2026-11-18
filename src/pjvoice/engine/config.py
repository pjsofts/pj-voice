"""Assistant configuration models (see SPEC.md §3)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

INJECTED_KEY = "x-injected"
_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def expand_env(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} at call time, so secrets stay out of stored configs."""
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), value)


def normalize_e164(phone: str) -> str:
    """'(555) 010-0' / 'tel:+1555…' / 'sip:+1555…@host' → '+1555…'."""
    phone = (phone or "").split("@")[0]
    digits = re.sub(r"\D", "", phone)
    return f"+{digits}" if digits else ""


class Models(BaseModel):
    llm: str = "openai/gpt-4.1-mini"
    fallback_llm: Optional[str] = None
    stt: str = "deepgram/nova-3"
    tts: str = "cartesia/sonic-2"
    voice: Optional[str] = None
    language: str = "en"


class Limits(BaseModel):
    max_tool_steps: int = 6
    max_stage_hops: int = 12
    max_silent_hops: int = 3
    user_away_timeout_s: float = 12.0
    away_prompts: int = 2


class ToolSpec(BaseModel):
    """An HTTP tool following the PJ Voice tool protocol (SPEC.md §4)."""

    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: str
    url: str
    method: str = "POST"
    timeout_s: float = 10.0
    headers: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    def resolved_headers(self) -> dict[str, str]:
        """Header values may reference env vars as ${VAR} or ${VAR:-default}."""
        return {k: expand_env(v) for k, v in self.headers.items()}

    def resolved_url(self) -> str:
        return expand_env(self.url)

    def injected(self) -> dict[str, str]:
        """{param_name: source} for parameters the runtime fills, not the model."""
        out: dict[str, str] = {}
        for name, prop in (self.parameters.get("properties") or {}).items():
            src = prop.get(INJECTED_KEY) if isinstance(prop, dict) else None
            if src is True:
                src = "server"
            if src:
                out[name] = str(src)
        return out

    def model_schema(self) -> dict[str, Any]:
        """JSON Schema shown to the model: injected params removed entirely."""
        hidden = set(self.injected())
        props = {
            k: {kk: vv for kk, vv in v.items() if kk != INJECTED_KEY}
            for k, v in (self.parameters.get("properties") or {}).items()
            if k not in hidden
        }
        required = [r for r in self.parameters.get("required", []) if r not in hidden]
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema


class Transition(BaseModel):
    to: str
    when: str = ""


class Stage(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    instructions: str = ""
    tools: list[str] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    llm: Optional[str] = None


class StageGraphConfig(BaseModel):
    initial: str
    global_instructions: str = ""
    stages: list[Stage] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_refs(self) -> "StageGraphConfig":
        names = {s.name for s in self.stages}
        if len(names) != len(self.stages):
            raise ValueError("stage names must be unique")
        if self.initial not in names:
            raise ValueError(f"initial stage {self.initial!r} is not defined")
        for s in self.stages:
            for t in s.transitions:
                if t.to not in names:
                    raise ValueError(f"stage {s.name!r} transitions to unknown {t.to!r}")
        return self

    def get(self, name: str) -> Stage:
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(name)


class TransferTarget(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    phone: str = Field(pattern=r"^\+\d{6,15}$")
    description: str = ""


class TransferConfig(BaseModel):
    mode: Literal["none", "static"] = "none"
    targets: list[TransferTarget] = Field(default_factory=list)


class ConfigWebhook(BaseModel):
    url: str
    timeout_s: float = 3.0
    headers: dict[str, str] = Field(default_factory=dict)


class AssistantConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str
    phone_numbers: list[str] = Field(default_factory=list)
    greeting: Optional[str] = None
    instructions: str = ""
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)
    flow: Optional[StageGraphConfig] = None
    tools: list[ToolSpec] = Field(default_factory=list)
    transfer: TransferConfig = Field(default_factory=TransferConfig)
    config_webhook: Optional[ConfigWebhook] = None
    timezone: str = "UTC"

    @model_validator(mode="after")
    def _check_tools(self) -> "AssistantConfig":
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("tool names must be unique")
        return self

    def tool(self, name: str) -> Optional[ToolSpec]:
        return next((t for t in self.tools if t.name == name), None)

    @classmethod
    def from_file(cls, path: str | Path) -> "AssistantConfig":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)
