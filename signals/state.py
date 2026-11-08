"""Which messages were already handled, in SQLite on the state volume.

A message can arrive twice: live over the websocket and again in the next
catch-up poll, or again after a restart. The seen table makes the second one
a no-op. Orders are safe on their own too (client_order_id), so a crash
between placing an order and recording the message only costs a duplicate
check at the terminal.
"""

from __future__ import annotations

import os
import sqlite3
import time

from signals.constants import SEEN_RETENTION_SECONDS


class State:
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS seen ("
            " source TEXT NOT NULL,"
            " message_id TEXT NOT NULL,"
            " seen_at REAL NOT NULL,"
            " PRIMARY KEY (source, message_id))"
        )
        self._db.execute(
            "DELETE FROM seen WHERE seen_at < ?",
            (time.time() - SEEN_RETENTION_SECONDS,),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def is_seen(self, source: str, message_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM seen WHERE source = ? AND message_id = ?",
            (source, message_id),
        ).fetchone()
        return row is not None

    def mark_seen(self, source: str, message_id: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO seen (source, message_id, seen_at) VALUES (?, ?, ?)",
            (source, message_id, time.time()),
        )
        self._db.commit()

    def has_any(self, source: str) -> bool:
        """Whether this source was ever read; on a first start its backlog is
        recorded as seen instead of traded."""
        row = self._db.execute("SELECT 1 FROM seen WHERE source = ? LIMIT 1", (source,)).fetchone()
        return row is not None
