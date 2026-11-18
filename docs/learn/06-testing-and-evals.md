# 6. Testing and evals

Voice agents fail in ways normal tests miss: a prompt tweak that makes the model skip a confirmation, a new model that calls tools in a different order. Use three layers.

## Layer 1: deterministic tests (CI, no API keys)
Replace the LLM with a script and test the runtime itself: stage switching, tool injection, guards, error handling.

```python
chat = ScriptedChat([
    call("find_customer"),
    say("Hi! What's your name?"),
])
runner = TextRunner(chat=chat, tools=HttpToolExecutor(tools_client))
convo = runner.start(salon, "s1", "+15550142")
await runner.turn(convo, "Hi, I'd like a haircut")
assert convo.ctx.tool_log[0]["arguments"] == {"caller_phone": "+15550142"}
```
See `tests/test_runner_booking.py`. These run in milliseconds on every push.

## Layer 2: simulated-caller evals (real LLMs)
An LLM plays the caller from a persona; your real assistant handles the call; validators score the result.

```yaml
name: new customer books a haircut
persona: Your name is Dana Kim. You want a haircut next Tuesday morning.
validators:
  - {type: tool_called, tool: book_appointment, weight: 3}
  - {type: appointment_count, equals: 1, weight: 3}
```
```bash
pjvoice eval examples/demo-salon.yaml evals/scenarios --repeat 3
```
- **Check outcomes, not wording.** "Exactly one appointment exists" beats "the agent said 'booked'".
- **Repeat runs:** LLMs are stochastic, so look at pass rates.
- **One scenario per bug:** every production failure becomes a regression test.

Details: [Evals guide](../guide/evals.md).

## Layer 3: real calls
Before launch and after big changes, make real phone calls from a real phone: noisy room, interruptions, accents, long pauses, a withheld number. Listen to recordings of real calls every week.

## What to measure
| Metric | Why |
|---|---|
| Task success (booked / moved / cancelled correctly) | the thing the business pays for |
| Transfer rate | how often the agent gives up |
| Turn latency (end of speech → first audio) | the main driver of perceived quality |
| Tool error rate | broken integrations show up here first |
| Calls ended by caller mid-task | frustration |

**Next:** [Running in production →](07-production.md)
