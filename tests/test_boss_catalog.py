"""One future battle has one persisted boss identity and its own HP."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import database as db
from handlers import boss_catalog, boss_registration, duel
from handlers.boss_read_model import _public_boss
from text_resources import _get_resource_value


@pytest.fixture
def prepared_world(temp_database, monkeypatch):
    monkeypatch.setattr(boss_registration, "_BOSS_REG_DB_PATH", temp_database)
    duel.ACTIVE_BOSS_BATTLES.clear()
    duel._BOSS_START_LOCKS.clear()
    yield temp_database
    for battle in duel.ACTIVE_BOSS_BATTLES.values():
        task = battle.get("phase_task")
        if task is not None:
            task.cancel()
    duel.ACTIVE_BOSS_BATTLES.clear()
    duel._BOSS_START_LOCKS.clear()


def _user(user_id):
    return SimpleNamespace(id=user_id, username=f"user{user_id}",
                           first_name="User", last_name=None)


def _generated(name="Мрак Танцор Молот", hp=5):
    return {"id": "generated", "name": name, "emoji": "👹",
            "description": None, "max_hp": hp}


def test_yaml_catalog_has_only_skier_and_three_complete_name_pools():
    catalog = _get_resource_value("boss.catalog")
    assert list(catalog) == ["chizyanovsky_skier"]
    assert catalog["chizyanovsky_skier"]["hp"] == 8
    assert boss_catalog.BOSS_CATALOG_IDS == ("chizyanovsky_skier",)
    assert boss_catalog.UNIQUE_BOSSES[0]["description"] == (
        "Любит всратые фигурки, не любит когда их роняют"
    )
    assert [len(parts) for parts in boss_catalog.NAME_PARTS] == [53, 69, 25]
    assert all(word in pool for word, pool in zip(
        ("Гадючий", "Танцор", "Колотитель"), boss_catalog.NAME_PARTS,
    ))
    assert all(word in pool for word, pool in zip(
        ("Мрак", "Похоть", "Холодный"), boss_catalog.NAME_PARTS,
    ))
    assert boss_catalog.UNIQUE_CHANCE == 0.2


def test_unique_branch_uses_configured_hp_and_description():
    rng = Mock()
    rng.random.return_value = 0.0
    rng.choice.side_effect = lambda choices: choices[0]
    boss = boss_catalog.choose_boss(rng)
    assert boss == boss_catalog.UNIQUE_BOSSES[0]
    assert boss["max_hp"] == 8
    assert _public_boss(boss)["description"] == boss["description"]
    rng.choice.assert_called_once_with(boss_catalog.UNIQUE_BOSSES)
    rng.randint.assert_not_called()


@pytest.mark.parametrize("starting_hits,expected_victory", [(6, False), (7, True)])
def test_skier_round_uses_eight_hit_threshold(starting_hits, expected_victory):
    from handlers.boss_state import _apply_boss_round_result
    from tests.test_boss_flow import make_battle, make_participant

    player = make_participant(1, attack="head", block="head")
    battle = make_battle([player], phase="block", hits=starting_hits,
                         round_num=1, boss_attack="body", boss_block="dick")
    battle["boss"] = dict(boss_catalog.UNIQUE_BOSSES[0])
    result = _apply_boss_round_result(battle, boss_catalog.boss_required_hits(battle))
    assert battle["hits"] == starting_hits + 1
    assert result["victory"] is expected_victory


@pytest.mark.parametrize("hp", [3, 4, 5, 6, 7])
def test_generated_branch_picks_three_independent_parts_and_hp(hp):
    rng = Mock()
    rng.random.return_value = 1.0
    rng.choice.side_effect = lambda choices: choices[0]
    rng.randint.return_value = hp
    boss = boss_catalog.choose_boss(rng)
    words = boss["name"].split()
    assert len(words) == 3
    assert all(word in pool for word, pool in zip(words, boss_catalog.NAME_PARTS))
    assert boss == _generated(" ".join(words), hp)
    assert _public_boss(boss)["description"] is None
    assert [call.args[0] for call in rng.choice.call_args_list] == list(
        boss_catalog.NAME_PARTS
    )
    rng.randint.assert_called_once_with(3, 7)


def test_registration_persists_boss_once_and_deleted_user_does_not_prepare(
    prepared_world, monkeypatch,
):
    assert db.delete_gnome(3, "user3")
    choose = Mock(return_value=_generated())
    monkeypatch.setattr(boss_registration, "choose_boss", lambda _: choose())
    assert boss_registration._boss_register_user(-1, _user(3)) is False
    choose.assert_not_called()
    assert boss_registration._boss_register_user(-1, _user(1)) is True
    assert boss_registration._boss_register_user(-1, _user(2)) is True
    choose.assert_called_once_with()
    assert boss_registration._boss_prepare_next_boss(
        -1, Mock(side_effect=AssertionError("rerolled"))
    ) == _generated()
    with sqlite3.connect(prepared_world) as conn:
        assert conn.execute("SELECT COUNT(*) FROM boss_next_battles").fetchone() == (1,)


def test_consumed_boss_is_not_kept_for_a_later_battle(prepared_world):
    first = _generated("Мрак Танцор Молот", 3)
    second = _generated("Гадючий Танцор Колотитель", 7)
    assert boss_registration._boss_prepare_next_boss(-2, lambda: first) == first
    assert boss_registration._boss_consume_registrations(-2) == []
    with sqlite3.connect(prepared_world) as conn:
        assert conn.execute("SELECT COUNT(*) FROM boss_next_battles").fetchone() == (0,)
    assert boss_registration._boss_prepare_next_boss(-2, lambda: second) == second


@pytest.mark.asyncio
async def test_prepared_boss_survives_restart_and_start_does_not_reroll(
    prepared_world, fake_context, monkeypatch,
):
    selected = _generated("Гадючий Танцор Колотитель", 6)
    selector = Mock(return_value=selected)
    assert boss_registration._boss_prepare_next_boss(-11, selector) == selected
    selector.assert_called_once_with()
    # A new connection simulates a process restart: no runtime boss object is reused.
    assert boss_registration._boss_prepare_next_boss(
        -11, Mock(side_effect=AssertionError("rerolled after restart"))
    ) == selected
    monkeypatch.setattr(boss_registration, "choose_boss",
                        Mock(side_effect=AssertionError("rerolled during start")))
    assert await duel._start_boss_battle(fake_context, -11)
    battle = duel.ACTIVE_BOSS_BATTLES[-11]
    assert battle["boss"] == selected
    assert duel.boss_required_hits(battle) == 6
    assert "<b>6 раз</b>" in fake_context.bot.send_message.await_args.kwargs["text"]
    with sqlite3.connect(prepared_world) as conn:
        assert conn.execute("SELECT COUNT(*) FROM boss_next_battles").fetchone() == (0,)


@pytest.mark.asyncio
async def test_two_chats_keep_independent_prepared_bosses(
    prepared_world, fake_context,
):
    first = _generated("Мрак Танцор Молот", 3)
    second = dict(boss_catalog.UNIQUE_BOSSES[0])
    boss_registration._boss_prepare_next_boss(-21, lambda: first)
    boss_registration._boss_prepare_next_boss(-22, lambda: second)
    assert await duel._start_boss_battle(fake_context, -21)
    assert duel.ACTIVE_BOSS_BATTLES[-21]["boss"] == first
    assert boss_registration._boss_prepare_next_boss(
        -22, Mock(side_effect=AssertionError("other chat rerolled"))
    ) == second
    assert await duel._start_boss_battle(fake_context, -22)
    assert duel.ACTIVE_BOSS_BATTLES[-22]["boss"] == second
    assert duel.boss_required_hits(duel.ACTIVE_BOSS_BATTLES[-21]) == 3
    assert duel.boss_required_hits(duel.ACTIVE_BOSS_BATTLES[-22]) == 8


@pytest.mark.asyncio
async def test_final_report_does_not_store_future_boss_until_signup(
    prepared_world, fake_context, monkeypatch,
):
    from tests.test_boss_flow import make_battle

    selected = _generated(hp=4)
    choose = Mock(return_value=selected)
    monkeypatch.setattr(boss_registration, "choose_boss", lambda _: choose())
    monkeypatch.setattr(duel, "_boss_final_report", lambda *_: "Итог боя")
    battle = make_battle([], phase="resolving")
    await duel._boss_send_final_report(fake_context, -41, battle, victory=False)
    choose.assert_not_called()
    with boss_registration._boss_registration_connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM boss_next_battles").fetchone() == (0,)
    button = fake_context.bot.edit_message_text.await_args.kwargs[
        "reply_markup"
    ].inline_keyboard[0][0]
    assert button.callback_data == "boss_reg_next"
    assert boss_registration._boss_register_user(-41, _user(1))
    choose.assert_called_once_with()
    assert boss_registration._boss_prepare_next_boss(
        -41, Mock(side_effect=AssertionError("signup boss rerolled"))
    ) == selected


@pytest.mark.asyncio
async def test_manual_start_without_signup_generates_its_own_boss(
    prepared_world, fake_context, monkeypatch,
):
    selected = _generated("Гадючий Танцор Колотитель", 7)
    choose = Mock(return_value=selected)
    monkeypatch.setattr(boss_registration, "choose_boss", lambda _: choose())
    assert await duel._start_boss_battle(fake_context, -42, include_registrations=False)
    choose.assert_called_once_with()
    assert duel.ACTIVE_BOSS_BATTLES[-42]["boss"] == selected
    assert "<b>7 раз</b>" in fake_context.bot.send_message.await_args.kwargs["text"]
    with sqlite3.connect(prepared_world) as conn:
        assert conn.execute("SELECT COUNT(*) FROM boss_next_battles").fetchone() == (0,)


def test_additive_table_and_old_pending_queue_are_safe(prepared_world):
    with sqlite3.connect(prepared_world) as conn:
        conn.execute(boss_registration._NEXT_TABLE_SQL)
        conn.execute("""INSERT INTO boss_next_registrations
            (chat_id, user_id, username, first_name)
            VALUES (-31, 1, 'old', 'Old')""")
        conn.execute("""INSERT INTO boss_battle_results
            (chat_id, battle_id, boss_id, boss_name, finished_at, outcome,
             hits, required_hits, rounds, participants_json)
            VALUES (-31, 'old-result', 'deep_snouted_baron', 'Глубокорылый Барон',
                    1, 'victory', 5, 5, 1, '[]')""")
    selected = _generated(hp=7)
    assert boss_registration._boss_prepare_next_boss(-31, lambda: selected) == selected
    assert boss_registration._boss_prepare_next_boss(
        -31, Mock(side_effect=AssertionError("legacy pending rerolled"))
    ) == selected
    assert [row[0] for row in boss_registration._boss_get_registered_users(-31)] == [1]
    with sqlite3.connect(prepared_world) as conn:
        assert conn.execute(
            "SELECT boss_name, required_hits FROM boss_battle_results WHERE battle_id='old-result'"
        ).fetchone() == ("Глубокорылый Барон", 5)
        assert conn.execute("PRAGMA table_info(boss_next_battles)").fetchall()


def test_old_runtime_battle_keeps_five_hit_fallback():
    assert boss_catalog.boss_required_hits({"boss": {"name": "Старый босс"}}) == 5
