"""Generated item names stay stable across the existing loot claim paths."""

import importlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import database as db
from handlers import duel_items
from loot import catalog
from loot.translit import transliterate


def _register(chat_id, user_id):
    user = SimpleNamespace(
        id=user_id, username=f"user{user_id}", first_name=f"User {user_id}",
        last_name=None, is_bot=False,
    )
    db.get_or_create_duel_user(user, chat_id)


def _published_event(chat_id, *, item_id=None):
    event_id = db.create_duel_item_event(chat_id)
    assert event_id is not None
    assert db.set_duel_item_event_message(event_id, event_id + 500, "Находка")
    if item_id is not None:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE duel_item_events SET item_id = ? WHERE event_id = ?",
                (item_id, event_id),
            )
    return event_id


def test_catalog_data_and_validation(tmp_path):
    assert len(catalog.QUALITIES) == 5
    assert len(catalog.ADJECTIVES) == 313
    assert len(catalog.BASES) == 621
    assert len(catalog.SOURCES) == 244
    assert all(item["weight"] > 0 for group in (
        catalog.QUALITIES, catalog.ADJECTIVES, catalog.BASES, catalog.SOURCES,
    ) for item in group)
    assert all(all(item[form] for form in ("m", "f", "n", "pl"))
               for group in (catalog.QUALITIES, catalog.ADJECTIVES)
               for item in group)

    quality = dict(catalog.QUALITIES[0])
    quality["weight"] = 0
    invalid = tmp_path / "qualities.json"
    invalid.write_text(json.dumps([quality], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="weight"):
        catalog._load_qualities(invalid)
    quality["weight"] = 1
    invalid.write_text(json.dumps([quality, quality], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate quality id"):
        catalog._load_qualities(invalid)
    invalid.write_text("<html>not json</html>", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        catalog._load_qualities(invalid)


@pytest.mark.parametrize("loader,entries,invalid_field,error", [
    (catalog._load_bases, catalog.BASES, "weight", "weight"),
    (catalog._load_adjectives, catalog.ADJECTIVES, "m", "form"),
    (catalog._load_sources, catalog.SOURCES, "weight", "weight"),
])
def test_other_catalogs_reject_invalid_rows_and_duplicate_ids(
    tmp_path, loader, entries, invalid_field, error,
):
    row = dict(entries[0])
    row[invalid_field] = 0 if invalid_field == "weight" else ""
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        loader(path)
    path.write_text(json.dumps([entries[0], entries[0]], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        loader(path)


@pytest.mark.parametrize("second_roll,expected_count", [(0.0, 2), (1.0, 1)])
def test_generator_controlled_rng_and_second_adjective(
    monkeypatch, second_roll, expected_count,
):
    generator = importlib.import_module("loot.generate")
    monkeypatch.setattr(generator, "BASES", (
        {"id": "base", "name": "палка", "list": "f", "kind": "оружие", "weight": 3},
    ))
    monkeypatch.setattr(generator, "QUALITIES", (
        {"id": "quality", "tier": 2, "weight": 2, "f": "Пробная"},
    ))
    monkeypatch.setattr(generator, "ADJECTIVES", (
        {"id": "first", "weight": 4, "f": "первая"},
        {"id": "second", "weight": 1, "f": "вторая"},
    ))
    monkeypatch.setattr(generator, "SOURCES", (
        {"id": "source", "name": "снега", "weight": 5},
    ))

    class ControlledRng:
        def __init__(self):
            self.calls = []

        def choices(self, items, *, weights, k):
            self.calls.append(("choices", tuple(weights), k))
            return [items[0]]

        def random(self):
            self.calls.append(("random",))
            return second_roll

    rng = ControlledRng()
    item = generator.generate(rng)
    assert len(item["adjective_ids"]) == expected_count
    assert item["name"] == (
        "Пробная первая вторая палка снега" if expected_count == 2
        else "Пробная первая палка снега"
    )
    assert item["item_id"] == transliterate(item["name"])
    assert item["tier"] == 2 and item["kind"] == "оружие"
    assert [call[0] for call in rng.calls] == (
        ["choices", "choices", "choices", "choices", "random"]
        + (["choices"] if expected_count == 2 else [])
    )


@pytest.mark.parametrize("reserved_id", ["benzin_psiop", "knife", "oiled_vest"])
def test_only_static_or_special_id_collision_rerolls(monkeypatch, reserved_id):
    chosen = Mock(side_effect=[
        {"item_id": reserved_id, "name": "Не то имя"},
        {"item_id": "generated_relic", "name": "Новая реликвия"},
    ])
    monkeypatch.setattr(duel_items, "generate", chosen)
    assert duel_items.roll_generated_item() == ("generated_relic", "Новая реликвия")
    assert chosen.call_count == 2


def test_existing_generated_id_keeps_canonical_name_and_stacks(temp_database, monkeypatch):
    chat_id = -8101
    _register(chat_id, 1)
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO generated_item_names (item_id, name) VALUES (?, ?)",
            ("same_generated_id", "Каноническое имя"),
        )
    chosen = Mock(return_value={"item_id": "same_generated_id", "name": "Иной вариант"})
    monkeypatch.setattr(duel_items, "generate", chosen)
    for _ in range(2):
        event_id = _published_event(chat_id)
        status, instance = db.claim_duel_item_event(
            event_id, chat_id, 1, duel_items.roll_generated_item,
        )
        assert status == "claimed" and instance["item_id"] == "same_generated_id"
    assert chosen.call_count == 2
    assert db.get_generated_item_name("same_generated_id") == "Каноническое имя"
    rows = duel_items.get_duel_display_inventory_rows(db.get_duel_inventory(chat_id, 1))
    assert {"item_id": "same_generated_id", "name": "Каноническое имя", "count": 2} in rows
    assert "Каноническое имя" in duel_items.format_duel_display_inventory(
        db.get_duel_inventory(chat_id, 1)
    )


def test_static_and_special_display_names_take_precedence(temp_database):
    with db.get_db() as conn:
        conn.executemany(
            "INSERT INTO generated_item_names (item_id, name) VALUES (?, ?)",
            (("knife", "Неверное имя"), ("benzin_psiop", "Другое неверное имя")),
        )
    assert duel_items.get_duel_item_name("knife") == "Нож"
    assert duel_items.get_duel_item_name("benzin_psiop") == "Бензиновый псиоп"


def test_additive_name_dictionary_migration_and_restart(tmp_path, monkeypatch):
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE preserved (value TEXT NOT NULL)")
        conn.execute("INSERT INTO preserved VALUES ('keep')")
    monkeypatch.setattr(db, "DB_NAME", str(path))
    db.init_db()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO generated_item_names (item_id, name) VALUES (?, ?)",
            ("persistent_generated", "Постоянное имя"),
        )
    db.init_db()
    assert db.get_generated_item_name("persistent_generated") == "Постоянное имя"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM preserved").fetchone()[0] == "keep"
        columns = [row[1] for row in conn.execute("PRAGMA table_info(generated_item_names)")]
    assert columns == ["item_id", "name"]


def test_dig_fixes_item_at_discovery_and_pickup_never_regenerates(temp_database):
    chat_id = -8102
    _register(chat_id, 1)
    with db.get_db() as conn:
        conn.execute("UPDATE duel_users SET points = 50 WHERE chat_id = ? AND user_id = 1",
                     (chat_id,))
    selector = Mock(return_value=("dig_generated", "Открытая находка"))
    status, dig = db.try_duel_dig(chat_id, 1, lambda: 0.0, selector)
    assert status == "found"
    selector.assert_called_once_with()
    assert db.get_duel_item_event(dig["event_id"])["item_id"] == "dig_generated"
    assert db.get_generated_item_name("dig_generated") == "Открытая находка"
    assert db.get_duel_inventory(chat_id, 1) == []
    assert db.set_duel_item_event_message(dig["event_id"], 501, "Находка")
    no_reroll = Mock(side_effect=AssertionError("fixed dig loot regenerated"))
    claimed, instance = db.claim_duel_item_event(
        dig["event_id"], chat_id, 1, no_reroll,
    )
    assert claimed == "claimed" and instance["item_id"] == "dig_generated"
    no_reroll.assert_not_called()


def test_generic_claim_is_single_generation_and_deleted_click_does_not_claim(
    temp_database,
):
    chat_id = -8103
    _register(chat_id, 1)
    _register(chat_id, 2)
    _register(chat_id, 3)
    event_id = _published_event(chat_id)
    assert db.get_duel_item_event(event_id)["item_id"] is None
    assert db.delete_gnome(3, "user3")
    selector = Mock(return_value=("race_generated", "Единственная награда"))
    rejected, _ = db.claim_duel_item_event(event_id, chat_id, 3, selector)
    assert rejected == "not_registered"
    assert db.get_duel_item_event(event_id)["claimed"] is False
    selector.assert_not_called()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(db.claim_duel_item_event, event_id, chat_id, user_id,
                               selector) for user_id in (1, 2)]
        results = [future.result() for future in futures]
    assert sorted(status for status, _ in results) == ["already_claimed", "claimed"]
    selector.assert_called_once_with()
    assert sum(len(db.get_duel_inventory(chat_id, user_id)) for user_id in (1, 2)) == 1
    assert db.get_generated_item_name("race_generated") == "Единственная награда"


def test_failed_generated_award_rolls_back_name_and_claim(temp_database):
    chat_id = -8106
    _register(chat_id, 1)
    event_id = _published_event(chat_id)
    with db.get_db() as conn:
        conn.execute(
            """CREATE TRIGGER reject_generated_item BEFORE INSERT ON duel_inventory
               BEGIN SELECT RAISE(ABORT, 'blocked'); END"""
        )
    selector = Mock(return_value=("failed_generated", "Несохранённая вещь"))
    with pytest.raises(sqlite3.IntegrityError, match="blocked"):
        db.claim_duel_item_event(event_id, chat_id, 1, selector)
    selector.assert_called_once_with()
    assert db.get_generated_item_name("failed_generated") is None
    assert db.get_duel_item_event(event_id)["claimed"] is False
    assert db.get_duel_inventory(chat_id, 1) == []


def test_huecrab_generates_only_deferred_item_and_skips_deleted_owner(temp_database):
    chat_id = -8104
    _register(chat_id, 1)
    with db.get_db() as conn:
        conn.execute("INSERT INTO huecrab_owners (chat_id, user_id) VALUES (?, ?)",
                     (chat_id, 1))
    selector = Mock(return_value=("crab_generated", "Крабья добыча"))
    owner_selector = Mock(side_effect=AssertionError("sole owner chosen by RNG"))

    generic = _published_event(chat_id)
    with db.get_db() as conn:
        conn.execute("UPDATE duel_item_events SET published_at = 1000 WHERE event_id = ?",
                     (generic,))
    status, claim = db.claim_due_item_for_huecrab(
        generic, 1020, 20, selector, owner_selector,
    )
    assert status == "claimed" and claim["item_id"] == "crab_generated"
    selector.assert_called_once_with()

    fixed = _published_event(chat_id, item_id="crab_generated")
    with db.get_db() as conn:
        conn.execute("UPDATE duel_item_events SET published_at = 1000 WHERE event_id = ?",
                     (fixed,))
    no_reroll = Mock(side_effect=AssertionError("fixed item regenerated"))
    status, claim = db.claim_due_item_for_huecrab(
        fixed, 1020, 20, no_reroll, owner_selector,
    )
    assert status == "claimed" and claim["item_id"] == "crab_generated"
    no_reroll.assert_not_called()

    deleted_event = _published_event(chat_id)
    with db.get_db() as conn:
        conn.execute("UPDATE duel_item_events SET published_at = 1000 WHERE event_id = ?",
                     (deleted_event,))
    assert db.delete_gnome(1, "user1")
    status, claim = db.claim_due_item_for_huecrab(
        deleted_event, 1020, 20, no_reroll, owner_selector,
    )
    assert status == "no_owners" and claim is None
    assert db.get_duel_item_event(deleted_event)["claimed"] is False


def test_player_originated_drop_keeps_item_id_without_generator(temp_database):
    chat_id = -8105
    _register(chat_id, 1)
    _register(chat_id, 2)
    with db.get_db() as conn:
        conn.execute("INSERT INTO generated_item_names VALUES (?, ?)",
                     ("owned_generated", "Вещь из кармана"))
    original = db.add_duel_inventory_item(chat_id, 1, "owned_generated")
    drop = db.create_duel_item_event_from_inventory(chat_id, 1, original["id"])
    assert drop["item_id"] == "owned_generated"
    assert db.set_duel_item_event_message(drop["event_id"], 502, "Дроп")
    no_reroll = Mock(side_effect=AssertionError("player item regenerated"))
    status, instance = db.claim_duel_item_event(
        drop["event_id"], chat_id, 2, no_reroll,
    )
    assert status == "claimed" and instance["item_id"] == "owned_generated"
    assert db.get_generated_item_name(instance["item_id"]) == "Вещь из кармана"
    no_reroll.assert_not_called()
