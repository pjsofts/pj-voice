# Contributing

Thanks for helping! PJ Voice aims to stay small, readable and safe by default.

```bash
uv venv && uv pip install -e '.[dev,voice]'
pytest
```

- Keep `pjvoice.engine` free of LiveKit and web-framework imports.
- New runtime behaviour needs a unit test (use `ScriptedChat` in `tests/conftest.py`).
- New assistant behaviour needs an eval scenario in `evals/scenarios/`.
- Tool results and errors are written for the model and the caller's ear: short, specific, actionable.
- Open an issue first for large changes.

Good first issues: a Cal.com `BookingBackend`, SMS carrier webhook, OpenTelemetry spans, a Postgres store.
