from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import SQLiteJsonListStore


class DecisionAudit:
    def __init__(self, path: Path) -> None:
        self.store = SQLiteJsonListStore(path, "decisions")

    def record(self, payload: dict[str, Any], keep: int = 5000) -> dict[str, Any]:
        row = {"created_at": datetime.now(timezone.utc).isoformat(), **payload}

        def append(rows: list[dict[str, Any]]) -> dict[str, Any]:
            rows.append(row)
            if len(rows) > keep:
                del rows[: len(rows) - keep]
            return row

        return self.store.update(append)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.load()[-max(1, min(limit, 500)) :]
