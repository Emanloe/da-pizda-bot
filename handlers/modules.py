"""Admin-only Telegram controls for per-chat automatic activity."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from module_settings import (
    MODULE_CATALOG, is_module_enabled, toggle_module_enabled,
)
from handlers.utils import is_admin
from text_resources import get_text


def _menu(chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    for module_id, label_key in MODULE_CATALOG.items():
        state_key = "enabled" if is_module_enabled(chat_id, module_id) else "disabled"
        rows.append([InlineKeyboardButton(
            get_text("modules.button", label=get_text(label_key),
                     state=get_text(f"modules.{state_key}")),
            callback_data=f"module_toggle:{module_id}",
        )])
    rows.append([InlineKeyboardButton(get_text("modules.close"), callback_data="module_close")])
    return InlineKeyboardMarkup(rows)


async def modules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    if not is_admin(getattr(update.effective_user, "id", None)):
        await context.bot.send_message(
            chat_id=chat.id, text=get_text("modules.forbidden"),
        )
        return
    if chat.type not in {"group", "supergroup"}:
        await context.bot.send_message(chat_id=chat.id, text=get_text("modules.group_only"))
        return
    await context.bot.send_message(
        chat_id=chat.id, text=get_text("modules.title"), reply_markup=_menu(chat.id),
    )


async def modules_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if not is_admin(getattr(update.effective_user, "id", None)):
        await query.answer(get_text("modules.forbidden"))
        return
    message = query.message
    chat = getattr(message, "chat", None)
    if chat is None or chat.type not in {"group", "supergroup"}:
        await query.answer(get_text("modules.group_only"))
        return
    if query.data == "module_close":
        await context.bot.edit_message_reply_markup(
            chat_id=chat.id, message_id=message.message_id, reply_markup=None,
        )
        await query.answer()
        return
    module_id = (query.data or "").removeprefix("module_toggle:")
    if not query.data or not query.data.startswith("module_toggle:") or module_id not in MODULE_CATALOG:
        await query.answer(get_text("modules.unknown"))
        return
    toggle_module_enabled(chat.id, module_id, update.effective_user.id)
    await context.bot.edit_message_text(
        chat_id=chat.id, message_id=message.message_id,
        text=get_text("modules.title"), reply_markup=_menu(chat.id),
    )
    await query.answer()
