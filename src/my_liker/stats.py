"""Durable, idempotent event journal and aggregate statistics."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


class StatsStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_key TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                notification_id INTEGER,
                actor_id INTEGER NOT NULL,
                actor_name TEXT NOT NULL,
                comment_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def record_received_like(self, notification: dict[str, Any]) -> None:
        context = notification.get("context", {})
        data = context.get("data", {})
        notification_id = notification.get("id")
        actor_id = data.get("actor_id")
        if not isinstance(notification_id, int) or not isinstance(actor_id, int):
            return
        self._insert(
            event_key=f"received:{notification_id}",
            event_type="received_like",
            notification_id=notification_id,
            actor_id=actor_id,
            actor_name=str(data.get("actor_name") or actor_id),
            comment_id=data.get("comment_id"),
        )

    def record_given_like(
        self, *, actor_id: int, actor_name: str, comment_id: int
    ) -> None:
        self._insert(
            event_key=f"given:{comment_id}",
            event_type="given_like",
            notification_id=None,
            actor_id=actor_id,
            actor_name=actor_name,
            comment_id=comment_id,
        )

    def aggregate(self) -> dict[str, dict[str, int | str]]:
        rows = self.connection.execute(
            """
            SELECT actor_id, MAX(actor_name),
                   SUM(event_type = 'received_like'),
                   SUM(event_type = 'given_like')
            FROM events
            GROUP BY actor_id
            ORDER BY MAX(actor_name), actor_id
            """
        )
        return {
            str(actor_id): {
                "name": name,
                "received_likes": int(received),
                "given_likes": int(given),
            }
            for actor_id, name, received, given in rows
        }

    def export_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.aggregate(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _insert(
        self,
        *,
        event_key: str,
        event_type: str,
        notification_id: int | None,
        actor_id: int,
        actor_name: str,
        comment_id: int | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO events (
                event_key, event_type, notification_id,
                actor_id, actor_name, comment_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_key,
                event_type,
                notification_id,
                actor_id,
                actor_name,
                comment_id,
            ),
        )
        self.connection.commit()
