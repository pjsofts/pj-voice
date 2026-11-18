# PJ Voice — Specification

PJ Voice is an open source, self-hostable AI phone agent that **does real work
in a business system**, not just takes messages. A caller (or texter) talks to an
agent that can look up their account, check availability, and book, move or
cancel appointments through tools the business controls, safely.

Status: v0.1 (spec + reference implementation). License: MIT.

---

## 1. Goals and non-goals

### Goals
1. **Real actions, safely.** The agent calls business tools over a small HTTP
   protocol. Correctness (no double bookings, no invented prices, no wrong
   entity) is enforced server-side, not in the prompt.
2. **Voice-grade latency.** Streaming STT → LLM → TTS on LiveKit; callers hang
   up after ~1 s of dead air, so every design choice is checked against that.
3. **One brain, many channels.** The same assistant config and tools drive phone
   calls, web calls, and text (SMS / chat / evals).
4. **Multi-tenant control plane.** Many assistants, routed by phone number;
   each tenant can serve its own live config from a webhook.
5. **Never drop a call because a dependency failed.** Tenant webhook down → last
   known good config. Primary LLM down → fallback model.
6. **Testable without a phone.** A text runner and a simulated-caller eval
   harness exercise the same stage graph and tools in CI.

### Non-goals (v0.1)
- A hosted SaaS, billing, or a full admin dashboard (a minimal API only).
- Owning telephony: SIP trunks are provisioned in LiveKit / the carrier.
- Vendor-specific booking integrations. The example tool server ships an
  in-memory/SQLite backend; real systems plug in behind the same interface.

---

## 2. Architecture

```
 Caller ──PSTN──▶ SIP trunk ──▶ LiveKit room ◀── Web caller (browser)
                                     │
                             ┌───────▼────────┐   GET /agent/assistant?phone=…   ┌──────────────────┐
                             │  VOICE WORKER  │ ───────────────────────────────▶ │  CONTROL PLANE   │
                             │ (LiveKit Agent)│ ◀──────── AssistantConfig ────── │    (FastAPI)     │
                             │ STT → LLM → TTS│                                  │ routing · store  │
                             └───────┬────────┘                                  │ tenant loader    │
       SMS / chat / evals ──▶ TEXT RUNNER (same engine)  ◀───────────────────────│ text endpoint    │
                                     │ tool calls: POST {type:"tool_call",…}     └────────┬─────────┘
                                     ▼                                                    │ fetch live config
                             ┌────────────────────────────────────────┐                   ▼ (fallback: stored copy)
                             │ TOOL SERVER (business-owned)           │◀──────── tenant config webhook
                             │ correctness layer · per-session drafts │
                             │ booking backend (pluggable)            │
                             └────────────────────────────────────────┘
```

| Component | Responsibility | Tech |
|---|---|---|
| `pjvoice.engine` | Framework-agnostic core: config models, stage graph, HTTP tool client, injected args, guards | Python, Pydantic |
| `pjvoice.voice` | LiveKit worker: session build, stage agents, greeting, end-call / transfer | livekit-agents 1.x |
| `pjvoice.text` | Text runner over any OpenAI-compatible chat API, same stage graph + tools | openai SDK |
| `pjvoice.control` | Control plane: assistants, phone routing, tenant config loader with fallback, text turn endpoint | FastAPI, SQLite |
| `pjvoice.booking` | Reference tool server: generic appointment booking with a correctness layer | FastAPI |
| `pjvoice.evals` | Simulated-caller harness with weighted validators | text runner + LLM |

**Design rule:** the control plane never writes prompts. Tenants own their
brain (instructions, stages, tools); the platform owns routing, runtime and
reliability.

---

## 3. Assistant configuration

One YAML/JSON document per assistant (stored by the control plane, or served
live by a tenant webhook).

```yaml
id: demo-salon
name: Demo Salon receptionist
phone_numbers: ["+15550100"]
greeting: "Thanks for calling Demo Salon, this is the assistant. How can I help?"
models:
  llm: openai/gpt-4.1-mini
  fallback_llm: openai/gpt-4o-mini
  stt: deepgram/nova-3
  tts: cartesia/sonic-2
  voice: <voice id>
limits:
  max_tool_steps: 6
  max_stage_hops: 12
  max_silent_hops: 3
flow:
  initial: triage
  global_instructions: |
    You are a phone receptionist. Keep replies short (one or two sentences).
    Never state a price, time or name that a tool did not give you.
  stages:
    - name: triage
      instructions: Find out what the caller needs.
      tools: [find_customer]
      transitions:
        - to: booking
          when: The caller wants a new appointment.
        - to: manage
          when: The caller wants to move or cancel an existing appointment.
    - name: booking
      instructions: Book exactly one appointment. Confirm service, day and time before booking.
      tools: [list_services, check_availability, book_appointment]
      transitions: [{to: triage, when: The caller needs something else.}]
    - name: manage
      tools: [list_appointments, reschedule_appointment, cancel_appointment]
      transitions: [{to: triage, when: The caller needs something else.}]
tools:
  - name: check_availability
    description: Open slots for a service on a date.
    url: http://localhost:8100/tools
    timeout_s: 8
    parameters:            # JSON Schema
      type: object
      properties:
        service_id: {type: string}
        date: {type: string, description: "YYYY-MM-DD"}
        caller_phone: {type: string, x-injected: caller_phone}
      required: [service_id, date]
transfer:
  mode: static           # none | static
  targets: [{id: front_desk, name: Front desk, phone: "+15550199", description: A human at the front desk}]
```

### 3.1 Single-agent vs stage graph
If `flow` is omitted, one agent runs with `instructions` and all `tools`.
With `flow`, each stage is an agent with its own instructions and a **tool
subset**; transitions become auto-generated `to_<stage>` tools.

Stage rules:
- Handoffs are **silent** (no spoken "transferring you"); the new stage
  produces the next turn from the carried-over chat context.
- **Hop caps:** total hops per call (`max_stage_hops`) and consecutive hops with
  no speech in between (`max_silent_hops`). Over a cap the handoff is refused
  and the model is told to keep helping in the current stage. This stops A→B→A
  ping-pong.
- Built-in tools (`end_call`, `transfer_call`) are available in every stage.
- Unknown tool names in a stage are logged and skipped, never fatal.

---

## 4. Tool protocol (agent → tool server)

`POST <tool.url>`, JSON:

```json
{
  "type": "tool_call",
  "tool_call_id": "call_book_appointment_1731912345_4821",
  "session_id": "room-or-conversation-id",
  "name": "book_appointment",
  "arguments": {"slot_id": "…", "customer_id": "…"},
  "injected": ["customer_id"]
}
```

Headers: `X-Call-Id`, `X-Tool-Call-Id`, `X-Tool-Name`, plus `traceparent` when tracing is on.

Response:

```json
{"results": [{"toolCallId": "…", "result": "Booked Tue 14 Jan 10:00 with Sam. Ref A7K2."}]}
```
or `{"results": [{"toolCallId": "…", "error": "That slot was just taken. Offer: 10:30, 11:00."}]}`

Rules:
- `result` is text written **for the LLM** (or JSON, which is stringified).
- `error` is surfaced to the model as a tool error. It must be **actionable**
  (what to do next), never a stack trace.
- `session_id` is constant for the whole conversation so the tool server can
  keep per-call drafts (e.g. "customer identified on this call").
- Timeouts are per tool (`timeout_s`, default 10 s). Writes are never retried
  by the runtime; reads may be retried by the tool server.

### 4.1 Injected arguments
A parameter marked `x-injected: <source>` is **removed from the schema the model
sees** and filled by the runtime:

| Source | Value |
|---|---|
| `caller_phone` | Caller ID (E.164) |
| `called_phone` | The dialled number |
| `session_id` | Conversation id |
| `current_stage` | Active stage name |
| `server` / `true` | Not filled by runtime; the tool server resolves it from `session_id` |

Why: the most damaging LLM error is *entity binding*, using a real, valid ID
that belongs to the wrong customer or record. A parameter the model never emits
is one it can't mis-bind. If nothing fills it, the call still goes out and
the tool server answers with an actionable error (fail forward, not closed).

### 4.2 No-progress note
If the model repeats the **same tool with identical arguments** and gets an
**identical result**, the runtime appends a short note telling it the call won't
change and to act on the answer. The call is never blocked.

---

## 5. Correctness layer (tool server)

The reference booking server shows the patterns a business tool server should use:

1. **Server-side identity.** `find_customer` binds the customer to the
   `session_id`; later tools read it from there (`x-injected: server`).
2. **Offered-slot guard.** `book_appointment` only accepts slots this session was
   actually offered by `check_availability`. This stops the model from
   inventing a time.
3. **Re-check before write.** The slot is re-validated against the backend at
   booking time; on conflict, the error lists fresh alternatives.
4. **Idempotency.** A repeat booking of the same slot for the same session
   returns the existing booking instead of creating a duplicate.
5. **Prices from data only.** Prices/durations come from the service catalogue;
   results phrase them explicitly so the model can repeat, not compute.
6. **Timezones.** All storage in UTC; results rendered in the business's local
   timezone with weekday names.
7. **LLM-shaped results.** Short, speakable sentences; IDs included only where
   the next tool needs them.

Backend interface (`BookingBackend`): `find_customer`, `create_customer`,
`list_services`, `open_slots`, `book`, `list_appointments`, `reschedule`,
`cancel`. v0.1 ships `MemoryBackend`; Cal.com / Google Calendar adapters are
roadmap items.

---

## 6. Voice runtime

- **Session:** LiveKit `AgentSession` with Silero VAD, multilingual turn
  detector, STT/LLM/TTS via LiveKit Inference model strings (bring-your-own
  plugin keys optional).
- **Room naming:** `call-…` inbound SIP, `web-…` browser, `outbound-…` outbound.
- **Config load:** by called number (SIP attribute) or by assistant id in the
  room name/metadata; loaded from the control plane.
- **Greeting:** spoken with `session.say` once the caller is connected; entry
  stage stays silent to avoid double-speak.
- **Echo warm-up:** interruptions disabled for the first ~3 s of inbound calls
  so the greeting's echo on the SIP leg isn't heard as a barge-in.
- **Fallback LLM:** primary wrapped in `FallbackAdapter` with a known-good model.
- **Repeat suppressor:** a streaming filter drops a verbatim restart of a
  complete reply (some models emit the whole reply twice).
- **Inactivity:** after `user_away_timeout`, prompt the caller up to N times,
  then end the call politely.
- **Built-ins:** `end_call(reason)` (waits for the goodbye to finish playing),
  `transfer_call(target_id)` (SIP REFER to a configured target only; the model
  can't dial arbitrary numbers).
- **Session log:** on shutdown, POST transcript, tool calls, stage history and
  end reason to the control plane.

## 7. Text runner

Same `StageGraph`, tools, injected args and guards, driven by an OpenAI-compatible
Chat Completions loop (`base_url` configurable: OpenAI, OpenRouter, vLLM,
Ollama…). Used for: `POST /v1/assistants/{id}/messages` (SMS/chat), the CLI
`pjvoice chat`, and evals. Conversation state is keyed by `session_id`.

## 8. Control plane API (v0.1)

| Method | Path | Purpose |
|---|---|---|
| `PUT` | `/v1/assistants/{id}` | Create or replace an assistant config |
| `GET` | `/v1/assistants/{id}` | Read config |
| `GET` | `/v1/assistants` | List |
| `GET` | `/agent/assistant?phone=+1…` | Worker lookup by dialled number (resolves live config) |
| `GET` | `/agent/assistant/{id}` | Worker lookup by id |
| `POST` | `/v1/assistants/{id}/messages` | One text turn `{session_id, text, caller_phone?}` → `{reply}` |
| `POST` | `/agent/session-logs` | Worker posts the end-of-call log |
| `GET` | `/v1/session-logs` | List logs |

Auth: `Authorization: Bearer <PJVOICE_API_KEY>` for `/v1/*`, a separate
`PJVOICE_AGENT_KEY` for `/agent/*`.

**Tenant config webhook.** If an assistant has `config_webhook.url`, lookups GET
it (`?assistant_id=…&phone=…&caller=…`, 3 s timeout). A valid response is used
**and stored as last-known-good**; any failure (timeout, non-2xx, invalid
config) serves the stored copy. A tenant outage never drops a call.

## 9. Evals

Scenario YAML: a persona for the simulated caller, a goal, max turns, and
weighted validators:

```yaml
name: new customer books a haircut
persona: You are Dana, phone +15550142, new customer. You want a women's haircut next Tuesday morning.
max_turns: 14
validators:
  - {type: tool_called, tool: book_appointment, weight: 3}
  - {type: tool_not_called, tool: cancel_appointment, weight: 1}
  - {type: backend_state, check: appointment_count, equals: 1, weight: 3}
  - {type: transcript_excludes, pattern: "\\$\\d+(?!\\.)", weight: 1}
```
Score = Σ weight of passed validators / Σ weights; a scenario passes at ≥ 0.8.
The runner drives agent ↔ simulated caller through the text runner against an
in-process tool server with a seeded `MemoryBackend`, so runs are reproducible.

## 10. Observability
Structured logs with `session_id` on every line; OpenTelemetry spans for turns
and tool calls (optional extra), and W3C trace context propagated to tool servers.

## 11. Security
- Tool servers authenticate the agent with a per-tool header (configured in
  `tools[].headers`, values may reference env vars `${VAR}`).
- No secrets in configs committed to git; `.env.example` only.
- Transfers only to configured E.164 targets.
- Session logs may contain PII; storage location is operator-controlled and
  redaction hooks are a roadmap item.

## 12. Roadmap
- v0.2: outbound calls (dial-out + answer detection), warm transfer, SMS via a
  carrier webhook, Cal.com backend.
- v0.3: knowledge-base search tool (pgvector), post-call summary + structured
  extraction, canned-audio cache for static lines.
- v0.4: minimal dashboard (calls, transcripts, eval results), redaction.

## 13. Repository layout

```
pj-voice/
  SPEC.md  README.md  LICENSE  pyproject.toml  .env.example
  src/pjvoice/
    engine/   config.py  stages.py  tools.py  guards.py
    text/     runner.py
    voice/    worker.py  repeat.py
    control/  app.py  store.py
    booking/  backend.py  app.py
    evals/    harness.py
    cli.py
  examples/demo-salon.yaml
  evals/scenarios/*.yaml
  tests/
```
