"""Manual Mini App equipment changes are instance-scoped and battle-safe."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import database as db
from duel_session_repository import create_duel_session
from handlers import duel
from miniapp_api import create_miniapp_api
from tests.test_miniapp_api import CHAT_A, CHAT_B, register, session_for, snapshot
from tests.test_miniapp_auth import TEST_BOT_TOKEN


def item(chat, user, item_id, name, kind):
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO generated_item_names (item_id, name, kind) VALUES (?, ?, ?)",
            (item_id, name, kind),
        )
    return db.add_duel_inventory_item(chat, user, item_id)["id"]


def equipment(chat=CHAT_A, user=101):
    return db.get_duel_equipment(chat, user)


@pytest.mark.asyncio
async def test_list_equip_replace_unequip_and_virtual_defaults(temp_database):
    register(CHAT_A, 101, "hero")
    register(CHAT_A, 202, "other")
    old = item(CHAT_A, 101, "old_sword", "Старый меч", "оружие")
    new = item(CHAT_A, 101, "new_sword", "Новый меч", "оружие")
    boots = item(CHAT_A, 101, "boots", "Башмаки", "обувь")
    foreign = item(CHAT_A, 202, "foreign", "Чужой меч", "оружие")
    other_chat = item(CHAT_B, 101, "other_chat", "Чужой чат", "оружие")
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 101)
        listed = (await client.get("/api/v1/equipment/weapon", headers=headers)).json()
        assert listed == {"slot": "weapon", "blocked": False, "items": [
            {"inventory_id": old, "name": "Старый меч", "equipped": False},
            {"inventory_id": new, "name": "Новый меч", "equipped": True},
        ]}
        assert (await client.get("/api/v1/equipment/clothing", headers=headers)).json()["items"] == []
        assert (await client.get("/api/v1/equipment/not-a-slot", headers=headers)).status_code == 422
        async def equip(instance, slot="weapon"):
            return await client.post("/api/v1/equipment/equip", headers=headers,
                                     json={"slot": slot, "inventory_id": instance})
        assert (await equip(old)).json()["status"] == "equipped"
        assert (await equip(old)).json()["status"] == "already_equipped"
        assert equipment()["weapon"]["inventory_id"] == old
        assert {i["id"] for i in db.get_duel_inventory(CHAT_A, 101)} == {old, new, boots}
        for instance, slot, code in [
            (foreign, "weapon", "item_unavailable"),
            (other_chat, "weapon", "item_unavailable"),
            (boots, "weapon", "wrong_slot"),
        ]:
            response = await equip(instance, slot)
            assert response.status_code == 409 and response.json()["detail"]["code"] == code
        db.remove_duel_inventory_instance(CHAT_A, 101, new)
        assert (await equip(new)).json()["detail"]["code"] == "item_unavailable"
        assert (await client.post("/api/v1/equipment/unequip", headers=headers,
                                  json={"slot": "weapon"})).json()["status"] == "unequipped"
        assert (await client.post("/api/v1/equipment/unequip", headers=headers,
                                  json={"slot": "weapon"})).json()["status"] == "already_empty"
        me = (await client.get("/api/v1/me", headers=headers)).json()
        assert me["equipment"]["weapon"]["name"] == "Нож"
        assert me["equipment"]["outerwear"]["name"] != "Новый меч"
        assert old in {i["id"] for i in db.get_duel_inventory(CHAT_A, 101)}


@pytest.mark.asyncio
async def test_old_accessory_can_be_manually_equipped_and_unequipped(temp_database):
    register(CHAT_A, 101, "hero")
    register(CHAT_A, 202, "other")
    with db.get_db() as conn:
        conn.execute("INSERT INTO generated_item_names (item_id, name, kind) VALUES (?, ?, ?)",
                     ("legacy_ring", "Старое кольцо", "аксессуар"))
        old = conn.execute("INSERT INTO duel_inventory (chat_id, user_id, item_id) VALUES (?, ?, ?)",
                           (CHAT_A, 101, "legacy_ring")).lastrowid
    foreign = item(CHAT_A, 202, "foreign_ring", "Чужое кольцо", "аксессуар")
    wrong = item(CHAT_A, 101, "sword_for_ring", "Меч", "оружие")
    assert "accessory" not in equipment()
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 101)
        listed = (await client.get("/api/v1/equipment/accessory", headers=headers)).json()
        assert listed["items"] == [{"inventory_id": old, "name": "Старое кольцо", "equipped": False}]
        for instance, code in ((foreign, "item_unavailable"), (wrong, "wrong_slot")):
            response = await client.post("/api/v1/equipment/equip", headers=headers,
                                         json={"slot": "accessory", "inventory_id": instance})
            assert response.status_code == 409 and response.json()["detail"]["code"] == code
        response = await client.post("/api/v1/equipment/equip", headers=headers,
                                     json={"slot": "accessory", "inventory_id": old})
        assert response.json()["status"] == "equipped"
        assert (await client.get("/api/v1/me", headers=headers)).json()["equipment"]["accessory"]["name"] == "Старое кольцо"
        response = await client.post("/api/v1/equipment/unequip", headers=headers,
                                     json={"slot": "accessory"})
        assert response.json()["status"] == "unequipped"
        assert (await client.get("/api/v1/me", headers=headers)).json()["equipment"]["accessory"]["name"] == "Пусто"
    assert any(row["id"] == old for row in db.get_duel_inventory(CHAT_A, 101))


@pytest.mark.asyncio
async def test_deleted_profile_cannot_equip_and_return_starts_empty(temp_database):
    register(CHAT_A, 101, "hero")
    sword = item(CHAT_A, 101, "sword", "Меч", "оружие")
    assert db.delete_gnome(101, "hero")
    assert db.change_generated_equipment(CHAT_A, 101, "weapon", sword) == "profile_unavailable"
    assert db.list_generated_equipment_instances(CHAT_A, 101, "weapon") is None
    assert db.return_gnome("hero", verified_user_id=101) == "returned"
    register(CHAT_A, 101, "hero")
    assert db.list_generated_equipment_instances(CHAT_A, 101, "weapon") == []
    assert db.change_generated_equipment(CHAT_A, 101, "weapon", sword) == "item_unavailable"


@pytest.mark.asyncio
async def test_pvp_participants_cannot_change_any_slot_until_finished(temp_database):
    player1 = register(CHAT_A, 101, "hero")
    player2 = register(CHAT_A, 202, "opponent")
    first = item(CHAT_A, 101, "first", "Первый шлем", "головной убор")
    second = item(CHAT_A, 101, "second", "Второй шлем", "головной убор")
    accessory = item(CHAT_A, 101, "pvp_ring", "Кольцо", "аксессуар")
    assert db.change_generated_equipment(CHAT_A, 101, "head", first) == "equipped"
    duel_session = create_duel_session(
        CHAT_A, 101, 202, snapshot(player1), snapshot(player2), 101, 202,
        status="publishing", phase="attack",
    )
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 101)
        assert (await client.get("/api/v1/me", headers=headers)).json()["equipment_management_blocked"]
        assert (await client.get("/api/v1/equipment/head", headers=headers)).json()["blocked"]
        for path, body in [
            ("equip", {"slot": "head", "inventory_id": second}),
            ("equip", {"slot": "head", "inventory_id": first}),
            ("unequip", {"slot": "head"}),
            ("equip", {"slot": "accessory", "inventory_id": accessory}),
            ("unequip", {"slot": "accessory"}),
        ]:
            response = await client.post(f"/api/v1/equipment/{path}", headers=headers, json=body)
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "active_battle"
        assert equipment()["head"]["inventory_id"] == first
        with db.get_db() as conn:
            conn.execute("UPDATE duel_sessions SET status = 'cancelled' WHERE id = ?",
                         (duel_session["id"],))
        assert not (await client.get("/api/v1/me", headers=headers)).json()["equipment_management_blocked"]
        assert (await client.post("/api/v1/equipment/equip", headers=headers,
                                  json={"slot": "head", "inventory_id": second})).status_code == 200


@pytest.mark.asyncio
async def test_boss_participant_rejected_and_boss_join_race_serialized(temp_database, monkeypatch):
    register(CHAT_A, 101, "hero")
    first = item(CHAT_A, 101, "first", "Шлем", "головной убор")
    second = item(CHAT_A, 101, "second", "Шапка", "головной убор")
    accessory = item(CHAT_A, 101, "boss_ring", "Кольцо", "аксессуар")
    battle = {"lock": asyncio.Lock(), "participants": {101: {}}}
    monkeypatch.setitem(duel.ACTIVE_BOSS_BATTLES, CHAT_A, battle)
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 101)
        assert (await client.get("/api/v1/me", headers=headers)).json()["equipment_management_blocked"]
        for path, body in [
            ("equip", {"slot": "head", "inventory_id": first}),
            ("equip", {"slot": "head", "inventory_id": second}),
            ("unequip", {"slot": "head"}),
            ("equip", {"slot": "accessory", "inventory_id": accessory}),
            ("unequip", {"slot": "accessory"}),
        ]:
            response = await client.post(f"/api/v1/equipment/{path}", headers=headers, json=body)
            assert response.status_code == 409
        assert equipment()["head"]["inventory_id"] == second
        async with battle["lock"]:
            battle["participants"].clear()
            pending = asyncio.create_task(client.post(
                "/api/v1/equipment/equip", headers=headers,
                json={"slot": "head", "inventory_id": first},
            ))
            await asyncio.sleep(0)
            battle["participants"][101] = {}
        assert (await pending).status_code == 409
        duel.ACTIVE_BOSS_BATTLES.pop(CHAT_A)
        assert not (await client.get("/api/v1/me", headers=headers)).json()["equipment_management_blocked"]
        assert (await client.post("/api/v1/equipment/equip", headers=headers,
                                  json={"slot": "head", "inventory_id": first})).status_code == 200


@pytest.mark.asyncio
async def test_boss_start_wins_race_before_equipment_mutation(temp_database, monkeypatch):
    register(CHAT_A, 101, "hero")
    first = item(CHAT_A, 101, "first", "Шлем", "головной убор")
    second = item(CHAT_A, 101, "second", "Шапка", "головной убор")
    start_lock = asyncio.Lock()
    monkeypatch.setitem(duel._BOSS_START_LOCKS, CHAT_A, start_lock)
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 101)
        async with start_lock:
            pending = asyncio.create_task(client.post(
                "/api/v1/equipment/equip", headers=headers,
                json={"slot": "head", "inventory_id": first},
            ))
            await asyncio.sleep(0)
            monkeypatch.setitem(duel.ACTIVE_BOSS_BATTLES, CHAT_A, {
                "lock": asyncio.Lock(), "participants": {101: {}},
            })
        response = await pending
        assert response.status_code == 409
        assert equipment()["head"]["inventory_id"] == second


def test_sqlite_write_lock_serializes_equip_and_duel_start(temp_database):
    from handlers.duel_service import start_persistent_duel
    register(CHAT_A, 101, "hero")
    register(CHAT_A, 202, "opponent")
    first = item(CHAT_A, 101, "first", "Первый меч", "оружие")
    second = item(CHAT_A, 101, "second", "Второй меч", "оружие")
    with ThreadPoolExecutor(max_workers=2) as pool:
        equip_future = pool.submit(db.change_generated_equipment, CHAT_A, 101,
                                   "weapon", first)
        duel_future = pool.submit(start_persistent_duel, CHAT_A, 101, 202)
        equip_result = equip_future.result()
        duel_result = duel_future.result()
    assert duel_result.success
    assert equip_result in ("equipped", "active_battle")
    assert db.change_generated_equipment(CHAT_A, 101, "weapon", second) == "active_battle"


def test_parallel_replacements_keep_one_owned_instance(temp_database):
    register(CHAT_A, 101, "hero")
    first = item(CHAT_A, 101, "first", "Первый меч", "оружие")
    second = item(CHAT_A, 101, "second", "Второй меч", "оружие")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda instance: db.change_generated_equipment(CHAT_A, 101,
                                                            "weapon", instance),
            (first, second),
        ))
    assert set(results) <= {"equipped", "already_equipped"}
    assert equipment()["weapon"]["inventory_id"] in (first, second)
    assert {i["id"] for i in db.get_duel_inventory(CHAT_A, 101)} == {first, second}
    with db.get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM duel_equipment WHERE chat_id = ? AND user_id = ?",
            (CHAT_A, 101),
        ).fetchone()[0] == 1


def test_hit_during_pvp_cannot_be_replaced_by_manual_action(temp_database):
    player1 = register(CHAT_A, 101, "hero")
    player2 = register(CHAT_A, 202, "opponent")
    spare = item(CHAT_A, 101, "spare", "Запасной шлем", "головной убор")
    worn = item(CHAT_A, 101, "worn", "Надетый шлем", "головной убор")
    create_duel_session(CHAT_A, 101, 202, snapshot(player1), snapshot(player2),
                        101, 202, status="publishing", phase="attack")
    with ThreadPoolExecutor(max_workers=2) as pool:
        change = pool.submit(db.change_generated_equipment, CHAT_A, 101, "head", spare)
        hit = pool.submit(db.consume_equipped_item_for_hit, CHAT_A, 101, "head")
        assert change.result() == "active_battle"
        assert hit.result()["inventory_id"] == worn
    assert "head" not in equipment()
    assert db.get_duel_inventory(CHAT_A, 101)[0]["id"] == spare
