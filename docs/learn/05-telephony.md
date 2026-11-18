# 5. Telephony

## The path of a phone call
```
caller's phone ─▶ carrier (PSTN) ─▶ your SIP provider ─▶ SIP trunk ─▶ LiveKit SIP ─▶ room ─▶ agent
```
- **PSTN:** the public phone network.
- **SIP:** the protocol that sets up and controls voice calls over the internet.
- **SIP trunk:** the connection between your phone-number provider and LiveKit. Inbound trunks receive calls; outbound trunks place them.
- **Dispatch rule:** tells LiveKit which room to put a caller in and which agent to send.

## Setting up an inbound number
1. Buy a number from a SIP-capable provider.
2. Point the number's SIP traffic at your LiveKit SIP endpoint.
3. Create an inbound trunk in LiveKit for that number.
4. Create a dispatch rule: a new room per call, agent `pjvoice`.
5. Add the number to your assistant's `phone_numbers` and `pjvoice load` it.

Step-by-step commands: [Voice deployment](../guide/voice-deployment.md).

## What the agent knows about the call
LiveKit exposes SIP details as participant attributes:
- `sip.phoneNumber`: the caller's number (caller ID). It may be missing or spoofed, so treat it as a hint, not proof of identity.
- `sip.trunkPhoneNumber`: the number that was dialled, which PJ Voice uses to pick the assistant.

## Transfers
**Cold transfer (SIP REFER):** the agent hands the call to another number and leaves. PJ Voice's `transfer_call` does this, but only to targets listed in the config. The model never dials a number on its own.

**Warm transfer:** the agent calls the human first, briefs them, then connects the caller. It's more complex and is on the roadmap.

## Audio quality
Phone audio is narrowband (8 kHz) and often noisy. Use STT models tuned for telephony, enable noise cancellation if your stack supports it, and give STT a list of key terms (service names, staff names) to improve recognition.

## Recording and consent
Laws on call recording and on disclosing that the caller is speaking with an AI vary by country and state. Common practice: say it up front ("This is an automated assistant; this call may be recorded"). Check the rules where you operate.

**Next:** [Testing and evals →](06-testing-and-evals.md)
