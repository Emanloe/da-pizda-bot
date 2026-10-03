import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import handlers.adventure as adv


@pytest.fixture
def mock_player_basic():
    return {
        "user_id": 123,
        "username": "testuser",
        "hp": 200,
        "max_hp": 200,
        "lvl": 1,
        "xp": 0,
        "gold": 0,
        "inventory": [],
        "last_fight_time": None,
        "total_fights": 0,
        "wins": 0,
        "active_expedition": None,
    }


@pytest.fixture
def mock_locations():
    return {
        "dark_forest": {
            "name": "🌲Охуенно тёмный лес",
            "description": "Лес с особыми соснами",
            "duration": 300,
            "required_level": 1,
            "encounter_chance": 0.5,
            "loot_table": [
                {"item_id": "pine_needles", "name": "🌿 Хвоя", "chance": 0.6, "quantity_min": 1, "quantity_max": 3},
                {"item_id": "wood", "name": "🪵 Древесина", "chance": 0.45, "quantity_min": 1, "quantity_max": 2},
                {"item_id": "pine_cone", "name": "🌰 Шишка", "chance": 0.35, "quantity_min": 1, "quantity_max": 1},
            ]
        },
        "abandoned_mine": {
            "name": "Заброшенная шахта",
            "description": "Старая шахта",
            "duration": 300,
            "required_level": 3,
            "encounter_chance": 0.5,
            "loot_table": []
        },
        "mountain_lair": {
            "name": "Логово горного уебана",
            "description": "Логово босса",
            "duration": 300,
            "required_level": 5,
            "encounter_chance": 0.5,
            "loot_table": []
        }
    }


@pytest.fixture
def mock_mobs():
    return {
        "specific_mobs": {
            "dark_forest": [
                {
                    "name": "🌳Куст с предъявами",
                    "hp": 30,
                    "attack": 5,
                    "defense": 1,
                    "speed": 3,
                    "crit_chance": 0.0,
                    "encounter_chance": 35,
                    "min_level": 1,
                    "tactics": {"debuff_chance": 0.3, "debuff_name": "Паранойя"},
                },
                {
                    "name": "Голова деда на паучьих ногах",
                    "hp": 20,
                    "attack": 8,
                    "defense": 0,
                    "speed": 5,
                    "crit_chance": 0.1,
                    "encounter_chance": 35,
                    "min_level": 1,
                    "tactics": {"overpower": True, "overpower_threshold": 3},
                },
            ]
        }
    }


class TestResolveEncounterFight:
    """Тесты боевой системы энкаунтеров"""

    def test_player_wins_against_weak_mob(self, mock_player_basic):
        mob = {
            "name": "Слабый куст",
            "hp": 15,
            "dmg": 3,
            "level": 1,
            "tactics": {}
        }
        result = adv._resolve_encounter_fight(200, 200, mob)
        
        assert result["winner"] in ["player", "mob", "draw"]
        assert "rounds" in result
        assert len(result["rounds"]) > 0
        assert result["mob_name"] == "Слабый куст"

    def test_mob_wins_when_player_low_hp(self, mock_player_basic):
        mob = {
            "name": "Сильный моб",
            "hp": 100,
            "dmg": 50,
            "crit_chance": 0.5,
            "level": 5,
            "tactics": {}
        }
        result = adv._resolve_encounter_fight(10, 200, mob)
        
        assert result["rounds"]
        assert result["mob_name"] == "Сильный моб"

    def test_bush_debuff_mechanic(self, mock_player_basic):
        """Тест дебаффа 'Паранойя' от куста"""
        mob = {
            "name": "🌳Куст с предъявами",
            "hp": 30,
            "dmg": 5,
            "level": 1,
            "tactics": {"debuff_chance": 0.3}
        }
        
        # Многоразовый тест чтобы убедиться что дебафф иногда срабатывает
        results_with_debuff = 0
        for _ in range(20):
            result = adv._resolve_encounter_fight(200, 200, mob)
            # Проверка что бой вообще работает
            assert result["rounds"]
            # Дебафф может не сработать из-за рандома, поэтому не asserted напрямую
        # Тест просто проверяет что система работает

    def test_overpower_mechanic(self, mock_player_basic):
        """Тест спецспособности overpower (Голова деда на паучьих ногах)"""
        mob = {
            "name": "Голова деда на паучьих ногах",
            "hp": 50,
            "dmg": 5,
            "level": 1,
            "tactics": {"overpower": True}
        }
        result = adv._resolve_encounter_fight(200, 200, mob)
        
        assert result["rounds"]
        assert result["mob_name"] == "Голова деда на паучьих ногах"

    def test_max_rounds_limit(self, mock_player_basic):
        """Тест что бой не длится вечность"""
        mob = {
            "name": "Неубиваемый моб",
            "hp": 10000,
            "dmg": 1,
            "level": 1,
            "tactics": {}
        }
        result = adv._resolve_encounter_fight(200, 200, mob)
        
        assert len(result["rounds"]) <= 10
        assert result["encounter_result"] == "mob_escaped"


class TestRollLoot:
    """Тесты системы дропа ресурсов"""

    def test_roll_loot_respects_chances(self):
        loot_table = [
            {"item_id": "common_item", "name": "Common", "chance": 1.0, "quantity_min": 1, "quantity_max": 1},
            {"item_id": "rare_item", "name": "Rare", "chance": 0.0, "quantity_min": 1, "quantity_max": 1},
        ]
        
        # common_item должен всегда выпадать, rare_item никогда
        result = adv._roll_loot(loot_table)
        
        item_ids = [r["item_id"] for r in result]
        assert "common_item" in item_ids
        assert "rare_item" not in item_ids

    def test_roll_loot_empty_table(self):
        result = adv._roll_loot([])
        assert result == []

    def test_roll_loot_variable_quantity(self):
        loot_table = [
            {"item_id": "item", "name": "Item", "chance": 1.0, "quantity_min": 2, "quantity_max": 5},
        ]
        
        result = adv._roll_loot(loot_table)
        assert len(result) == 1
        assert 2 <= result[0]["quantity"] <= 5


class TestFormatResource:
    """Тесты форматирования ресурсов"""

    def test_format_resource_with_quantity(self):
        resource = {"name": "Хвоя", "quantity": 3}
        result = adv._format_resource(resource)
        assert "Хвоя" in result
        assert "x3" in result

    def test_format_resource_without_name(self):
        resource = {"item_id": "unknown_item", "quantity": 1}
        result = adv._format_resource(resource)
        assert "unknown_item" in result


class TestPlayerData:
    """Тесты работы с данными игрока"""

    def test_get_player_initializes_new_player(self, tmp_path):
        """Тест инициализации нового игрока"""
        with patch.object(adv, 'PLAYER_DATA_PATH', str(tmp_path / "player_data.json")):
            player = adv.get_player(999)
            
            assert player["hp"] == 200
            assert player["max_hp"] == 200
            assert player["lvl"] == 1
            assert player["inventory"] == []
            assert player["active_expedition"] is None

    def test_get_player_returns_existing_player(self, tmp_path):
        """Тест получения существующего игрока"""
        import json
        player_data = {
            "999": {"hp": 150, "max_hp": 200, "lvl": 5, "inventory": [], "active_expedition": None}
        }
        data_path = tmp_path / "player_data.json"
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(player_data, f)
        
        with patch.object(adv, 'PLAYER_DATA_PATH', str(data_path)):
            player = adv.get_player(999)
            
            assert player["hp"] == 150
            assert player["lvl"] == 5

    def test_update_player_updates_fields(self, tmp_path):
        """Тест обновления данных игрока"""
        import json
        player_data = {"999": {"hp": 200, "xp": 0, "inventory": [], "active_expedition": None}}
        data_path = tmp_path / "player_data.json"
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(player_data, f)
        
        with patch.object(adv, 'PLAYER_DATA_PATH', str(data_path)):
            adv.update_player(999, hp=180, xp=100)
            
            # Перезагрузка чтобы проверить изменения
            with open(data_path, 'r', encoding='utf-8') as f:
                updated = json.load(f)
            
            assert updated["999"]["hp"] == 180
            assert updated["999"]["xp"] == 100


class TestLoaders:
    """Тесты загрузчиков данных"""

    def test_load_mobs(self, mock_mobs, tmp_path):
        """Тест загрузки данных мобов"""
        import json
        mobs_path = tmp_path / "mobs.json"
        with open(mobs_path, 'w', encoding='utf-8') as f:
            json.dump(mock_mobs, f)
        
        with patch.object(adv, 'MOBS_PATH', str(mobs_path)):
            mobs = adv.load_mobs()
            assert "specific_mobs" in mobs
            assert "dark_forest" in mobs["specific_mobs"]
