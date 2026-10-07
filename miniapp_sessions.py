"""Chat-scoped, restart-safe Mini App launch and bearer sessions."""

import hashlib
import re
import secrets
import time
from dataclasses import dataclass

from database import get_db, is_deleted_user_in_transaction


LAUNCH_TTL_SECONDS = 120
SESSION_TTL_SECONDS = 3600
_OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")


@dataclass(frozen=True)
class MiniAppSession:
    chat_id: int
    user_id: int
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class IssuedMiniAppSession:
    token: str
    session: MiniAppSession
    launch_message_id: int | None


def _digest(token: str) -> str | None:
    if not isinstance(token, str) or not _OPAQUE_TOKEN.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_launch_token(chat_id: int, user_id: int, *, now: int | None = None) -> str:
    """Only Telegram group handlers should pass their trusted update IDs here."""
    if type(chat_id) is not int or type(user_id) is not int or chat_id >= 0 or user_id <= 0:
        raise ValueError("Group chat and Telegram user IDs are required")
    issued_at = int(time.time()) if now is None else now
    token = secrets.token_urlsafe(32)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if is_deleted_user_in_transaction(conn.cursor(), user_id):
            raise ValueError("Deleted user cannot launch game")
        conn.execute(
            """INSERT INTO miniapp_launch_tokens
               (token_digest, chat_id, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (_digest(token), chat_id, user_id, issued_at, issued_at + LAUNCH_TTL_SECONDS),
        )
    return token


def bind_launch_message_id(token: str, chat_id: int, user_id: int,
                           message_id: int, *, now: int | None = None) -> bool:
    """Persist the sent message before exposing its Mini App button."""
    digest = _digest(token)
    if (digest is None or type(chat_id) is not int or type(user_id) is not int or
            type(message_id) is not int or message_id <= 0):
        return False
    timestamp = int(time.time()) if now is None else now
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE miniapp_launch_tokens SET launch_message_id = ?
               WHERE token_digest = ? AND chat_id = ? AND user_id = ?
                 AND launch_message_id IS NULL AND consumed_at IS NULL AND expires_at > ?""",
            (message_id, digest, chat_id, user_id, timestamp),
        )
        return cursor.rowcount == 1


def exchange_launch_token(token: str, verified_user_id: int, *,
                          now: int | None = None,
                          failure_reason: list[str] | None = None) -> IssuedMiniAppSession | None:
    """Consume once and insert the session in the same SQLite write transaction."""
    digest = _digest(token)
    if digest is None or type(verified_user_id) is not int or verified_user_id <= 0:
        if failure_reason is not None:
            failure_reason.append("launch_token_invalid")
        return None
    timestamp = int(time.time()) if now is None else now
    session_token = secrets.token_urlsafe(32)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        if is_deleted_user_in_transaction(cursor, verified_user_id):
            if failure_reason is not None:
                failure_reason.append("deleted_user")
            return None
        row = cursor.execute(
            """SELECT chat_id, user_id, launch_message_id FROM miniapp_launch_tokens
               WHERE token_digest = ? AND consumed_at IS NULL AND expires_at > ?""",
            (digest, timestamp),
        ).fetchone()
        if row is None:
            if failure_reason is not None:
                state = cursor.execute(
                    """SELECT consumed_at, expires_at FROM miniapp_launch_tokens
                       WHERE token_digest = ?""", (digest,),
                ).fetchone()
                failure_reason.append(
                    "launch_token_not_found" if state is None else
                    "launch_token_consumed" if state[0] is not None else
                    "launch_token_expired" if state[1] <= timestamp else
                    "launch_token_unavailable"
                )
            return None
        if row[1] != verified_user_id:
            if failure_reason is not None:
                failure_reason.append("launch_token_wrong_user")
            return None
        cursor.execute(
            """UPDATE miniapp_launch_tokens SET consumed_at = ?
               WHERE token_digest = ? AND consumed_at IS NULL AND expires_at > ?""",
            (timestamp, digest, timestamp),
        )
        if cursor.rowcount != 1:
            if failure_reason is not None:
                failure_reason.append("launch_token_consumed")
            return None
        session = MiniAppSession(row[0], row[1], timestamp, timestamp + SESSION_TTL_SECONDS)
        cursor.execute(
            """INSERT INTO miniapp_sessions
               (token_digest, chat_id, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (_digest(session_token), session.chat_id, session.user_id,
             session.created_at, session.expires_at),
        )
        return IssuedMiniAppSession(session_token, session, row[2])


def get_miniapp_session(token: str, *, now: int | None = None) -> MiniAppSession | None:
    digest = _digest(token)
    if digest is None:
        return None
    timestamp = int(time.time()) if now is None else now
    with get_db() as conn:
        row = conn.execute(
            """SELECT chat_id, user_id, created_at, expires_at
               FROM miniapp_sessions WHERE token_digest = ? AND expires_at > ?""",
            (digest, timestamp),
        ).fetchone()
        return MiniAppSession(*row) if row else None
