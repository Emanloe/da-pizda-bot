"""Daily moss choice, published by the existing Hyperborean event job."""

import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import (
    format_user_title, get_db, get_duel_user_by_id_in_transaction, get_or_create_duel_user,
)
from handlers.duel_messaging import schedule_auto_delete
from module_settings import is_module_enabled
from text_resources import get_text


MOSS_CHOICE_CHANCE = 0.05
MOSS_CHOICE_CALLBACK_PREFIX = "moss_choice:"


def _reserve_event(chat_id: int, event_date: str) -> int | None:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "INSERT OR IGNORE INTO moss_choice_events (chat_id, event_date) VALUES (?, ?)",
            (chat_id, event_date),
        )
        return cursor.lastrowid if cursor.rowcount == 1 else None


async def spawn_moss_choice_event(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, event_date: str,
) -> None:
    if not is_module_enabled(chat_id, "duel_random_events"):
        return
    with get_db() as conn:
        if conn.execute(
            "SELECT 1 FROM moss_choice_events WHERE chat_id = ? AND event_date = ?",
            (chat_id, event_date),
        ).fetchone():
            return
    if random.random() >= MOSS_CHOICE_CHANCE:
        return
    if not is_module_enabled(chat_id, "duel_random_events"):
        return

    event_id = _reserve_event(chat_id, event_date)
    if event_id is None:
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            get_text("moss_choice.buttons.clever"),
            callback_data=f"{MOSS_CHOICE_CALLBACK_PREFIX}{event_id}:clever",
        ),
        InlineKeyboardButton(
            get_text("moss_choice.buttons.wise"),
            callback_data=f"{MOSS_CHOICE_CALLBACK_PREFIX}{event_id}:wise",
        ),
    ]])
    try:
        message = await context.bot.send_message(
            chat_id=chat_id, text=get_text("moss_choice.spawn"),
            parse_mode="HTML", reply_markup=keyboard,
        )
    except Exception:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM moss_choice_events WHERE id = ? AND message_id IS NULL",
                (event_id,),
            )
        logging.exception("Could not publish moss choice in chat %s", chat_id)
        return
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE moss_choice_events SET message_id = ? WHERE id = ? AND message_id IS NULL",
                (message.message_id, event_id),
            )
    except Exception:
        logging.exception("Could not bind moss choice %s in chat %s", event_id, chat_id)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)
        except Exception:
            logging.exception("Could not remove unbound moss choice %s", event_id)


def _claim_event(event_id: int, chat_id: int, message_id: int, user_id: int, choice: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        from database import is_deleted_user_in_transaction
        if is_deleted_user_in_transaction(cursor, user_id):
            return "not_registered", None
        row = cursor.execute(
            "SELECT message_id, claimed_by FROM moss_choice_events WHERE id = ? AND chat_id = ?",
            (event_id, chat_id),
        ).fetchone()
        if row is None or row[0] != message_id or row[1] is not None:
            return "taken", None
        user = get_duel_user_by_id_in_transaction(cursor, chat_id, user_id)
        if user is None:
            return "not_registered", None
        if choice == "clever":
            cursor.execute(
                "UPDATE duel_users SET points = points + 100 WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
        else:
            cursor.execute(
                """UPDATE duel_users
                   SET dick_stolen_today = 0, last_stolen_by = NULL
                   WHERE chat_id = ? AND user_id = ? AND dick_stolen_today = 1""",
                (chat_id, user_id),
            )
        cursor.execute(
            """UPDATE moss_choice_events
               SET claimed_by = ?, choice = ?, claimed_at = CURRENT_TIMESTAMP
               WHERE id = ? AND claimed_by IS NULL""",
            (user_id, choice, event_id),
        )
        return "claimed", user


async def moss_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not query.message or not update.effective_chat:
        return
    from database import is_deleted_user
    if is_deleted_user(query.from_user.id):
        await query.answer(get_text("gnome_deletion.deleted"), show_alert=True)
        return
    try:
        prefix, event_id_text, choice = query.data.split(":")
        event_id = int(event_id_text)
        if prefix != "moss_choice" or choice not in {"clever", "wise"}:
            raise ValueError("Invalid moss choice callback")
    except (AttributeError, ValueError):
        await query.answer(get_text("moss_choice.alerts.taken"), show_alert=True)
        return

    chat_id = update.effective_chat.id
    message_id = query.message.message_id
    try:
        status, user = _claim_event(event_id, chat_id, message_id, query.from_user.id, choice)
        if status == "not_registered":
            get_or_create_duel_user(query.from_user, chat_id)
            status, user = _claim_event(event_id, chat_id, message_id, query.from_user.id, choice)
    except Exception:
        logging.exception("Could not claim moss choice %s in chat %s", event_id, chat_id)
        await query.answer(get_text("moss_choice.alerts.error"), show_alert=True)
        return
    if status != "claimed":
        await query.answer(get_text(f"moss_choice.alerts.{status}"), show_alert=True)
        return

    try:
        await query.answer()
    except Exception:
        logging.exception("Could not answer claimed moss choice %s", event_id)
    text = get_text(
        f"moss_choice.result.{choice}", title=format_user_title(user),
    )
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text,
            parse_mode="HTML", reply_markup=None,
        )
    except Exception:
        logging.exception("Could not edit claimed moss choice %s in chat %s", event_id, chat_id)
    try:
        schedule_auto_delete(context, chat_id, [message_id], delay=60)
    except Exception:
        logging.exception("Could not schedule moss choice %s deletion in chat %s", event_id, chat_id)
