"""
Idempotency manager to prevent duplicate outreach attempts.
Uses a local SQLite database to track processed operations.
"""
from __future__ import annotations

import hashlib
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config

logger = logging.getLogger(__name__)


class IdempotencyManager:
    """
    Prevents duplicate operations by tracking unique operation keys.
    Key format: {parent_id}:{channel}:{date}
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.DB_PATH
        self._init_db()

    def _init_db(self):
        """Initialize the idempotency table."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key_hash TEXT PRIMARY KEY,
                    operation_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to init idempotency DB: {e}")

    def generate_key(self, parent_id: str, channel: str, date_str: Optional[str] = None) -> str:
        """Generate a unique operation key."""
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        return f"{parent_id}:{channel}:{date_str}"

    def _hash_key(self, key: str) -> str:
        """Hash the operation key."""
        return hashlib.sha256(key.encode()).hexdigest()

    def check_and_set(self, parent_id: str, channel: str, date_str: Optional[str] = None) -> bool:
        """
        Check if operation was already done. If not, mark it as done.

        Returns:
            True if this is a NEW operation (proceed)
            False if this is a DUPLICATE (skip)
        """
        op_key = self.generate_key(parent_id, channel, date_str)
        key_hash = self._hash_key(op_key)

        try:
            conn = sqlite3.connect(str(self.db_path))
            existing = conn.execute(
                "SELECT key_hash FROM idempotency_keys WHERE key_hash = ?",
                (key_hash,)
            ).fetchone()

            if existing:
                conn.close()
                logger.warning(f"Duplicate operation detected: {op_key}")
                return False

            conn.execute(
                "INSERT INTO idempotency_keys (key_hash, operation_key, created_at) VALUES (?, ?, ?)",
                (key_hash, op_key, datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
            logger.info(f"New operation registered: {op_key}")
            return True

        except Exception as e:
            logger.error(f"Idempotency check failed: {e}")
            return True  # Fail open: allow operation if check fails

    def clear(self, parent_id: Optional[str] = None):
        """Clear idempotency keys (for testing)."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            if parent_id:
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE operation_key LIKE ?",
                    (f"{parent_id}:%",)
                )
            else:
                conn.execute("DELETE FROM idempotency_keys")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to clear idempotency keys: {e}")
