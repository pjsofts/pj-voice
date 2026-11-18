"""Small runtime guards against common LLM failure modes."""

from __future__ import annotations

import json
from typing import Any

NO_PROGRESS_NOTE = (
    "\n\n[Note: you made this exact call before with the same arguments and got the same "
    "answer. Calling it again will not change it. Act on this result or ask the caller "
    "for what is missing.]"
)


class NoProgressTracker:
    """Flags a repeat of the same tool + identical args that returns an identical result.

    The call is never blocked. Legitimate repeats (availability after a
    cancellation, a list after a change) return a different result and never trip it.
    """

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    @staticmethod
    def _key(tool: str, args: dict[str, Any]) -> str:
        return tool + ":" + json.dumps(args, sort_keys=True, default=str)

    def annotate(self, tool: str, args: dict[str, Any], result: str) -> str:
        key = self._key(tool, args)
        prev = self._seen.get(key)
        self._seen[key] = result
        if prev is not None and prev == result:
            return result + NO_PROGRESS_NOTE
        return result


_TERMINAL = (".", "?", "!", "…")


class RepeatSuppressor:
    """Streaming filter that drops a verbatim restart of the reply.

    Some models occasionally emit the whole reply twice in one completion. Text
    passes straight through until the first complete utterance (ending in
    . ? ! …, at least ``min_len`` chars) has been forwarded. After that, text
    that re-traces that utterance from its start is held. If the *entire*
    utterance is reproduced, the rest of the turn is dropped; on any divergence
    the held text is released, so a genuine continuation that merely starts the
    same way is never lost (it is only delayed until it diverges).
    """

    def __init__(self, min_len: int = 15) -> None:
        self.min_len = min_len
        self.forwarded = ""
        self.first: str | None = None  # first complete utterance, stripped
        self.held = ""
        self.dropping = False

    def _maybe_lock_first(self) -> None:
        if self.first is not None:
            return
        text = self.forwarded.strip()
        if len(text) >= self.min_len and text.endswith(_TERMINAL):
            self.first = text

    def push(self, chunk: str) -> str:
        if self.dropping:
            return ""
        out: list[str] = []
        for ch in chunk:
            if self.first is None:
                self.forwarded += ch
                out.append(ch)
                if ch in _TERMINAL:
                    self._maybe_lock_first()
                continue
            if not self.held and ch.isspace():
                self.forwarded += ch
                out.append(ch)
                continue
            candidate = self.held + ch
            if self.first.startswith(candidate):
                self.held = candidate
                if candidate == self.first:
                    self.dropping = True
                    self.held = ""
                    break
            else:
                self.forwarded += candidate
                out.append(candidate)
                self.held = ""
        return "".join(out)

    def flush(self) -> str:
        held, self.held = self.held, ""
        if self.dropping:
            return ""
        self.forwarded += held
        return held
