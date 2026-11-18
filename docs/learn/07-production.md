# 7. Running in production

## Reliability: never drop a call
Everything a call depends on will fail sometimes. Plan for each:

| Dependency fails | PJ Voice behaviour |
|---|---|
| Primary LLM errors | `FallbackAdapter` switches to `fallback_llm` (use a different provider) |
| Tenant config webhook down | serve the last-known-good config |
| Tool times out | the agent hears an actionable error, apologises and offers a callback |
| Model loops between stages | hop caps refuse the switch and the agent keeps helping |
| Model repeats a useless call | no-progress note nudges it to act on the answer |

## Latency
- Prewarm per-process resources (VAD, HTTP clients) before the first call.
- Keep stage prompts and tool lists small.
- Measure **end of user speech → first agent audio** per turn, and alert on p95, not average.
- Put workers, LiveKit and providers in the same region.

## Observability
Log every turn and tool call with the **session id**. PJ Voice sends it to tool servers as `session_id` and `X-Call-Id`, so you can follow one call across services. At the end of each call the worker posts a session log (transcript, tool calls, stage path, end reason) to the control plane.

## Cost
Per-minute cost is roughly STT + LLM tokens + TTS characters + telephony + compute. Biggest levers:
- smaller models for simple stages,
- shorter prompts (stages again),
- canned audio for fixed lines such as greetings and disclaimers,
- ending calls promptly when the caller is done or gone.

## Security and privacy
- Separate keys for the management API, the worker API and each tool server.
- Transfers only to configured numbers.
- Call logs contain personal data, so decide retention and access up front.
- Caller ID can be spoofed. For sensitive actions, verify with something else (a code sent by SMS, a date of birth, a booking reference).
- Disclose that callers are talking to an AI where required.

## Release process
1. Change the config or prompt in a branch.
2. Run deterministic tests and evals in CI (`--repeat 3`).
3. Roll out to one number or tenant first.
4. Review real call logs, then roll out widely.

**You've finished the course.** Now build one: [Tutorial: restaurant reservations agent →](tutorial-restaurant.md)
