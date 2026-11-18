# 3. Tools: making the agent do things

An agent that only talks is a fancy answering machine. The value is in **actions**: look up the account, check the calendar, book, move, cancel, transfer.

## How tool calling works
You give the LLM a list of functions (name, description, JSON Schema for arguments). Each turn it either replies with text or asks to call one or more tools. You run them, feed back the results, and it continues until it has something to say.

```
user: "Can I get a haircut Thursday morning?"
LLM → check_availability(service_id="haircut", date="2026-11-19")
tool → "Openings: 9 AM (slot_id …); 9:30 AM (slot_id …)…"
LLM → "I have 9 or 9:30 on Thursday. Which works?"
```

PJ Voice tools are plain HTTP endpoints ([protocol](../guide/tool-protocol.md)), so your business logic can live in any language or service.

## Design tools for voice
**Few and coarse-grained.** Every tool call is another LLM round trip, which the caller hears as silence. Prefer `find_customer` returning the profile *and* upcoming appointments over three separate lookups.

**Results written for the ear.** The tool result is what the LLM will paraphrase out loud:

| Bad | Good |
|---|---|
| `{"start":"2026-11-19T14:00:00Z","staff":"st_8f2"}` | `Thursday 19 November at 9 AM with Sam (slot_id …)` |
| 40 open slots | At most 6, plus "offer two or three" |
| `500 Internal Server Error` | `That time was just taken. Other openings: …` |

**Errors that say what to do next.** "No customer is identified on this call yet. Call find_customer first" lets the model recover in the same turn. A stack trace doesn't.

**Fast.** Set timeouts under ~8 s for anything a caller waits on. If something is slow, have the agent say a filler line first ("let me check that for you").

## Safety belongs on the server
The model *will* eventually:
- invent a time that "sounds right",
- use a real customer id that belongs to someone else,
- call `book` twice after an interruption,
- state a price from memory.

Prompts reduce this, but they don't prevent it. Code does:

| Risk | Server-side guard |
|---|---|
| Invented slot | Only accept slot ids this session was offered |
| Wrong customer | Bind identity to the session; don't take a customer id from the model |
| Double booking | Idempotent writes keyed by session + slot |
| Race with another caller | Re-check inside a lock; return fresh alternatives on conflict |
| Wrong price | Render prices from data; say "quote exactly" |
| Cancelling someone else's booking | Ownership check against the session's customer |

All of these are implemented in `src/pjvoice/booking/app.py`. Read it; it's about 200 lines.

## Injected arguments
Some values should never come from the model at all: the caller's phone number, the session id, the identified customer. Mark them `x-injected` and PJ Voice removes them from the schema the model sees and fills them in itself:

```yaml
parameters:
  type: object
  properties:
    caller_phone: {type: string, x-injected: caller_phone}
```

If a parameter isn't in the schema, the model can't get it wrong.

**Next:** [Conversation design →](04-conversation-design.md)
