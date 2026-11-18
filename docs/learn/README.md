# Learn to build voice agents

A practical course on building AI agents that talk on the phone and get things done. Every idea maps to working code in this repo, so you can read a chapter, then open the file that implements it.

**Who it's for:** engineers who have used an LLM API and want to build production voice agents. No audio or telephony background needed.

## Chapters
1. [How voice agents work](01-how-voice-agents-work.md): the STT → LLM → TTS pipeline, speech-to-speech models, and where the milliseconds go
2. [Turn-taking](02-turn-taking.md): VAD, endpointing, turn detection, interruptions and echo
3. [Tools: making the agent do things](03-tools-and-actions.md): tool design, results written for the ear, and safety on the server
4. [Conversation design for the ear](04-conversation-design.md): voice prompts, stages, confirmations, and reading out numbers and dates
5. [Telephony](05-telephony.md): PSTN, SIP, trunks, caller ID, transfers and consent
6. [Testing and evals](06-testing-and-evals.md): scripted tests, simulated callers and regression suites
7. [Running in production](07-production.md): latency, reliability, observability, cost and privacy

## Hands-on
- **[Tutorial: build a restaurant reservations agent](tutorial-restaurant.md).** Write your own tool server, configure an assistant, chat with it, add evals, then put it on a phone number. About 45 minutes.

## Cheat sheet
| Problem | Where to look |
|---|---|
| Agent is slow to answer | [Ch. 1 latency budget](01-how-voice-agents-work.md#the-latency-budget), [Ch. 7](07-production.md) |
| Agent talks over the caller, or the caller can't interrupt | [Ch. 2](02-turn-taking.md) |
| Agent books the wrong thing | [Ch. 3 server-side safety](03-tools-and-actions.md#safety-belongs-on-the-server) |
| Agent sounds robotic or reads IDs aloud | [Ch. 4](04-conversation-design.md) |
| "It worked yesterday" | [Ch. 6 evals](06-testing-and-evals.md) |
