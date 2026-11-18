# Configuration

An assistant is one YAML (or JSON) document. Load it with `pjvoice load file.yaml`, or `PUT /v1/assistants/{id}`.

```yaml
id: demo-salon                   # [a-z0-9_-], unique
name: Demo Salon receptionist
phone_numbers: ["+15550100"]     # E.164; each number routes to one assistant
timezone: America/New_York       # used for "today is…" and in tool servers
greeting: Thanks for calling Demo Salon. How can I help?
instructions: ""                 # used when there is no flow (single-stage)
```

## models
| Field | Default | Notes |
|---|---|---|
| `llm` | `openai/gpt-4.1-mini` | LiveKit Inference model string. In text mode the `provider/` prefix is stripped unless `PJVOICE_KEEP_MODEL_PREFIX=1` |
| `fallback_llm` | none | Used when the primary errors (voice: `FallbackAdapter`; text: retry) |
| `stt` | `deepgram/nova-3` | |
| `tts` | `cartesia/sonic-2` | |
| `voice` | none | Provider voice id |
| `language` | `en` | STT language |

## limits
| Field | Default | Meaning |
|---|---|---|
| `max_tool_steps` | 6 | Tool rounds per turn before the agent must answer |
| `max_stage_hops` | 12 | Total stage switches per call |
| `max_silent_hops` | 3 | Switches in a row without the agent speaking |
| `user_away_timeout_s` | 12 | Silence before "are you still there?" |
| `away_prompts` | 2 | Prompts before ending the call |

## flow (optional stage graph)
```yaml
flow:
  initial: triage
  global_instructions: Applies to every stage.
  stages:
    - name: triage
      instructions: Find out what the caller needs.
      tools: [find_customer]
      llm: openai/gpt-4.1          # optional per-stage model
      transitions:
        - to: booking
          when: The caller wants a new appointment.   # becomes the to_booking tool description
```
Without `flow`, a single stage named `main` gets `instructions` and every tool.

Every stage also gets `end_call`, plus `transfer_call` when transfer targets exist.

## tools
```yaml
tools:
  - name: check_availability
    description: Open times for a service on a date.
    url: ${TOOLS_URL:-http://localhost:8100/tools}     # env vars expand at call time
    method: POST
    timeout_s: 8
    headers: {Authorization: "Bearer ${PJVOICE_TOOLS_KEY}"}
    parameters:                                         # JSON Schema
      type: object
      properties:
        service_id: {type: string}
        date: {type: string, description: YYYY-MM-DD}
        caller_phone: {type: string, x-injected: caller_phone}
      required: [service_id, date]
```
`x-injected` sources: `caller_phone`, `called_phone`, `session_id`, `current_stage`, or `server`/`true`, meaning the tool server resolves the value from `session_id`. Injected parameters are removed from the schema the model sees.

## transfer
```yaml
transfer:
  mode: static
  targets:
    - {id: front_desk, name: Front desk, phone: "+15550199", description: A person at the salon}
```
The model can only pick a configured `id`. It never dials a number it made up.

## config_webhook (multi-tenant)
```yaml
config_webhook:
  url: https://tenant.example.com/pjvoice/config
  timeout_s: 3
  headers: {Authorization: "Bearer …"}
```
On each lookup the control plane GETs `url?assistant_id=…&phone=…&caller=…`. The response is a full assistant config; `id`, `phone_numbers` and `config_webhook` are always taken from the stored config, so a tenant can't re-route numbers. A valid response is saved as last-known-good, and any failure serves that copy.
