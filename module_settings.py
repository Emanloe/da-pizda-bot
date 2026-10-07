"""Per-chat controls for sources of unsolicited bot activity."""

import sqlite3

from database import get_db


# Stable IDs are the only values accepted from callback data or callers.
MODULE_CATALOG = {
    "duel_random_events": "modules.catalog.duel_random_events",
    "boss_auto": "modules.catalog.boss_auto",
    "monthly_summary": "modules.catalog.monthly_summary",
    "past_pizda": "modules.catalog.past_pizda",
    "chat_reactions": "modules.catalog.chat_reactions",
    "birthday_greetings": "modules.catalog.birthday_greetings",
}


def _require_module(module_id: str) -> None:
    if module_id not in MODULE_CATALOG:
        raise ValueError("Unknown module ID")


def is_module_enabled(chat_id: int, module_id: str) -> bool:
    """A missing override is enabled; this read never creates a row."""
    _require_module(module_id)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT enabled FROM chat_module_settings WHERE chat_id = ? AND module_id = ?",
                (chat_id, module_id),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        # Startup runs init_db first; legacy test databases may predate this table.
        if "no such table: chat_module_settings" not in str(exc):
            raise
        return True
    return row is None or bool(row[0])


def set_module_enabled(chat_id: int, module_id: str, enabled: bool, updated_by: int) -> None:
    _require_module(module_id)
    with get_db() as conn:
        conn.execute(
            """INSERT INTO chat_module_settings
               (chat_id, module_id, enabled, updated_at, updated_by)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
               ON CONFLICT(chat_id, module_id) DO UPDATE SET
                 enabled = excluded.enabled,
                 updated_at = excluded.updated_at,
                 updated_by = excluded.updated_by""",
            (chat_id, module_id, int(enabled), updated_by),
        )


def toggle_module_enabled(chat_id: int, module_id: str, updated_by: int) -> bool:
    """Invert the persisted state atomically; concurrent clicks invert in order."""
    _require_module(module_id)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT enabled FROM chat_module_settings WHERE chat_id = ? AND module_id = ?",
            (chat_id, module_id),
        ).fetchone()
        enabled = not (row is None or bool(row[0]))
        conn.execute(
            """INSERT INTO chat_module_settings
               (chat_id, module_id, enabled, updated_at, updated_by)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
               ON CONFLICT(chat_id, module_id) DO UPDATE SET
                 enabled = excluded.enabled,
                 updated_at = excluded.updated_at,
                 updated_by = excluded.updated_by""",
            (chat_id, module_id, int(enabled), updated_by),
        )
    return enabled
