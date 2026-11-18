# 2. Turn-taking

Most "this bot is annoying" complaints are turn-taking problems: it cuts you off, it doesn't stop when you interrupt, or it waits forever.

## Voice activity detection (VAD)
VAD labels each audio frame as speech or not speech. It's fast and local (Silero is ~2 MB), but it only knows about *sound*, not meaning. A pause to think ("I'd like to book for… um…") looks the same as a finished sentence.

PJ Voice loads Silero once per worker process (`prewarm`) so the first call doesn't pay the load time.

## Endpointing: "are they done?"
The simplest rule is "N ms of silence = end of turn". Too short and you interrupt people mid-thought; too long and every reply feels slow.

**Semantic turn detection** improves on this: a small model looks at the *transcript* and judges whether the sentence is complete. "My phone number is 555…" → not done, wait. "Yes, that works." → done, reply now. LiveKit's turn detector does this, and `AgentSession` uses it automatically when available.

## Interruptions (barge-in)
Callers interrupt: "no, Thursday, not Tuesday". When they do, the agent should:
1. Stop speaking immediately.
2. Throw away the rest of the planned reply.
3. Know that only the part that was actually spoken counts as said.

LiveKit handles this. Your job is to keep replies short so there's less to interrupt.

**False interruptions:** a cough or "mm-hm" shouldn't stop the agent. Frameworks require a minimum speech duration or number of words before treating sound as an interruption.

## Echo on phone lines
On a phone call the agent's own voice can leak back down the line. Without echo cancellation, the agent "hears itself" and treats it as the caller interrupting. This most often happens during the greeting, when nothing has calibrated yet.

PJ Voice disables interruptions for the first 3 seconds of inbound SIP calls (`aec_warmup_duration`) so the greeting plays cleanly. See `voice/worker.py`.

## Silence and inactivity
If the caller goes quiet, don't wait forever:
1. After ~12 s: "Are you still there?"
2. After N prompts: a polite goodbye and hang up.

Configured via `limits.user_away_timeout_s` and `limits.away_prompts`.

## Checklist
- [ ] Semantic turn detector on, not just a silence timer.
- [ ] Replies of one or two sentences (less to interrupt, faster to start).
- [ ] Interruptions protected during the greeting on phone lines.
- [ ] Inactivity prompts, then a clean hang-up.

**Next:** [Tools →](03-tools-and-actions.md)
