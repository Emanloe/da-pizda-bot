import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
async def test_duel_event_publishers_skip_disabled_chat_before_rng(
    monkeypatch, temp_database, fake_context,
):
    from handlers import duel_items, huecrab, hyperborean_event
    from module_settings import set_module_enabled

    set_module_enabled(-1, "duel_random_events", False, 42)
    roll = Mock(side_effect=AssertionError("disabled event consumed RNG"))
    monkeypatch.setattr(hyperborean_event.random, "random", roll)
    monkeypatch.setattr(duel_items.random, "random", roll)
    monkeypatch.setattr(huecrab.random, "random", roll)
    await hyperborean_event._spawn_hyperboreic_huy(fake_context, -1)
    await duel_items._spawn_duel_item_event(fake_context, -1)
    await huecrab.spawn_huecrab_event(fake_context, -1)
    roll.assert_not_called()
    fake_context.bot.send_message.assert_not_awaited()

    # The next chat retains its usual event roll.
    monkeypatch.setattr(hyperborean_event.random, "random", Mock(return_value=1.0))
    hyperborean_event.ACTIVE_HYPERBOREAN_EVENTS.pop(-2, None)
    await hyperborean_event._spawn_hyperboreic_huy(fake_context, -2)
    hyperborean_event.random.random.assert_called_once()


@pytest.mark.asyncio
async def test_late_disable_prevents_new_event_publication(
    monkeypatch, temp_database, fake_context,
):
    from handlers import duel_items, huecrab, hyperborean_event

    hyperborean_event.ACTIVE_HYPERBOREAN_EVENTS.pop(-1, None)
    hyperborean_event.HYPERBOREAN_HUY_DAILY_SPAWNS.pop(-1, None)
    monkeypatch.setattr(hyperborean_event.random, "random", lambda: 0.0)
    monkeypatch.setattr(hyperborean_event.random, "choice", lambda values: values[0])
    states = iter([True, True, False])
    monkeypatch.setattr(hyperborean_event, "is_module_enabled", lambda *_: next(states))
    await hyperborean_event._spawn_hyperboreic_huy(fake_context, -1)
    assert -1 not in hyperborean_event.ACTIVE_HYPERBOREAN_EVENTS

    discarded = Mock()
    monkeypatch.setattr(duel_items, "create_duel_item_event", lambda _: 99)
    monkeypatch.setattr(duel_items, "discard_unpublished_duel_item_event", discarded)
    iter_states = iter([True, False])
    monkeypatch.setattr(duel_items, "is_module_enabled", lambda *_: next(iter_states))
    await duel_items._spawn_duel_item_event(fake_context, -1)
    discarded.assert_called_once_with(99)

    monkeypatch.setattr(huecrab, "has_active_huecrab_event", lambda _: False)
    huecrab.HUECRAB_DAILY_SPAWNS.pop(-1, None)
    monkeypatch.setattr(huecrab, "create_huecrab_event", lambda _: 88)
    discarded_crab = Mock()
    monkeypatch.setattr(huecrab, "discard_unpublished_huecrab_event", discarded_crab)
    crab_states = iter([True, False])
    monkeypatch.setattr(huecrab, "is_module_enabled", lambda *_: next(crab_states))
    await huecrab.spawn_huecrab_event(fake_context, -1)
    discarded_crab.assert_called_once_with(88)
    fake_context.bot.send_message.assert_not_awaited()


def test_legacy_duel_pocket_drop_skips_disabled_rng_and_inventory(
    monkeypatch, temp_database,
):
    from handlers import duel
    from module_settings import set_module_enabled

    set_module_enabled(-1, "duel_random_events", False, 42)
    inventory = Mock(side_effect=AssertionError("disabled pocket inspected inventory"))
    roll = Mock(side_effect=AssertionError("disabled pocket consumed RNG"))
    monkeypatch.setattr(duel, "get_duel_inventory", inventory)
    monkeypatch.setattr(duel.random, "random", roll)
    assert duel._maybe_drop_loser_inventory_item(-1, 2) is None
    inventory.assert_not_called()
    roll.assert_not_called()


@pytest.mark.asyncio
async def test_ordinary_duel_selection_remains_available_when_random_events_off(
    monkeypatch, temp_database, fake_context,
):
    import database
    from handlers import duel
    from module_settings import set_module_enabled

    clicker = SimpleNamespace(id=1, username="clicker", first_name="Clicker")
    opponent = SimpleNamespace(id=2, username="opponent", first_name="Opponent")
    database.get_or_create_duel_user(clicker, -1)
    database.get_or_create_duel_user(opponent, -1)
    set_module_enabled(-1, "duel_random_events", False, 42)
    sent = AsyncMock()
    monkeypatch.setattr(duel, "send_and_schedule", sent)
    message = SimpleNamespace(
        from_user=clicker, chat=SimpleNamespace(id=-1), chat_id=-1,
        message_id=77, text="/duel",
    )

    await duel.duel_command(SimpleNamespace(message=message), fake_context)

    sent.assert_awaited_once()
    keyboard = sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert [button.callback_data for row in keyboard for button in row] == ["start_duel_opponent"]


@pytest.mark.asyncio
async def test_existing_item_can_be_claimed_without_huegryz_roll(
    monkeypatch, temp_database, fake_context,
):
    import database as db
    from handlers import duel_items
    from module_settings import set_module_enabled
    from tests.test_duel_items import item_event_update, make_user

    chat_id = -771
    claimant = make_user(1, "claimant")
    db.get_or_create_duel_user(claimant, chat_id)
    event_id = db.create_duel_item_event(chat_id)
    assert db.set_duel_item_event_message(event_id, 777)
    set_module_enabled(chat_id, "duel_random_events", False, 42)
    monkeypatch.setattr(duel_items.random, "choice", Mock(return_value=duel_items.DUEL_ITEMS[0]))
    roll = Mock(side_effect=AssertionError("Huegryz consumed RNG while OFF"))
    monkeypatch.setattr(duel_items.random, "random", roll)

    await duel_items.duel_item_event_callback(
        item_event_update(chat_id, claimant, event_id)[0], fake_context,
    )

    assert len(db.get_duel_inventory(chat_id, claimant.id)) == 1
    assert db.get_duel_item_event(event_id)["claimed"]
    roll.assert_not_called()


@pytest.mark.asyncio
async def test_huecrab_autoloot_skips_disabled_chat(monkeypatch, temp_database, fake_context):
    from handlers import huecrab
    from module_settings import set_module_enabled

    set_module_enabled(-1, "duel_random_events", False, 42)
    monkeypatch.setattr(huecrab, "list_due_huecrab_item_events", lambda *_: [1])
    monkeypatch.setattr(huecrab, "get_duel_item_event", lambda _: {"chat_id": -1})
    monkeypatch.setattr(huecrab, "list_unannounced_huecrab_claims", lambda: [{"chat_id": -1}])
    claim = Mock(side_effect=AssertionError("disabled auto-loot claimed item"))
    monkeypatch.setattr(huecrab, "claim_due_item_for_huecrab", claim)
    await huecrab.huecrab_autoloot_job(fake_context)
    claim.assert_not_called()
    fake_context.bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_boss_auto_off_skips_selection_but_manual_start_still_works(
    monkeypatch, temp_database, fake_context,
):
    from handlers import duel
    from module_settings import set_module_enabled

    set_module_enabled(-1, "boss_auto", False, 42)
    duel.ACTIVE_BOSS_BATTLES.pop(-1, None)
    choose = Mock(side_effect=AssertionError("auto boss consumed RNG"))
    monkeypatch.setattr(duel.random, "choice", choose)
    assert await duel._start_boss_battle(fake_context, -1, include_registrations=True) is False
    choose.assert_not_called()
    fake_context.bot.send_message.assert_not_awaited()

    monkeypatch.setattr(duel, "get_all_chats", lambda: [-1, -2])
    monkeypatch.setattr(duel, "_boss_get_registered_chat_ids", lambda: [])
    monkeypatch.setattr(duel, "is_boss_enabled", lambda _: True)
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(duel, "_start_boss_battle", start)
    await duel.boss_daily_job(fake_context)
    start.assert_awaited_once_with(fake_context, -2, include_registrations=True)

    monkeypatch.setattr(duel, "ADMIN_IDS", {42})
    monkeypatch.setattr(duel, "set_boss_enabled", Mock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42), chat=SimpleNamespace(type="group"), chat_id=-1,
    )
    await duel.boss_command(SimpleNamespace(message=message), fake_context)
    start.assert_any_await(fake_context, -1, include_registrations=False)


@pytest.mark.asyncio
async def test_boss_auto_off_keeps_existing_battle_visible_to_miniapp(temp_database):
    from handlers import duel
    from handlers.boss_read_model import get_boss_battle_read_model
    from module_settings import set_module_enabled

    battle = {
        "battle_id": "manual", "boss": duel.BOSSES[0], "participants": {},
        "phase": "join", "round": 0, "hits": 0, "deadline_at": None,
        "lock": asyncio.Lock(),
    }
    duel.ACTIVE_BOSS_BATTLES[-1] = battle
    try:
        set_module_enabled(-1, "boss_auto", False, 42)
        model = await get_boss_battle_read_model(-1, 101)
        assert model["battle"]["battle_id"] == "manual"
        assert duel.ACTIVE_BOSS_BATTLES[-1] is battle
    finally:
        duel.ACTIVE_BOSS_BATTLES.pop(-1, None)


@pytest.mark.asyncio
async def test_monthly_summary_and_past_pizda_are_per_chat(
    monkeypatch, temp_database, fake_context,
):
    from handlers import monthly_summary, past_pizda
    from module_settings import set_module_enabled

    set_module_enabled(-1, "monthly_summary", False, 42)
    set_module_enabled(-1, "past_pizda", False, 42)
    monkeypatch.setattr(monthly_summary, "get_registered_duel_chat_ids", lambda: [-1, -2])
    monkeypatch.setattr(monthly_summary, "format_monthly_summary", lambda chat_id, _month: str(chat_id))
    await monthly_summary.monthly_summary_job(fake_context)
    assert fake_context.bot.send_message.await_args.kwargs["chat_id"] == -2
    assert fake_context.bot.send_message.await_count == 1

    roll = Mock(side_effect=AssertionError("disabled past_pizda consumed RNG"))
    monkeypatch.setattr(past_pizda.random, "randint", roll)
    await past_pizda.run_past_pizda_in_chat(fake_context, -1)
    roll.assert_not_called()
    monkeypatch.setattr(past_pizda.random, "randint", Mock(return_value=1))
    monkeypatch.setattr(past_pizda, "pick_pizda_candidates", lambda *_: [])
    await past_pizda.run_past_pizda_in_chat(fake_context, -2)
    past_pizda.random.randint.assert_called_once()


@pytest.mark.asyncio
async def test_chat_reactions_and_birthday_greetings_off_skip_publication_and_rng(
    monkeypatch, temp_database, fake_context, tg_user,
):
    from handlers import triggers
    from module_settings import set_module_enabled
    from tests.test_triggers import Message

    for module_id in ("chat_reactions", "birthday_greetings", "past_pizda"):
        set_module_enabled(-44, module_id, False, 42)
    now = datetime.now()
    fake_context.bot.get_chat.return_value = SimpleNamespace(
        birthdate=SimpleNamespace(day=now.day, month=now.month),
    )
    message = Message(tg_user, "да", chat_id=-44)
    roll = Mock(side_effect=AssertionError("disabled reaction consumed RNG"))
    monkeypatch.setattr(triggers.random, "random", roll)

    await triggers.respond_trigger(SimpleNamespace(message=message), fake_context)

    roll.assert_not_called()
    message.reply_text.assert_not_awaited()
    message.reply_animation.assert_not_awaited()

    other = Message(tg_user, "нет", chat_id=-45)
    monkeypatch.setattr(triggers.random, "random", Mock(return_value=0.0))
    await triggers.respond_trigger(SimpleNamespace(message=other), fake_context)
    other.reply_text.assert_awaited_once()
    other.reply_animation.assert_awaited_once()
