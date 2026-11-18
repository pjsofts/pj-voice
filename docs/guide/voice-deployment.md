# Voice deployment

## 1. LiveKit
Use LiveKit Cloud or self-host LiveKit server. Set:
```
LIVEKIT_URL=wss://…
LIVEKIT_API_KEY=…
LIVEKIT_API_SECRET=…
```
STT, LLM and TTS go through LiveKit Inference by default, using the model strings in your config, so one set of credentials covers all three.

## 2. Control plane and tools
```bash
PJVOICE_API_KEY=… PJVOICE_AGENT_KEY=… pjvoice control --host 0.0.0.0
pjvoice tools --host 0.0.0.0          # or your own tool server
pjvoice load examples/demo-salon.yaml
```

## 3. Worker
```bash
PJVOICE_CONTROL_URL=https://control.example.com PJVOICE_AGENT_KEY=… pjvoice worker start
```
`pjvoice worker dev` gives hot reload locally. The worker registers as agent `pjvoice` (override with `PJVOICE_AGENT_NAME`).

## 4. Phone numbers (SIP)
1. Buy a number from any SIP carrier and point it at your LiveKit SIP endpoint.
2. Create an **inbound trunk** for that number in LiveKit.
3. Create a **dispatch rule** that puts each caller in a new room (prefix `call-`) and dispatches agent `pjvoice`.
4. Add the number to the assistant's `phone_numbers`.

The worker reads `sip.trunkPhoneNumber` (the dialled number) to pick the assistant and `sip.phoneNumber` (caller ID) for `caller_phone` injection.

## 5. Browser calls
Create a room named `web-<assistant_id>-<random>`, issue a participant token, and connect with any LiveKit client SDK, or with LiveKit's open source agents playground.

## Production checklist
- [ ] Separate `PJVOICE_API_KEY` and `PJVOICE_AGENT_KEY`; tool servers require their own bearer token.
- [ ] `fallback_llm` set to a different provider than `llm`.
- [ ] Tool `timeout_s` under ~8 s for anything a caller waits on.
- [ ] Transfer targets tested from a real phone.
- [ ] Session logs (they contain PII) stored where your privacy policy says.
- [ ] Evals pass in CI before every config change.
