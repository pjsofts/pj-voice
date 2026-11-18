"""SQLite storage for assistants, last-known-good tenant configs and session logs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Optional

from ..engine.config import AssistantConfig, normalize_e164

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistants (
  id TEXT PRIMARY KEY,
  config TEXT NOT NULL,
  live_config TEXT,            -- last known good config from the tenant webhook
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS phone_routes (
  phone TEXT PRIMARY KEY,
  assistant_id TEXT NOT NULL REFERENCES assistants(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS session_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  assistant_id TEXT,
  channel TEXT,
  data TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str = "pjvoice.db") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def put_assistant(self, cfg: AssistantConfig) -> None:
        with self._lock, self._db:
            for phone in cfg.phone_numbers:
                row = self._db.execute(
                    "SELECT assistant_id FROM phone_routes WHERE phone = ?", (normalize_e164(phone),)
                ).fetchone()
                if row and row[0] != cfg.id:
                    raise ValueError(f"{phone} is already routed to assistant {row[0]}")
            self._db.execute(
                "INSERT INTO assistants (id, config, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET config = excluded.config, live_config = NULL, "
                "updated_at = excluded.updated_at",
                (cfg.id, cfg.model_dump_json(), time.time()),
            )
            self._db.execute("DELETE FROM phone_routes WHERE assistant_id = ?", (cfg.id,))
            self._db.executemany(
                "INSERT INTO phone_routes (phone, assistant_id) VALUES (?, ?)",
                [(normalize_e164(p), cfg.id) for p in cfg.phone_numbers],
            )

    def get_assistant(self, assistant_id: str) -> Optional[tuple[AssistantConfig, Optional[AssistantConfig]]]:
        row = self._db.execute(
            "SELECT config, live_config FROM assistants WHERE id = ?", (assistant_id,)
        ).fetchone()
        if not row:
            return None
        base = AssistantConfig.model_validate_json(row[0])
        live = AssistantConfig.model_validate_json(row[1]) if row[1] else None
        return base, live

    def save_live_config(self, assistant_id: str, cfg: AssistantConfig) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE assistants SET live_config = ?, updated_at = ? WHERE id = ?",
                (cfg.model_dump_json(), time.time(), assistant_id),
            )

    def list_assistants(self) -> list[AssistantConfig]:
        rows = self._db.execute("SELECT config FROM assistants ORDER BY id").fetchall()
        return [AssistantConfig.model_validate_json(r[0]) for r in rows]

    def delete_assistant(self, assistant_id: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM assistants WHERE id = ?", (assistant_id,))
            return cur.rowcount > 0

    def assistant_for_phone(self, phone: str) -> Optional[str]:
        row = self._db.execute(
            "SELECT assistant_id FROM phone_routes WHERE phone = ?", (normalize_e164(phone),)
        ).fetchone()
        return row[0] if row else None

    def add_session_log(self, session_id: str, assistant_id: Optional[str], channel: str, data: dict[str, Any]) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO session_logs (session_id, assistant_id, channel, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, assistant_id, channel, json.dumps(data), time.time()),
            )
            return int(cur.lastrowid)

    def list_session_logs(self, assistant_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        q = "SELECT id, session_id, assistant_id, channel, data, created_at FROM session_logs"
        params: tuple = ()
        if assistant_id:
            q += " WHERE assistant_id = ?"
            params = (assistant_id,)
        q += " ORDER BY id DESC LIMIT ?"
        rows = self._db.execute(q, params + (limit,)).fetchall()
        return [
            {"id": r[0], "session_id": r[1], "assistant_id": r[2], "channel": r[3], "data": json.loads(r[4]), "created_at": r[5]}
            for r in rows
        ]
