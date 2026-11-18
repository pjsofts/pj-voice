# Architecture

PJ Voice has four parts, all in one Python package:

```
                         ┌──────────────────────────────────────────┐
 Caller ─PSTN─▶ SIP ─▶   │ LiveKit room                             │
 Browser ───────────▶    │   └─ voice worker  (pjvoice.voice)       │
                         └──────────────┬───────────────────────────┘
                                        │ uses
 SMS / chat / evals ─▶ text runner ─────┤        (pjvoice.text)
                                        ▼
                         ┌──────────────────────────────────────────┐
                         │ engine  (pjvoice.engine)                 │
                         │  config · stage graph · HTTP tools ·     │
                         │  injected args · guards                  │
                         └───────┬───────────────────────┬──────────┘
                                 │ tool calls            │ config lookups, logs
                                 ▼                       ▼
                     tool server (your API)      control plane (pjvoice.control)
                     e.g. pjvoice.booking        routing · storage · tenant webhook
```

## Engine (`pjvoice.engine`)
Pure Python, with no LiveKit or HTTP framework imports. Both runtimes drive the same objects.

- `config.py`: Pydantic models for an assistant (`AssistantConfig`, `ToolSpec`, `StageGraphConfig`). Validates stage references, unique tool names and E.164 transfer targets.
- `stages.py`: `StageGraph`, which knows the active stage, composes its instructions, resolves its tool subset, turns transitions into `to_<stage>` tools, and enforces hop caps.
- `tools.py`: `HttpToolExecutor`, which builds the protocol request, fills injected arguments, applies per-tool timeouts, maps failures to messages the model can recover from, and records a per-call tool log.
- `guards.py`: `NoProgressTracker` (identical call + identical result → nudge) and `RepeatSuppressor` (drops a reply the model says twice).

## Voice worker (`pjvoice.voice.worker`)
A LiveKit `AgentServer`. On each job it:
1. Waits for the caller and reads SIP attributes (caller ID, dialled number).
2. Loads the assistant from the control plane: by dialled number for SIP, or by the `web-<assistant_id>-…` room name for browser calls.
3. Builds an `AgentSession` (Silero VAD, STT/LLM/TTS via LiveKit Inference, a fallback LLM).
4. Starts a `PJAgent` for the initial stage. Handoff tools return a new `PJAgent` for the target stage, which is a *silent* handoff, and the chat history carries over.
5. Speaks the greeting, handles silence prompts, and posts a session log on shutdown.

## Text runner (`pjvoice.text.runner`)
A Chat Completions tool loop over any OpenAI-compatible endpoint. It runs the same stage graph, tools and guards, so a behaviour you fix in text is fixed on the phone too. Used by the SMS/chat endpoint, `pjvoice chat` and evals.

## Control plane (`pjvoice.control`)
FastAPI + SQLite.
- `/v1/*` (management key): assistant CRUD, text turns, session logs.
- `/agent/*` (worker key): lookup by phone or id, session-log ingest.
- `ConfigResolver`: if an assistant has a `config_webhook`, it fetches the live config, stores it as last-known-good, and falls back to that copy on any failure.

## Reference tool server (`pjvoice.booking`)
A generic appointment-booking API on a pluggable `BookingBackend`, shipped with an in-memory backend. It exists to show the correctness layer a real business API should have; see [Writing a tool server](writing-tools.md).

## Request lifecycle (one phone turn)
1. Caller audio → VAD + turn detector decide the turn ended.
2. STT transcript → LLM with the active stage's instructions and tools.
3. LLM calls `check_availability` → `HttpToolExecutor` injects `caller_phone`, POSTs to the tool server with `session_id`, and returns the result text.
4. LLM replies → `RepeatSuppressor` → TTS → caller.
