<h1 align="center">PJ Voice</h1>

<p align="center">
  <b>Open source AI phone agents that do real work, safely.</b><br>
  And a hands-on guide to building voice agents yourself.
</p>

<p align="center">
  <a href="https://pjsofts.github.io/pj-voice/">Website</a> ·
  <a href="docs/learn/README.md">Learn to build voice agents</a> ·
  <a href="docs/learn/tutorial-restaurant.md">Tutorial</a> ·
  <a href="docs/guide/README.md">Docs</a> ·
  <a href="SPEC.md">Spec</a>
</p>

<p align="center">
  <a href="https://github.com/pjsofts/pj-voice/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/pjsofts/pj-voice/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="LiveKit Agents" src="https://img.shields.io/badge/LiveKit-Agents%201.x-6f42c1">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

---

**An open source AI phone agent that does real work, safely.** Callers phone a
number and talk to an agent that looks up their account, checks availability,
and books, moves or cancels appointments through tools the business owns, with
correctness enforced on the server, not hoped for in the prompt.

- **Voice-first:** streaming STT → LLM → TTS on [LiveKit Agents](https://github.com/livekit/agents), SIP or browser.
- **One brain, many channels:** the same assistant config runs phone calls, text/SMS turns and evals.
- **Stage graphs:** split a call into steps (triage → booking → manage), each with its own instructions and tool subset. Silent handoffs, with loop caps.
- **Safe tools:** a tiny HTTP tool protocol. *Injected arguments* hide IDs from the model so it can't attach a booking to the wrong customer.
- **Doesn't drop calls:** a tenant config webhook with last-known-good fallback, plus a fallback LLM.
- **Testable without a phone:** a text runner and a simulated-caller eval harness with weighted validators.

## Learn to build voice agents

New to voice AI? The repo includes a practical course. Every chapter maps to working code here.

| | |
|---|---|
| 1. [How voice agents work](docs/learn/01-how-voice-agents-work.md) | STT → LLM → TTS, speech-to-speech, the latency budget |
| 2. [Turn-taking](docs/learn/02-turn-taking.md) | VAD, endpointing, interruptions, echo |
| 3. [Tools](docs/learn/03-tools-and-actions.md) | Designing actions, results for the ear, safety on the server |
| 4. [Conversation design](docs/learn/04-conversation-design.md) | Voice prompts, stages, confirmations, numbers and dates |
| 5. [Telephony](docs/learn/05-telephony.md) | PSTN, SIP trunks, caller ID, transfers, consent |
| 6. [Testing and evals](docs/learn/06-testing-and-evals.md) | Scripted tests, simulated callers, regressions |
| 7. [Production](docs/learn/07-production.md) | Reliability, latency, observability, cost, privacy |
| **[Tutorial](docs/learn/tutorial-restaurant.md)** | Build a restaurant reservations agent from scratch (~45 min) |

```
Caller ─SIP─▶ LiveKit ─▶ voice worker ──┐                         ┌─▶ tool server (your business API)
                                        ├─ engine (stages, tools) ┤
SMS / chat / evals ─▶ text runner ──────┘                         └─▶ control plane (routing, configs, logs)
```

## Quick start (text mode, no phone needed)

```bash
git clone https://github.com/pjsofts/pj-voice && cd pj-voice
uv venv && uv pip install -e '.[dev]'
export OPENAI_API_KEY=sk-...   # or PJVOICE_LLM_BASE_URL + PJVOICE_LLM_API_KEY for any OpenAI-compatible API

pjvoice tools &                 # reference booking server on :8100 (a demo salon)
pjvoice chat examples/demo-salon.yaml --show-stage --show-tools
```

## Phone calls

```bash
uv pip install -e '.[voice]'
cp .env.example .env                     # LiveKit + keys
pjvoice control &                        # control plane on :8000
pjvoice load examples/demo-salon.yaml    # routes +15550100 to the demo assistant
pjvoice worker dev                       # LiveKit worker (agent name: pjvoice)
```

Point a LiveKit SIP inbound trunk and dispatch rule at agent `pjvoice`. The
worker looks up the assistant by the dialled number. For browser calls, join a
room named `web-<assistant_id>-<anything>`.

## Evals

```bash
pjvoice eval examples/demo-salon.yaml evals/scenarios -v
```

An LLM plays the caller from a persona; each scenario is scored by weighted
validators (tools called or not called, final booking state, transcript
patterns) against a fresh in-process booking backend, so runs are reproducible
and never touch real systems.

## Write your own tools

A tool is any HTTP endpoint that accepts

```json
{"type": "tool_call", "tool_call_id": "…", "session_id": "…", "name": "book_appointment", "arguments": {"slot_id": "…"}}
```

and returns `{"results": [{"toolCallId": "…", "result": "text for the model"}]}`
(or `"error": "what to do next"`). Mark a parameter `x-injected: caller_phone`
and the model never sees it; the runtime fills it in. See `src/pjvoice/booking/`
for the patterns: server-side identity, an offered-slot guard, re-check before
write, idempotent bookings.

## Documentation

- [Architecture](docs/guide/architecture.md) · [Configuration](docs/guide/configuration.md) · [Tool protocol](docs/guide/tool-protocol.md)
- [Writing a tool server](docs/guide/writing-tools.md) · [Voice deployment](docs/guide/voice-deployment.md) · [Evals](docs/guide/evals.md)
- [Design decisions](docs/guide/design-decisions.md) · [Full spec](SPEC.md)

## Project layout

```
src/pjvoice/
  engine/    config, stage graph, HTTP tools, injected args, guards (no framework imports)
  voice/     LiveKit worker
  text/      text runner (SMS, chat, evals)
  control/   control plane API + SQLite store
  booking/   reference tool server with a correctness layer
  evals/     simulated-caller harness
examples/    demo salon (stages) and restaurant tutorial
evals/       eval scenarios
docs/        landing page, guides and the course
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Good first issues: a Cal.com backend, an SMS carrier webhook, OpenTelemetry spans.

## Development

```bash
uv pip install -e '.[dev,voice]'
pytest
```

## License

MIT. See [LICENSE](LICENSE). Built by [Pouria Jahandideh](https://pouriajahandideh.com/). More of my work: [pouriajahandideh.com](https://pouriajahandideh.com/) · [AI Engineering course](https://pjsofts.github.io/).
