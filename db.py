from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "packages.db"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            tracking_code TEXT NOT NULL,
            alias TEXT,
            pkg_id INTEGER NOT NULL,
            last_status TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, tracking_code)
        )
        """
    )
    conn.commit()
    conn.close()


def add_package(
    chat_id: int, tracking_code: str, pkg_id: int, alias: str | None = None, last_status: str | None = None
) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO packages (chat_id, tracking_code, alias, pkg_id, last_status) "
        "VALUES (?, ?, ?, ?, ?)",
        (chat_id, tracking_code, alias, pkg_id, last_status),
    )
    conn.commit()
    conn.close()


def list_packages(chat_id: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT tracking_code, alias, pkg_id, last_status FROM packages WHERE chat_id = ? ORDER BY added_at",
        (chat_id,),
    ).fetchall()
    conn.close()
    return rows


def get_package(chat_id: int, tracking_code: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT tracking_code, alias, pkg_id, last_status FROM packages WHERE chat_id = ? AND tracking_code = ?",
        (chat_id, tracking_code),
    ).fetchone()
    conn.close()
    return row


def list_all_packages() -> list[sqlite3.Row]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT chat_id, tracking_code, alias, pkg_id, last_status FROM packages"
    ).fetchall()
    conn.close()
    return rows


def resolve_code(chat_id: int, value: str) -> str | None:
    """Finds a package's tracking_code by exact code or alias (case-insensitive)."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT tracking_code FROM packages WHERE chat_id = ? AND "
        "(tracking_code = ? OR LOWER(alias) = LOWER(?)) LIMIT 1",
        (chat_id, value, value),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def update_alias(chat_id: int, tracking_code: str, alias: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "UPDATE packages SET alias = ? WHERE chat_id = ? AND tracking_code = ?",
        (alias, chat_id, tracking_code),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def update_last_status(chat_id: int, tracking_code: str, last_status: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE packages SET last_status = ? WHERE chat_id = ? AND tracking_code = ?",
        (last_status, chat_id, tracking_code),
    )
    conn.commit()
    conn.close()


def remove_package(chat_id: int, tracking_code: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "DELETE FROM packages WHERE chat_id = ? AND tracking_code = ?",
        (chat_id, tracking_code),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
