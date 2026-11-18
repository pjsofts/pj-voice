# 4. Conversation design for the ear

Text chat habits fail on the phone. Nobody can scroll back, skim, or click a link.

## Prompting for voice
Put these in every voice prompt (PJ Voice adds the first three automatically):
- **Short replies:** one or two sentences, one question at a time.
- **No invented facts:** never state a price, time, name or policy a tool didn't return.
- **Confirm before acting:** read back the details and get a clear yes before booking, changing or cancelling.
- **Today's date and timezone:** the model can't resolve "next Tuesday" otherwise. PJ Voice injects it.
- No markdown, lists, emoji or URLs, because they get read aloud literally.

## Say numbers and dates the way people do
| Written | Spoken |
|---|---|
| `2026-11-19T14:00Z` | "Thursday the nineteenth at 9 AM" |
| `$35.00` | "thirty-five dollars" |
| `+15550142` | "five five five, zero one four two" |
| `A0003` | don't say booking ids unless asked |

Do the formatting in the tool server (local time, weekday names) so the model only has to repeat it.

## Offer choices, don't list everything
"I have 9, 9:30 or 10 on Thursday" works. Reading out twelve slots doesn't. Return a few options and tell the model to offer two or three.

## Break calls into stages
One giant prompt with every tool gets slow and confused. Split the call into steps:

```
triage ──▶ booking
   │  ◀──────┘
   └────▶ manage (move / cancel)
```

Each stage has focused instructions and only the tools it needs. In PJ Voice, transitions become `to_<stage>` tools, and handoffs are silent: the caller just hears the conversation continue. Hop caps stop a confused model from bouncing between stages forever.

```yaml
flow:
  initial: triage
  stages:
    - name: triage
      tools: [find_customer]
      transitions:
        - {to: booking, when: The caller wants a new appointment.}
```

## Recover gracefully
- **Misheard input:** ask again with a narrower question ("Was that Tuesday or Thursday?").
- **Tool failure:** apologise once, try once more, then offer a callback or a human.
- **Out of scope:** say so plainly and offer a transfer.
- **Endings:** say goodbye *before* calling `end_call`; PJ Voice waits for the goodbye to finish playing before hanging up.

## Greeting
The greeting is spoken verbatim (no LLM call), so it plays instantly. Name the business, then ask an open question: "Thanks for calling Demo Salon. How can I help?"

**Next:** [Telephony →](05-telephony.md)
