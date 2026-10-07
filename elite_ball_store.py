"""Durable one-shot ball activations and inline actions."""

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from database import get_db, is_deleted_user_in_transaction


INLINE_ACTION_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class BallActionResult:
    status: str
    question: str | None = None
    answer: str | None = None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def activate_ball(chat_id: int, user_id: int) -> None:
    """Repeated activation of one chat/user remains one charge and keeps its age."""
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if is_deleted_user_in_transaction(conn.cursor(), user_id):
            return
        conn.execute(
            """INSERT OR IGNORE INTO elite_ball_activations
               (chat_id, user_id, created_at) VALUES (?, ?, ?)""",
            (chat_id, user_id, int(time.time())),
        )


def consume_chat_ball(chat_id: int, user_id: int) -> bool:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if is_deleted_user_in_transaction(conn.cursor(), user_id):
            return False
        row = conn.execute(
            "SELECT id FROM elite_ball_activations WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            "DELETE FROM elite_ball_activations WHERE id = ?", row,
        )
        return cursor.rowcount == 1


def create_inline_action(owner_user_id: int, question: str) -> str:
    token = secrets.token_urlsafe(18)
    now = int(time.time())
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if is_deleted_user_in_transaction(conn.cursor(), owner_user_id):
            raise ValueError("Deleted gnome cannot create inline action")
        conn.execute(
            "DELETE FROM elite_ball_inline_actions WHERE expires_at <= ?", (now,),
        )
        conn.execute(
            """INSERT INTO elite_ball_inline_actions
               (token_digest, owner_user_id, question, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (_digest(token), owner_user_id, question, now, now + INLINE_ACTION_TTL_SECONDS),
        )
    return token


def consume_inline_action(
    token: str, user_id: int, choose_answer: Callable[[], str],
) -> BallActionResult:
    """Serialize callbacks and persist one answer for each inline action."""
    if not token or len(token) > 48 or not token.isascii() or not all(
        c.isalnum() or c in "-_" for c in token
    ):
        return BallActionResult("unavailable")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        if is_deleted_user_in_transaction(cursor, user_id):
            return BallActionResult("unavailable")
        row = cursor.execute(
            """SELECT owner_user_id, question, expires_at, consumed_at, answer
               FROM elite_ball_inline_actions WHERE token_digest = ?""",
            (_digest(token),),
        ).fetchone()
        if row is None:
            return BallActionResult("unavailable")
        owner_id, question, expires_at, consumed_at, answer = row
        if owner_id != user_id:
            return BallActionResult("not_owner")
        if consumed_at is not None:
            return BallActionResult("already_used", question, answer)
        if expires_at <= int(time.time()):
            return BallActionResult("unavailable")
        answer = choose_answer()
        cursor.execute(
            """UPDATE elite_ball_inline_actions SET consumed_at = ?, answer = ?
               WHERE token_digest = ?""",
            (int(time.time()), answer, _digest(token)),
        )
        return BallActionResult("used", question, answer)
