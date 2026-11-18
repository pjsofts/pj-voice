# 1. How voice agents work

A voice agent is a loop: **listen → understand → act → speak**, repeated every turn, fast enough to feel like a conversation.

## The cascaded pipeline

```
caller audio ─▶ VAD ─▶ STT ─▶ LLM (+ tools) ─▶ TTS ─▶ caller audio
               "is      "what   "what to say    "say it"
               someone  did     / do"
               talking" they
                        say"
```

| Stage | Job | Typical providers |
|---|---|---|
| **VAD** (voice activity detection) | Detect speech vs silence in the audio stream | Silero (runs locally) |
| **STT** (speech-to-text) | Stream audio in, partial and final transcripts out | Deepgram, AssemblyAI, Whisper variants |
| **LLM** | Decide the reply and which tools to call | GPT, Claude, Gemini, Llama… |
| **TTS** (text-to-speech) | Stream text in, audio out | Cartesia, ElevenLabs, OpenAI |

Each stage **streams**. TTS starts speaking the first sentence while the LLM is still writing the second. Without streaming, latencies add up and the call feels dead.

In PJ Voice, `pjvoice/voice/worker.py` builds this pipeline with LiveKit's `AgentSession`: `stt=…, llm=…, tts=…, vad=…`.

## Speech-to-speech (realtime) models
Newer models take audio in and produce audio out directly. Pros: lower latency, they hear tone and emotion. Cons: less control (you can't inspect or rewrite the text before it's spoken), usually pricier, harder to test, and fewer voices and languages. Many production agents still use the cascade for control and testability; that's what PJ Voice does.

## The latency budget
People expect a reply within roughly a second of finishing speaking. Longer silences feel broken, and callers start saying "hello?" or hang up. A rough budget for one turn:

| Step | Rough cost |
|---|---|
| Endpointing (deciding they finished) | 200–800 ms |
| STT final transcript | 100–300 ms |
| LLM time-to-first-token | 200–1000 ms |
| TTS time-to-first-audio | 100–300 ms |
| Network / telephony | 50–200 ms |

These are ballpark figures that vary with provider, region and load, so measure your own. The biggest levers:
- **Endpointing:** a smarter turn detector lets you wait less without cutting people off ([Ch. 2](02-turn-taking.md)).
- **Model choice:** a smaller, faster model for routine stages; save big models for hard steps (per-stage `llm` in PJ Voice).
- **Tool latency:** every tool call is a full extra LLM round. Keep tools fast and few ([Ch. 3](03-tools-and-actions.md)).
- **Region:** run the worker close to your providers and your callers.
- **Prompt size:** fewer tokens means a faster first token. Stage graphs keep each prompt and tool list small.

## Where PJ Voice fits
```
LiveKit (audio transport, SIP, rooms)
  └─ PJ Voice worker (session, stages, tools, guards)
       └─ your tool server (business logic, correctness)
```
LiveKit moves audio in real time; PJ Voice decides what the agent knows, says and does; your server owns the business rules.

**Next:** [Turn-taking →](02-turn-taking.md)
