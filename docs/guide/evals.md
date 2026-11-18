# Evals

Prompt changes break things in ways unit tests can't see. `pjvoice eval` runs whole conversations: an LLM plays the caller, your assistant handles it through the text runner, and validators score the outcome.

```bash
pjvoice eval examples/demo-salon.yaml evals/scenarios --repeat 3 -v
```

## Scenario format
```yaml
name: returning customer books then cancels
persona: >
  Your name is Jordan Lee and you are an existing customer. Book a beard trim for
  next Wednesday afternoon, then change your mind and cancel it.
caller_phone: "+15550111"     # seeded as an existing customer in the demo backend
max_turns: 14
pass_score: 0.8
validators:
  - {type: tool_not_called, tool: create_customer, weight: 2}
  - {type: tool_called, tool: cancel_appointment, weight: 3}
  - {type: appointment_count, equals: 0, weight: 3}
```

| Validator | Passes when |
|---|---|
| `tool_called` / `tool_not_called` | the tool (or `to_<stage>` handoff) was / wasn't used |
| `appointment_count` | the backend ends with exactly `equals` booked appointments |
| `transcript_includes` / `transcript_excludes` | the agent's words match / don't match `pattern` (regex) |
| `ended` | the agent ended the call |

Score = passed weight ÷ total weight. Each scenario gets a fresh in-process backend, so runs are independent and never touch real systems.

## Tips
- Use `--repeat` because LLMs are stochastic; a scenario that passes 3 of 3 is a much better signal than 1 of 1.
- Check **state** (`appointment_count`) over wording. It's what the business cares about.
- Add a scenario for every production bug before fixing it.
- Unit tests in `tests/` use a scripted fake LLM to pin down exact runtime behaviour in CI without API keys.
