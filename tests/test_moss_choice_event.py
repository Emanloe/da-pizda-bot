import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


CHAT_A = -701
CHAT_B = -702
DAY = "2026-10-03"


def _update(chat_id, user, event_id, choice, message_id=101):
    query = SimpleNamespace(
        data=f"moss_choice:{event_id}:{choice}", from_user=user,
        message=SimpleNamespace(message_id=message_id), answer=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query, effective_chat=SimpleNamespace(id=chat_id),
    ), query


def _row(db_path, chat_id, day=DAY):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT id, message_id, claimed_by, choice FROM moss_choice_events "
            "WHERE chat_id = ? AND event_date = ?", (chat_id, day),
        ).fetchone()


def _user(db_path, chat_id, user_id):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT points, dick_stolen_today, last_stolen_by FROM duel_users "
            "WHERE chat_id = ? AND user_id = ?", (chat_id, user_id),
        ).fetchone()


async def test_daily_spawn_is_per_chat_and_persists_after_restart(
    temp_database, fake_context, monkeypatch,
):
    from handlers import moss_choice_event as event

    monkeypatch.setattr(event.random, "random", Mock(return_value=0.0))
    await event.spawn_moss_choice_event(fake_context, CHAT_A, DAY)
    await event.spawn_moss_choice_event(fake_context, CHAT_A, DAY)
    await event.spawn_moss_choice_event(fake_context, CHAT_B, DAY)
    assert fake_context.bot.send_message.await_count == 2
    assert _row(temp_database, CHAT_A)[1:] == (101, None, None)
    assert _row(temp_database, CHAT_B)[1:] == (101, None, None)
    assert event.random.random.call_count == 2
    keyboard = fake_context.bot.send_message.await_args.kwargs["reply_markup"]
    assert [button.text for button in keyboard.inline_keyboard[0]] == ["С умом", "Мудро"]
    assert fake_context.job_queue.calls == []

    await event.spawn_moss_choice_event(fake_context, CHAT_A, "2026-10-04")
    assert fake_context.bot.send_message.await_count == 3
    with sqlite3.connect(temp_database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM moss_choice_events").fetchone()[0] == 3


async def test_existing_daily_job_checks_moss_in_each_chat(fake_context, monkeypatch):
    from datetime import date
    from handlers import hyperborean_event

    old_event = AsyncMock()
    moss_event = AsyncMock()
    monkeypatch.setattr(hyperborean_event, "get_all_chats", lambda: [CHAT_A, CHAT_B])
    monkeypatch.setattr(hyperborean_event, "_current_date", lambda: date(2026, 10, 3))
    monkeypatch.setattr(hyperborean_event, "_spawn_hyperboreic_huy", old_event)
    monkeypatch.setattr(hyperborean_event, "spawn_moss_choice_event", moss_event)

    await hyperborean_event.hyperboreic_huy_daily_job(fake_context)
    assert old_event.await_count == 2
    assert [call.args for call in moss_event.await_args_list] == [
        (fake_context, CHAT_A, DAY), (fake_context, CHAT_B, DAY),
    ]


async def test_clever_claim_adds_100_once_and_deletes_only_after_claim(
    temp_database, fake_context, tg_user, monkeypatch,
):
    from database import get_or_create_duel_user
    from handlers import moss_choice_event as event
    from handlers.duel_messaging import delete_messages_job

    get_or_create_duel_user(tg_user, CHAT_A)
    monkeypatch.setattr(event.random, "random", Mock(return_value=0.0))
    await event.spawn_moss_choice_event(fake_context, CHAT_A, DAY)
    event_id = _row(temp_database, CHAT_A)[0]
    assert fake_context.job_queue.calls == []

    update, query = _update(CHAT_A, tg_user, event_id, "clever")
    await event.moss_choice_callback(update, fake_context)
    assert _user(temp_database, CHAT_A, tg_user.id)[0] == 120
    assert _row(temp_database, CHAT_A)[2:] == (tg_user.id, "clever")
    assert "100 очков" in fake_context.bot.edit_message_text.await_args.kwargs["text"]
    assert fake_context.bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    query.answer.assert_awaited_once_with()
    assert len(fake_context.job_queue.calls) == 1
    callback, delay, kwargs = fake_context.job_queue.calls[0]
    assert callback is delete_messages_job and delay == 60
    assert kwargs["data"] == {"chat_id": CHAT_A, "message_ids": [101]}
    fake_context.job.data = kwargs["data"]
    await callback(fake_context)
    fake_context.bot.delete_message.assert_awaited_once_with(chat_id=CHAT_A, message_id=101)

    await event.moss_choice_callback(update, fake_context)
    assert _user(temp_database, CHAT_A, tg_user.id)[0] == 120
    assert fake_context.bot.edit_message_text.await_count == 1
    assert len(fake_context.job_queue.calls) == 1


@pytest.mark.parametrize("stolen", [False, True])
async def test_wise_claim_restores_only_stolen_dick_and_always_says_exact_phrase(
    temp_database, fake_context, tg_user, monkeypatch, stolen,
):
    from database import get_or_create_duel_user
    from handlers import moss_choice_event as event

    get_or_create_duel_user(tg_user, CHAT_A)
    with sqlite3.connect(temp_database) as conn:
        conn.execute(
            "UPDATE duel_users SET points = 73, dick_stolen_today = ?, last_stolen_by = ? "
            "WHERE chat_id = ? AND user_id = ?",
            (int(stolen), "thief" if stolen else None, CHAT_A, tg_user.id),
        )
    monkeypatch.setattr(event.random, "random", Mock(return_value=0.0))
    await event.spawn_moss_choice_event(fake_context, CHAT_A, DAY)
    update, _ = _update(CHAT_A, tg_user, _row(temp_database, CHAT_A)[0], "wise")
    await event.moss_choice_callback(update, fake_context)
    assert _user(temp_database, CHAT_A, tg_user.id) == (73, 0, None)
    assert "КХЪ ЪЕЪ" in fake_context.bot.edit_message_text.await_args.kwargs["text"]
    assert len(fake_context.job_queue.calls) == 1


async def test_wrong_chat_and_message_cannot_claim_but_valid_new_user_can(
    temp_database, fake_context, tg_user, monkeypatch,
):
    from handlers import moss_choice_event as event

    monkeypatch.setattr(event.random, "random", Mock(return_value=0.0))
    await event.spawn_moss_choice_event(fake_context, CHAT_A, DAY)
    event_id = _row(temp_database, CHAT_A)[0]
    for chat_id, message_id in ((CHAT_B, 101), (CHAT_A, 999)):
        update, query = _update(chat_id, tg_user, event_id, "clever", message_id)
        await event.moss_choice_callback(update, fake_context)
        query.answer.assert_awaited_once()
    assert _row(temp_database, CHAT_A)[2] is None
    assert _user(temp_database, CHAT_A, tg_user.id) is None
    fake_context.bot.edit_message_text.assert_not_awaited()
    assert fake_context.job_queue.calls == []

    update, _ = _update(CHAT_A, tg_user, event_id, "clever")
    await event.moss_choice_callback(update, fake_context)
    assert _row(temp_database, CHAT_A)[2] == tg_user.id
    assert _user(temp_database, CHAT_A, tg_user.id)[0] == 120


async def test_concurrent_callbacks_choose_one_winner_and_keep_chats_separate(
    temp_database, fake_context, tg_user, monkeypatch,
):
    from database import get_or_create_duel_user
    from handlers import moss_choice_event as event

    second = SimpleNamespace(id=2002, username="second", first_name="Second")
    for chat_id in (CHAT_A, CHAT_B):
        get_or_create_duel_user(tg_user, chat_id)
        get_or_create_duel_user(second, chat_id)
    monkeypatch.setattr(event.random, "random", Mock(return_value=0.0))
    await event.spawn_moss_choice_event(fake_context, CHAT_A, DAY)
    await event.spawn_moss_choice_event(fake_context, CHAT_B, DAY)
    event_a = _row(temp_database, CHAT_A)[0]
    event_b = _row(temp_database, CHAT_B)[0]

    results = await asyncio.gather(
        asyncio.to_thread(event._claim_event, event_a, CHAT_A, 101, tg_user.id, "clever"),
        asyncio.to_thread(event._claim_event, event_a, CHAT_A, 101, second.id, "wise"),
    )
    assert sorted(status for status, _ in results) == ["claimed", "taken"]
    assert _row(temp_database, CHAT_A)[2] in (tg_user.id, second.id)
    if _row(temp_database, CHAT_A)[3] == "clever":
        assert _user(temp_database, CHAT_A, tg_user.id)[0] == 120
        assert _user(temp_database, CHAT_A, second.id)[0] == 20
    else:
        assert _user(temp_database, CHAT_A, tg_user.id)[0] == 20
        assert _user(temp_database, CHAT_A, second.id)[0] == 20

    update, _ = _update(CHAT_B, second, event_b, "wise")
    await event.moss_choice_callback(update, fake_context)
    assert _row(temp_database, CHAT_B)[2:] == (second.id, "wise")
    assert fake_context.job_queue.calls[0][2]["data"]["chat_id"] == CHAT_B
