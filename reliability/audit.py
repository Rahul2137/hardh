"""
Audit logging to SQLite for full traceability.
Every action in the system is logged for accountability, debugging, and compliance.
"""
from __future__ import annotations

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    SQLite-based audit logger for all outreach actions.
    Provides full traceability of the system's behavior.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.DB_PATH
        self._init_db()

    def _init_db(self):
        """Initialize the audit database and table."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    channel TEXT,
                    status TEXT,
                    details TEXT,
                    error TEXT,
                    duration_ms REAL,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_id ON audit_log(entity_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_log(timestamp)
            """)
            conn.commit()
            conn.close()
            logger.info(f"Audit DB initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to init audit DB: {e}")

    def log(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        channel: str = "",
        status: str = "",
        details: str = "",
        error: str = "",
        duration_ms: float = 0.0,
        metadata: Optional[dict] = None,
    ):
        """Log an audit entry."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                """INSERT INTO audit_log 
                   (timestamp, action, entity_type, entity_id, channel, status, details, error, duration_ms, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(),
                    action,
                    entity_type,
                    entity_id,
                    channel,
                    status,
                    details,
                    error,
                    duration_ms,
                    json.dumps(metadata) if metadata else "",
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Audit log write failed: {e}")

    def get_logs(
        self,
        entity_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Retrieve audit logs with optional filters."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM audit_log WHERE 1=1"
            params = []

            if entity_id:
                query += " AND entity_id = ?"
                params.append(entity_id)
            if action:
                query += " AND action = ?"
                params.append(action)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            conn.close()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Audit log read failed: {e}")
            return []

    def get_all_logs_formatted(self) -> str:
        """Get all logs formatted as a readable string."""
        logs = self.get_logs(limit=1000)
        if not logs:
            return "No audit logs found."

        lines = ["=" * 80, "AUDIT LOG", "=" * 80]
        for log in logs:
            lines.append(
                f"[{log['timestamp']}] {log['action']} | "
                f"{log['entity_type']}:{log['entity_id']} | "
                f"Channel: {log['channel']} | Status: {log['status']}"
            )
            if log.get('details'):
                lines.append(f"  Details: {log['details']}")
            if log.get('error'):
                lines.append(f"  ERROR: {log['error']}")
            lines.append("")

        return "\n".join(lines)
