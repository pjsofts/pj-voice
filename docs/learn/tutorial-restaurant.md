# Tutorial: build a restaurant reservations agent

You'll build a phone agent that takes table reservations for a restaurant: your own tool server, an assistant config, a chat test, an eval, and finally a real phone number.

**Time:** about 45 minutes. **You need:** Python 3.11+, [uv](https://github.com/astral-sh/uv), an OpenAI API key (or any OpenAI-compatible endpoint). LiveKit is only needed for step 7.

The finished code is in [`examples/restaurant/`](../../examples/restaurant/), and `tests/test_restaurant_example.py` keeps it working.

---

## Step 1: Install

```bash
git clone https://github.com/pjsofts/pj-voice && cd pj-voice
uv venv && uv pip install -e '.[dev]'
export OPENAI_API_KEY=sk-...
```

## Step 2: Decide what the agent can do

Before any code, write the actions down. For a reservations line:

| Action | Tool | Inputs from caller | Inputs from the system |
|---|---|---|---|
| Find times | `check_tables` | date, party size | — |
| Book | `reserve` | which time, name | caller's phone |

Two tools is enough. Every extra tool is another chance for the model to pick the wrong one, and every extra call is silence on the line ([Ch. 3](03-tools-and-actions.md)).

## Step 3: Write the tool server

A tool server is any HTTP endpoint that speaks the [tool protocol](../guide/tool-protocol.md): it receives `{name, arguments, session_id}` and returns `{"results": [{"result": "…"}]}` or `{"results": [{"error": "…"}]}`.

Open [`examples/restaurant/server.py`](../../examples/restaurant/server.py). The important parts:

**Per-call state, keyed by `session_id`.** It remembers which options this call was offered and whether it already booked.

```python
@dataclass
class Session:
    offered: dict[str, tuple[datetime, int]] = field(default_factory=dict)  # option id -> (time, party)
    booking_id: str | None = None
```

**`check_tables` returns options with ids, worded for speech:**

```python
options.append(f"{t.strftime('%-I:%M %p')} (option_id {option_id})")
...
return f"Tables for {party} on {day.strftime('%A %-d %B')}: " + ", ".join(options) + ". Offer two or three."
```

**`reserve` enforces the rules in code, not in the prompt:**

```python
if s.booking_id:                                    # idempotent: one reservation per call
    return f"Already reserved: {describe(bookings[s.booking_id])}. Do not book again."
option = s.offered.get(args.get("option_id", ""))
if option is None:                                  # the model can't invent a time
    raise ToolError("That option was not offered on this call. Call check_tables first.")
...
with _lock:                                         # re-check: someone else may have taken it
    table = free_table(start, party)
    if table is None:
        raise ToolError("That time was just taken. Call check_tables again for fresh options.")
```

Notice that every error tells the model **what to do next**. That's how the agent recovers mid-call instead of getting stuck.

Run it:

```bash
uvicorn examples.restaurant.server:app --port 8200
```

## Step 4: Configure the assistant

Open [`examples/restaurant/assistant.yaml`](../../examples/restaurant/assistant.yaml):

```yaml
id: trattoria
greeting: Good evening, Trattoria Demo. Would you like to book a table?
instructions: |
  Ask for the date and party size, call check_tables, offer two or three times,
  get the name for the reservation, confirm everything, then call reserve.
tools:
  - name: reserve
    url: ${RESTAURANT_TOOLS_URL:-http://localhost:8200/tools}
    parameters:
      type: object
      properties:
        option_id: {type: string}
        name: {type: string}
        caller_phone: {type: string, x-injected: caller_phone}   # the model never sees this
      required: [option_id, name]
```

Key ideas:
- The **greeting** is spoken verbatim, with no LLM call, so it plays instantly.
- **`x-injected: caller_phone`** removes the field from the model's view; PJ Voice fills in the real caller ID.
- PJ Voice adds voice rules (short replies, no invented facts, confirm before acting) and today's date to the prompt automatically.

## Step 5: Talk to it

In a second terminal:

```bash
pjvoice chat examples/restaurant/assistant.yaml --show-tools
```

Try:
- "Table for four this Friday." → should offer a few times.
- "6:30, under Sam." → should confirm, then reserve.
- "Actually, can you book it again?" → the server returns "Already reserved", and the agent shouldn't double-book.
- "We're twelve people." → the server refuses; the agent should offer the manager.

`--show-tools` prints every tool call and result at the end, which is the fastest way to see *why* the agent did something.

## Step 6: Add evals

Manual chats don't scale. An eval has an LLM play the caller from a persona, runs the whole conversation against your real tool server, and scores the result. The example ships two in [`examples/restaurant/evals/`](../../examples/restaurant/evals/):

```yaml
# books-table.yaml
name: party of four books Friday evening
persona: >
  You are Sam. You want a table for four this coming Friday around 7 PM.
  Accept the closest time offered. Give the name Sam when asked.
validators:
  - {type: tool_called, tool: check_tables, weight: 1}
  - {type: tool_called, tool: reserve, weight: 3}
  - {type: transcript_excludes, pattern: "option_id|opt\\d", weight: 1}   # never read ids aloud
```

```yaml
# large-group.yaml
name: party of twelve is offered the manager
persona: You want a table for twelve tomorrow at 8 PM. Accept a transfer if offered.
validators:
  - {type: tool_not_called, tool: reserve, weight: 3}
  - {type: tool_called, tool: transfer_call, weight: 2}
```

Run them against your server, loaded in-process (no need to start uvicorn):

```bash
pjvoice eval examples/restaurant/assistant.yaml examples/restaurant/evals \
  --tools-app examples.restaurant.server:app --repeat 3 -v
```

Failed scenarios print the full transcript. Add a scenario for every bug you find before you fix it.

Also write a fast test with a **scripted LLM**, so CI catches runtime regressions without API keys; see `tests/test_restaurant_example.py`.

## Step 7: Put it on the phone

```bash
uv pip install -e '.[voice]'
cp .env.example .env            # add LIVEKIT_URL / KEY / SECRET
pjvoice control &
pjvoice load examples/restaurant/assistant.yaml
pjvoice worker dev
```

Then connect a number:
1. Buy a number from a SIP provider and point it at LiveKit SIP.
2. Create an inbound trunk and a dispatch rule for agent `pjvoice`.
3. Make sure the number is in `phone_numbers` (here `+15550200`; change it to yours) and re-run `pjvoice load`.

Call it. Interrupt it. Mumble. Put it on speakerphone in a noisy room. Fix what you hear, add a scenario for each fix, and repeat. Full details: [Voice deployment](../guide/voice-deployment.md).

---

## Where to go next
- Split the call into **stages** (e.g. `reserve` and `change_or_cancel`); see the salon example's `flow:` and [Ch. 4](04-conversation-design.md).
- Add a `cancel_reservation` tool with an **ownership check** (only the caller's own booking).
- Add a **fallback LLM** from a different provider.
- Read the [design decisions](../guide/design-decisions.md) behind PJ Voice.
