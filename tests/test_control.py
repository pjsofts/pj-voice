import httpx
import pytest

from pjvoice.control.app import ConfigResolver, create_app
from pjvoice.control.store import Store
from pjvoice.engine.config import AssistantConfig, ConfigWebhook
from pjvoice.text.runner import TextRunner

from conftest import ScriptedChat, say


def make(store, runner=None, resolver=None):
    app = create_app(store=store, runner=runner, resolver=resolver, api_key="k", agent_key="ak")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cp")


AUTH = {"Authorization": "Bearer k"}
AGENT = {"Authorization": "Bearer ak"}


async def test_crud_routing_and_auth(salon):
    store = Store(":memory:")
    async with make(store) as c:
        body = salon.model_dump(mode="json")
        assert (await c.put("/v1/assistants/demo-salon", json=body)).status_code == 401
        assert (await c.put("/v1/assistants/demo-salon", json=body, headers=AUTH)).status_code == 200
        r = await c.get("/agent/assistant", params={"phone": "+1 555 0100"}, headers=AGENT)
        assert r.status_code == 200 and r.json()["id"] == "demo-salon"
        assert (await c.get("/agent/assistant", params={"phone": "+19999"}, headers=AGENT)).status_code == 404
        other = {**body, "id": "other"}
        assert (await c.put("/v1/assistants/other", json=other, headers=AUTH)).status_code == 409  # number taken
        assert (await c.delete("/v1/assistants/demo-salon", headers=AUTH)).status_code == 200
        assert (await c.get("/agent/assistant", params={"phone": "+15550100"}, headers=AGENT)).status_code == 404


async def test_tenant_webhook_live_then_fallback(salon):
    store = Store(":memory:")
    hooked = salon.model_copy(update={"config_webhook": ConfigWebhook(url="http://tenant/config")})
    store.put_assistant(hooked)
    state = {"up": True}

    def tenant(request: httpx.Request) -> httpx.Response:
        if not state["up"]:
            return httpx.Response(503)
        live = salon.model_dump(mode="json")
        live.update({"greeting": "Live greeting", "id": "hijack", "phone_numbers": ["+1999"]})
        return httpx.Response(200, json=live)

    resolver = ConfigResolver(store, httpx.AsyncClient(transport=httpx.MockTransport(tenant)))
    live = await resolver.resolve("demo-salon")
    assert live.greeting == "Live greeting"
    assert live.id == "demo-salon" and live.phone_numbers == ["+15550100"]  # tenant can't re-route
    state["up"] = False
    fallback = await resolver.resolve("demo-salon")
    assert fallback.greeting == "Live greeting"  # last known good, not an error


async def test_text_turn_endpoint_keeps_conversation(salon):
    store = Store(":memory:")
    store.put_assistant(salon)
    chat = ScriptedChat([say("Hi, how can I help?"), say("Sure.")])
    async with make(store, runner=TextRunner(chat=chat)) as c:
        r = await c.post("/v1/assistants/demo-salon/messages", json={"session_id": "t1", "text": "hi"}, headers=AUTH)
        assert r.json()["reply"] == "Hi, how can I help?"
        await c.post("/v1/assistants/demo-salon/messages", json={"session_id": "t1", "text": "ok"}, headers=AUTH)
    roles = [m["role"] for m in chat.calls[-1]["messages"]]
    assert roles == ["system", "assistant", "user", "assistant", "user"]
