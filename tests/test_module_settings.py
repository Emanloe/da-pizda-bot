import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_defaults_are_read_only_and_chat_overrides_persist(temp_database):
    from module_settings import MODULE_CATALOG, is_module_enabled, set_module_enabled

    with sqlite3.connect(temp_database) as conn:
        before = conn.execute("SELECT COUNT(*) FROM chat_module_settings").fetchone()[0]
    for _ in range(3):
        assert is_module_enabled(-1, "duel_random_events")
    with sqlite3.connect(temp_database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_module_settings").fetchone()[0] == before

    set_module_enabled(-1, "duel_random_events", False, 42)
    assert not is_module_enabled(-1, "duel_random_events")
    assert is_module_enabled(-2, "duel_random_events")
    assert is_module_enabled(-1, "boss_auto")
    set_module_enabled(-1, "duel_random_events", True, 42)
    assert is_module_enabled(-1, "duel_random_events")
    assert "pidor" not in " ".join(MODULE_CATALOG).lower()
    with pytest.raises(ValueError, match="Unknown module"):
        is_module_enabled(-1, "pidor")
    with pytest.raises(ValueError, match="Unknown module"):
        set_module_enabled(-1, "unknown", False, 42)


def test_toggle_is_atomic_and_schema_migration_is_idempotent(temp_database):
    from database import init_db
    from module_settings import is_module_enabled, toggle_module_enabled

    assert toggle_module_enabled(-1, "boss_auto", 42) is False
    assert toggle_module_enabled(-1, "boss_auto", 43) is True
    init_db()
    assert is_module_enabled(-1, "boss_auto")
    with sqlite3.connect(temp_database) as conn:
        assert conn.execute(
            "SELECT enabled, updated_by FROM chat_module_settings WHERE chat_id = -1 AND module_id = 'boss_auto'"
        ).fetchone() == (1, 43)


def test_concurrent_toggle_callbacks_produce_valid_boolean_state(temp_database):
    from module_settings import is_module_enabled, toggle_module_enabled

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: toggle_module_enabled(-1, "boss_auto", 42), range(2)))
    assert sorted(results) == [False, True]
    assert is_module_enabled(-1, "boss_auto")


@pytest.mark.parametrize("raw, expected", [
    ("123", {123}),
    ("123,456", {123, 456}),
    (" 123 , 456 ", {123, 456}),
    ("", set()),
    ("123,bad", {123}),
    ("123,", {123}),
])
def test_existing_admin_ids_parsing(monkeypatch, raw, expected):
    import config

    try:
        with monkeypatch.context() as patch:
            patch.setenv("ADMIN_IDS", raw)
            assert importlib.reload(config).ADMIN_IDS == expected
    finally:
        importlib.reload(config)


def test_existing_is_admin_uses_numeric_ids(monkeypatch):
    from handlers import utils

    monkeypatch.setattr(utils, "ADMIN_IDS", {123, 456})
    assert utils.is_admin(123)
    assert utils.is_admin(456)
    assert not utils.is_admin(789)
    assert not utils.is_admin("123")
    monkeypatch.setattr(utils, "ADMIN_IDS", set())
    assert not utils.is_admin(123)


def _update(chat_id, chat_type, user_id, *, callback=None, message_chat_id=None):
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    user = SimpleNamespace(id=user_id)
    if callback is None:
        return SimpleNamespace(effective_chat=chat, effective_user=user, callback_query=None)
    message_chat = SimpleNamespace(id=message_chat_id or chat_id, type=chat_type)
    query = SimpleNamespace(
        data=callback,
        message=SimpleNamespace(chat=message_chat, message_id=77),
        answer=AsyncMock(),
    )
    return SimpleNamespace(effective_chat=chat, effective_user=user, callback_query=query)


@pytest.mark.asyncio
async def test_modules_command_and_callback_require_server_allowlist(monkeypatch, temp_database, fake_context):
    from handlers.modules import modules_callback, modules_command
    from handlers import utils
    from module_settings import is_module_enabled

    monkeypatch.setattr(utils, "ADMIN_IDS", {42})
    unauthorized = _update(-1, "group", 99)
    await modules_command(unauthorized, fake_context)
    assert "reply_markup" not in fake_context.bot.send_message.await_args.kwargs

    unauthorized_button = _update(-1, "group", 99, callback="module_toggle:boss_auto")
    await modules_callback(unauthorized_button, fake_context)
    assert is_module_enabled(-1, "boss_auto")
    fake_context.bot.send_message.reset_mock()

    admin = _update(-1, "group", 42)
    await modules_command(admin, fake_context)
    keyboard = fake_context.bot.send_message.await_args.kwargs["reply_markup"]
    callback_data = [row[0].callback_data for row in keyboard.inline_keyboard]
    assert "module_toggle:duel_random_events" in callback_data
    assert "module_toggle:boss_auto" in callback_data
    assert "module_close" in callback_data
    assert all("pidor" not in value for value in callback_data)

    fake_context.bot.edit_message_text = AsyncMock()
    button = _update(-999, "group", 42, callback="module_toggle:boss_auto", message_chat_id=-1)
    await modules_callback(button, fake_context)
    assert not is_module_enabled(-1, "boss_auto")
    assert is_module_enabled(-999, "boss_auto")
    assert fake_context.bot.edit_message_text.await_args.kwargs["chat_id"] == -1
    await modules_callback(button, fake_context)
    assert is_module_enabled(-1, "boss_auto")


@pytest.mark.asyncio
async def test_private_and_unknown_callbacks_cannot_mutate(monkeypatch, temp_database, fake_context):
    from handlers.modules import modules_callback, modules_command
    from handlers import utils
    from module_settings import is_module_enabled

    monkeypatch.setattr(utils, "ADMIN_IDS", {42})
    await modules_command(_update(42, "private", 42), fake_context)
    assert "reply_markup" not in fake_context.bot.send_message.await_args.kwargs
    await modules_callback(
        _update(42, "private", 42, callback="module_toggle:boss_auto"), fake_context
    )
    await modules_callback(
        _update(-1, "group", 42, callback="module_toggle:pidor"), fake_context
    )
    assert is_module_enabled(42, "boss_auto")
    assert is_module_enabled(-1, "boss_auto")


@pytest.mark.asyncio
async def test_empty_existing_admin_ids_grants_no_module_access(
    monkeypatch, temp_database, fake_context,
):
    from handlers import utils
    from handlers.modules import modules_callback, modules_command
    from module_settings import is_module_enabled

    monkeypatch.setattr(utils, "ADMIN_IDS", set())
    await modules_command(_update(-1, "group", 42), fake_context)
    await modules_callback(
        _update(-1, "group", 42, callback="module_toggle:boss_auto"), fake_context,
    )
    assert is_module_enabled(-1, "boss_auto")
    assert "reply_markup" not in fake_context.bot.send_message.await_args.kwargs


@pytest.mark.asyncio
async def test_pidor_daily_and_manual_ignore_every_toggle(monkeypatch, temp_database, fake_context):
    from handlers import commands, game
    from module_settings import MODULE_CATALOG, set_module_enabled

    for module_id in MODULE_CATALOG:
        set_module_enabled(-1, module_id, False, 42)
    calls = []
    monkeypatch.setattr(game, "get_all_chats", lambda: [-1])
    monkeypatch.setattr(game, "run_pidor_game_in_chat", AsyncMock(side_effect=lambda _context, chat_id: calls.append(chat_id)))
    await game.daily_beauty_job(fake_context)
    assert calls == [-1]

    monkeypatch.setattr(commands, "is_admin", lambda _user_id: True)
    monkeypatch.setattr(commands, "pick_beauty_of_the_day", lambda _chat_id: ("tester", 3))
    monkeypatch.setattr(commands, "reply_or_send", AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), chat_id=-1, message_id=77)
    await commands.force_pidor_command(SimpleNamespace(message=message), fake_context)
    commands.reply_or_send.assert_awaited_once()
