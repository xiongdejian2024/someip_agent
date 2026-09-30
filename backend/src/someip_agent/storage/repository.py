from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditRepository:
    """轻量审计存储；任何密钥字段都不得传入 detail。"""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    detail_json TEXT NOT NULL
                )
                """
            )

    def add(
        self,
        *,
        action: str,
        target: str,
        success: bool = True,
        actor: str = "local-user",
        detail: dict[str, Any] | None = None,
    ) -> None:
        serialized = json.dumps(detail or {}, ensure_ascii=False, default=str)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(created_at, actor, action, target, success, detail_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    actor,
                    action,
                    target,
                    int(success),
                    serialized,
                ),
            )

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, actor, action, target, success, detail_json
                FROM audit_events ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(limit, 2000)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "action": row["action"],
                "target": row["target"],
                "success": bool(row["success"]),
                "detail": json.loads(row["detail_json"]),
            }
            for row in rows
        ]
