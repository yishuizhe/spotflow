from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable


class SQLiteJsonListStore:
    def __init__(self, legacy_path: Path, root_key: str) -> None:
        self.legacy_path = legacy_path
        self.root_key = root_key
        self.db_path = legacy_path.with_suffix(".sqlite3")

    def load(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            self._ensure(conn)
            return self._load_unlocked(conn)

    def save(self, rows: list[dict[str, Any]]) -> None:
        with closing(self._connect()) as conn:
            self._ensure(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._save_unlocked(conn, rows)
            conn.commit()

    def update(self, mutator: Callable[[list[dict[str, Any]]], Any]) -> Any:
        with closing(self._connect()) as conn:
            self._ensure(conn)
            conn.execute("BEGIN IMMEDIATE")
            rows = self._load_unlocked(conn)
            result = mutator(rows)
            self._save_unlocked(conn, rows)
            conn.commit()
            return result

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                position INTEGER PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )
        migrated = conn.execute("SELECT value FROM meta WHERE key = 'legacy_migrated'").fetchone()
        if migrated:
            return
        rows = self._read_legacy_rows()
        if rows:
            conn.execute("BEGIN IMMEDIATE")
            self._save_unlocked(conn, rows)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_migrated', '1')")
            conn.commit()
        else:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_migrated', '1')")
            conn.commit()

    def _read_legacy_rows(self) -> list[dict[str, Any]]:
        if not self.legacy_path.exists():
            return []
        raw = self.legacy_path.read_text()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            backup = self.legacy_path.with_suffix(self.legacy_path.suffix + f".corrupt-{int(time.time())}")
            backup.write_text(raw)
            return []
        rows = payload.get(self.root_key, []) if isinstance(payload, dict) else []
        return rows if isinstance(rows, list) else []

    def _load_unlocked(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        result = []
        for row in conn.execute("SELECT payload FROM records ORDER BY position ASC"):
            try:
                item = json.loads(str(row["payload"]))
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

    def _save_unlocked(self, conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
        conn.execute("DELETE FROM records")
        conn.executemany(
            "INSERT INTO records (position, payload) VALUES (?, ?)",
            [(idx, json.dumps(row, sort_keys=True)) for idx, row in enumerate(rows)],
        )
