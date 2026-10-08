"""One-time recovery of provable pre-equipment generated inventory."""

import sqlite3
from types import SimpleNamespace

import database as db
import pytest
from loot import recover
from loot.catalog import ADJECTIVES, BASES, QUALITIES, SOURCES
from loot.translit import transliterate


CHAT = -9401
SHIELD = "Поганый коммунальный щиток «Гарда» кобры"
BASTION = "Поганый коммунальный бастион кобры"


def register(user_id=1, chat_id=CHAT):
    db.get_or_create_duel_user(SimpleNamespace(
        id=user_id, username=f"dwarf{user_id}", first_name="Dwarf",
        last_name=None, is_bot=False,
    ), chat_id)


def legacy_item(name, *, user_id=1, chat_id=CHAT, kind=None, base_form=None):
    item_id = transliterate(name)
    with db.get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO generated_item_names (item_id, name, kind, base_form)
               VALUES (?, ?, ?, ?)""", (item_id, name, kind, base_form),
        )
        cursor = conn.execute(
            "INSERT INTO duel_inventory (chat_id, user_id, item_id) VALUES (?, ?, ?)",
            (chat_id, user_id, item_id),
        )
        return cursor.lastrowid, item_id


def run_legacy_migration():
    with db.get_db() as conn:
        conn.execute("DELETE FROM generated_equipment_migrations")
    db.init_db()


def metadata(item_id):
    with db.get_db() as conn:
        return conn.execute(
            "SELECT name, kind, base_form FROM generated_item_names WHERE item_id = ?",
            (item_id,),
        ).fetchone()


def test_exact_recovery_validates_complete_name_and_transliteration():
    assert recover.legacy_catalog_matches_snapshot()
    item_id = transliterate(SHIELD)
    assert recover.recover_legacy_generated_metadata(item_id, SHIELD) == (
        "recovered", ("пах", "m"))
    assert recover.recover_legacy_generated_metadata("wrong_id", SHIELD) == ("unknown", None)
    assert recover.recover_legacy_generated_metadata(
        transliterate("Несуществующий предмет"), "Несуществующий предмет") == ("unknown", None)


def test_id_collision_with_different_metadata_is_rejected(monkeypatch):
    fake = {"id": "colliding_base", "name": "щиток Гарда", "list": "m",
            "kind": "головной убор", "weight": 1}
    monkeypatch.setattr(recover, "BASES", recover.BASES + (fake,))
    assert recover.recover_legacy_generated_metadata(transliterate(SHIELD), SHIELD) == (
        "ambiguous", None)
    monkeypatch.setattr(recover, "BASES", recover.BASES[:-1] + (
        {**fake, "kind": "пах"},))
    assert recover.recover_legacy_generated_metadata(transliterate(SHIELD), SHIELD) == (
        "recovered", ("пах", "m"))


def test_old_shield_metadata_and_free_groin_are_restored_once(temp_database):
    register()
    instance_id, item_id = legacy_item(SHIELD)
    before = db.get_duel_inventory(CHAT, 1)
    run_legacy_migration()
    assert metadata(item_id) == (SHIELD, "пах", "m")
    assert db.get_duel_inventory(CHAT, 1) == before
    assert db.get_duel_equipment(CHAT, 1)["groin"] == {
        "inventory_id": instance_id, "item_id": item_id, "name": SHIELD,
    }
    from handlers.duel_text import get_equipment_break_text
    equipped = db.consume_equipped_item_for_hit(CHAT, 1, "dick")
    assert equipped["base_form"] == "m"
    assert get_equipment_break_text(equipped) == f"{SHIELD} остановил удар и порвался"
    db.init_db()
    assert db.get_duel_equipment(CHAT, 1) == {}


def test_occupied_slot_is_kept_and_oldest_free_candidate_wins(temp_database):
    register()
    first, _ = legacy_item(SHIELD)
    second, _ = legacy_item(BASTION)
    run_legacy_migration()
    assert db.get_duel_equipment(CHAT, 1)["groin"]["inventory_id"] == first
    assert {item["id"] for item in db.get_duel_inventory(CHAT, 1)} == {first, second}
    assert db.consume_equipped_item_for_hit(CHAT, 1, "dick") is not None
    db.init_db()
    assert db.get_duel_equipment(CHAT, 1) == {}
    assert db.get_duel_inventory(CHAT, 1)[0]["id"] == second


def test_existing_equipment_wins_over_legacy_inventory(temp_database):
    register()
    old_id, old_item_id = legacy_item(SHIELD)
    equipped_id = db.add_duel_inventory_item(CHAT, 1, "existing_guard")["id"]
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO generated_item_names (item_id, name, kind, base_form) VALUES (?, ?, ?, ?)",
            ("existing_guard", "Новая защита", "пах", "m"),
        )
        conn.execute(
            "INSERT INTO duel_equipment (chat_id, user_id, slot, inventory_id) VALUES (?, ?, ?, ?)",
            (CHAT, 1, "groin", equipped_id),
        )
    run_legacy_migration()
    assert metadata(old_item_id) == (SHIELD, "пах", "m")
    assert db.get_duel_equipment(CHAT, 1)["groin"]["inventory_id"] == equipped_id
    assert any(item["id"] == old_id for item in db.get_duel_inventory(CHAT, 1))


def test_conflicting_metadata_is_not_overwritten_and_unknown_is_skipped(temp_database):
    register()
    _, shield_id = legacy_item(SHIELD, kind="головной убор")
    _, unknown_id = legacy_item("Несуществующий предмет")
    run_legacy_migration()
    assert metadata(shield_id) == (SHIELD, "головной убор", None)
    assert metadata(unknown_id) == ("Несуществующий предмет", None, None)
    assert db.get_duel_equipment(CHAT, 1) == {}


def test_partially_known_metadata_is_filled_without_re_equipping(temp_database):
    register()
    _, item_id = legacy_item(SHIELD, kind="пах")
    run_legacy_migration()
    assert metadata(item_id) == (SHIELD, "пах", "m")
    assert db.get_duel_equipment(CHAT, 1) == {}


def test_migration_failure_rolls_back_metadata_equipment_and_marker(temp_database):
    register()
    _, item_id = legacy_item(SHIELD)
    with db.get_db() as conn:
        conn.execute("DELETE FROM generated_equipment_migrations")
        conn.execute("""CREATE TRIGGER reject_legacy_equipment BEFORE INSERT ON duel_equipment
                        BEGIN SELECT RAISE(ABORT, 'migration blocked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="migration blocked"):
        db.init_db()
    assert metadata(item_id) == (SHIELD, None, None)
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM generated_equipment_migrations").fetchone()[0] == 0
        conn.execute("DROP TRIGGER reject_legacy_equipment")
    db.init_db()
    assert metadata(item_id) == (SHIELD, "пах", "m")


def test_catalog_drift_skips_migration_without_marker(temp_database, monkeypatch):
    register()
    _, item_id = legacy_item(SHIELD)
    with db.get_db() as conn:
        conn.execute("DELETE FROM generated_equipment_migrations")
    monkeypatch.setattr(recover, "legacy_catalog_matches_snapshot", lambda: False)
    db.init_db()
    assert metadata(item_id) == (SHIELD, None, None)
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM generated_equipment_migrations").fetchone()[0] == 0


def test_deleted_and_reset_profiles_cannot_receive_old_equipment(temp_database):
    register(1)
    register(2)
    legacy_item(SHIELD, user_id=1)
    assert db.delete_gnome(1, "dwarf1")
    assert db.return_gnome("dwarf1", verified_user_id=1) == "returned"
    register(1)
    orphan_id, _ = legacy_item(SHIELD, user_id=2)
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO deleted_users (user_id, deleted_at) VALUES (?, CURRENT_TIMESTAMP)", (2,)
        )
    run_legacy_migration()
    assert db.get_duel_equipment(CHAT, 1) == {}
    assert db.get_duel_equipment(CHAT, 2) == {}
    assert db.get_duel_inventory(CHAT, 1) == []
    assert db.get_duel_inventory(CHAT, 2)[0]["id"] == orphan_id


def test_old_name_only_schema_migrates_and_new_items_still_equip(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE generated_item_names (item_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO generated_item_names VALUES (?, ?)", (transliterate(SHIELD), SHIELD))
    monkeypatch.setattr(db, "DB_NAME", str(path))
    db.init_db()
    assert metadata(transliterate(SHIELD)) == (SHIELD, "пах", "m")
    register()
    new_id = transliterate(BASTION)
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO generated_item_names VALUES (?, ?, ?, ?)",
            (new_id, BASTION, "пах", "m"),
        )
    instance = db.add_duel_inventory_item(CHAT, 1, new_id)
    assert db.get_duel_equipment(CHAT, 1)["groin"]["inventory_id"] == instance["id"]


@pytest.mark.parametrize("kind,slot", [
    ("оружие", "weapon"), ("верхняя одежда", "outerwear"), ("обувь", "footwear"),
])
def test_restored_nonprotective_slots_do_not_absorb_hits(temp_database, kind, slot):
    register()
    base = next(item for item in BASES if item["kind"] == kind)
    form = base["list"]
    name = " ".join((QUALITIES[0][form], ADJECTIVES[0][form],
                     base["name"], SOURCES[0]["name"]))
    instance_id, _ = legacy_item(name)
    run_legacy_migration()
    assert db.get_duel_equipment(CHAT, 1)[slot]["inventory_id"] == instance_id
    assert all(db.consume_equipped_item_for_hit(CHAT, 1, zone) is None
               for zone in ("head", "body", "dick"))
    assert db.get_duel_inventory(CHAT, 1)[0]["id"] == instance_id
