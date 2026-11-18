import pytest
from pydantic import ValidationError

from pjvoice.engine.config import AssistantConfig, ToolSpec, expand_env, normalize_e164
from pjvoice.engine.stages import StageGraph


def test_injected_params_hidden_from_model():
    t = ToolSpec(
        name="book", description="d", url="http://x",
        parameters={"type": "object", "properties": {
            "slot_id": {"type": "string"},
            "customer_id": {"type": "string", "x-injected": True},
            "phone": {"type": "string", "x-injected": "caller_phone"},
        }, "required": ["slot_id", "customer_id"]},
    )
    assert t.injected() == {"customer_id": "server", "phone": "caller_phone"}
    assert t.model_schema() == {"type": "object", "properties": {"slot_id": {"type": "string"}}, "required": ["slot_id"]}


def test_env_expansion(monkeypatch):
    monkeypatch.delenv("PJV_X", raising=False)
    assert expand_env("${PJV_X:-http://d}/t") == "http://d/t"
    monkeypatch.setenv("PJV_X", "http://e")
    assert expand_env("${PJV_X:-http://d}/t") == "http://e/t"


def test_bad_transition_rejected():
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({
            "id": "a", "name": "a",
            "flow": {"initial": "x", "stages": [{"name": "x", "transitions": [{"to": "nope"}]}]},
        })


def test_phone_normalization():
    assert normalize_e164("sip:+1 (555) 010-0@example") == "+15550100"
    assert normalize_e164("") == ""


def test_single_stage_default_and_hop_caps():
    a = AssistantConfig.model_validate({
        "id": "a", "name": "a", "limits": {"max_silent_hops": 2, "max_stage_hops": 3},
        "flow": {"initial": "x", "stages": [
            {"name": "x", "transitions": [{"to": "y"}]},
            {"name": "y", "transitions": [{"to": "x"}]},
        ]},
    })
    g = StageGraph(a)
    assert g.try_handoff("y") and g.try_handoff("x")
    assert not g.try_handoff("y")  # third silent hop refused
    assert g.current == "x"
    g.note_speech()
    assert g.try_handoff("y")
    assert not g.try_handoff("x")  # total cap (4th hop) refused
    assert not g.try_handoff("nowhere")

    single = StageGraph(AssistantConfig.model_validate({"id": "b", "name": "b", "instructions": "hi"}))
    assert single.current == "main" and "hi" in single.instructions()
