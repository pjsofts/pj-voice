# Design decisions

## Correctness lives in the tool server, not the prompt
Prompts drift, models change, and instructions get ignored under pressure. Anything that would cost the business money (a double booking, a wrong price, a booking on the wrong account) is enforced in code the model can't skip: offered-slot guards, ownership checks, idempotent writes. The prompt explains the rules, and the server enforces them.

## Hide IDs from the model (injected arguments)
The worst LLM tool error isn't an invalid argument, because validation catches those. It's a **valid id for the wrong entity**. If a parameter isn't in the schema, the model can't fill it wrongly. This mirrors context injection in most agent frameworks, applied at the protocol level so any tool server benefits.

## Fail forward, not closed
When an injected value is missing, the call still goes out and the server answers "no customer identified yet, call find_customer first". Hard-blocking the call ends the conversation; an actionable error lets the agent fix the problem in the same turn.

## Stage graphs over one giant prompt
A single prompt with every tool makes it easier for the model to pick the wrong tool and slower per turn (more tokens). Stages give each step a small, focused tool set. Handoffs are silent tool calls, and hop caps stop two stages ping-ponging. That's the one failure mode a hand-rolled state machine needs a guard for.

## One engine, two runtimes
The voice worker and the text runner share config, stages, tools and guards. Evals and unit tests run the real logic without audio, and SMS/chat come almost for free.

## Never drop a call because a dependency failed
- Tenant config webhook down → last-known-good config.
- Primary LLM down → fallback model.
- Tool timeout → the agent apologises and offers a callback; the call continues.

## Latency budget
Callers start to hang up after about a second of silence. So: streaming everywhere, one HTTP hop per tool, short per-tool timeouts, a prewarmed VAD per worker process, small stage tool sets, and no extra LLM calls in the turn path.

## Guards that annotate, not block
`NoProgressTracker` doesn't refuse a repeated call; it appends a note to the result. `RepeatSuppressor` only drops text that exactly reproduces the first utterance, so a sentence that merely starts the same way is never lost. When a guard is unsure, it lets the conversation through.

## Small, boring infrastructure
SQLite and FastAPI for the control plane, plain HTTP for tools, YAML for configs. Everything runs on a laptop; swap in Postgres or a queue when you need them.
