# Tool protocol

Every business action is an HTTP call from the agent to a tool server you control.

## Request
`POST <tool.url>` (or the configured method)

```json
{
  "type": "tool_call",
  "tool_call_id": "call_book_appointment_1763460000000000_4821",
  "session_id": "call-_+15550142_abc123",
  "name": "book_appointment",
  "arguments": {"slot_id": "haircut|sam|20261119T1400Z"},
  "injected": ["caller_phone"]
}
```

| Header | Value |
|---|---|
| `X-Call-Id` | same as `session_id` |
| `X-Tool-Call-Id` | unique per invocation |
| `X-Tool-Name` | tool name |
| anything in `tools[].headers` | e.g. `Authorization` |

- `session_id` stays the same for the whole conversation. Use it as the key for per-call state.
- `injected` lists arguments the runtime filled rather than the model. This is informational; they're already merged into `arguments`.

## Response
Success:
```json
{"results": [{"toolCallId": "…", "result": "Booked: Haircut with Sam on Thursday 19 November at 9 AM (appointment_id A0003)."}]}
```
Recoverable failure:
```json
{"results": [{"toolCallId": "…", "error": "That time was just taken. Other openings: 9:30 AM (slot_id …); 10 AM (slot_id …)."}]}
```

Rules:
- `result` is **written for the model**: short, speakable, with IDs only where the next tool needs them. Non-string results are JSON-encoded.
- `error` must say **what to do next**. The model sees it as a tool error and usually recovers in the same turn.
- Return HTTP 200 for both. Non-2xx, timeouts and invalid JSON become a generic "system unavailable" message.

## Timeouts and retries
`timeout_s` per tool (default 10 s). The runtime never retries a tool call, because a retried write can double-book. Retry idempotent reads inside your server if you need to.

## Injected arguments
Mark a parameter with `x-injected: <source>` and it disappears from the model's schema:

| Source | Filled with |
|---|---|
| `caller_phone` | caller ID (E.164) |
| `called_phone` | dialled number |
| `session_id` | conversation id |
| `current_stage` | active stage |
| `server` / `true` | nothing: your server resolves it from `session_id` |

If the runtime has no value (e.g. a withheld caller ID), the call still goes out without it, and your server should answer with an actionable error.
