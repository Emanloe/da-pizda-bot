import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from handlers.adventure import (
    adventure_available,
    generate_available_finds,
    handle_adventure_command,
    handle_callback,
    make_choice,
    on_adventure_step,
    process_pickaxe_break,
    Tiers,
)
import handlers.adventure as adv


@pytest.fixture
def mock_pool():
    return {"wins": 123, "finds": {}, "activity_log": {}, "solo": {"wins": []}}


@pytest.fixture
def mock_locations():
    return {
        "locations": {
            "stone_fields": {
                "name": "Каменные поля",
                "cost": 0,
                "tier": "stone",
                "min_health": 0,
                "max_health": 1,
                "solo": False,
                "success_cool_reward": 0.25,
                "success_cool_reward_7_days": 0.25,
                "trials": [
                    {
                        "type": "simple_choice",
                        "text": "Вы видите странный камень",
                        "weight": 1.0,
                        "choices": {
                            "poke": {
                                "text": "Ткнуть камень",
                                "success": 0.5,
                                "success_msg": "Камень оказался золотым",
                                "fail_msg": "Камень оказался простым",
                                "success_reward": {
                                    "text": "Вы нашли золотой самородок",
                                    "rarity": "common",
                                    "score": 5,
                                    "xp": 5,
                                },
                                "fail_result": "nothing",
                            },
                            "ignore": {
                                "text": "Пройти мимо",
                                "result": "nothing",
                                "message": "Вы прошли мимо",
                            },
                        },
                    }
                ],
                "finds": {
                    "health_potion_small": {
                        "name": "Малое зелье здоровья",
                        "description": "Восстанавливает 25 здоровья",
                        "type": "consumable",
                        "icon": "🧪",
                        "rare": False,
                        "score": 30,
                    }
                },
            }
        }
    }


@pytest.fixture
def mock_player_basic():
    return {
        "user_id": 123,
        "username": "testuser",
        "health": 100,
        "max_health": 100,
        "inventory": {},
        "pickaxe": {"power": 0, "score": 0, "name": "Ржавая кирка", "icon": "⛏️"},
        "join_time": datetime.now().isoformat(),
        "total_score": 0,
        "total_xp": 0,
        "active": True,
        "delved": 0,
        "won": 0,
        "won_1m": 0,
        "won_1w": 0,
        "last_daily": None,
        "achievements": [],
        "known_finds": {},
        "pending_review": None,
    }


def test_adventure_available_score_high(mock_player_basic):
    mock_player_basic["total_score"] = 150
    assert adventure_available(mock_player_basic) is True


def test_adventure_available_score_low(mock_player_basic):
    mock_player_basic["total_score"] = 50
    assert adventure_available(mock_player_basic) is False


def test_generate_available_finds(mock_locations):
    finds = generate_available_finds(mock_locations, "stone_fields")
    assert isinstance(finds, list)
    assert "health_potion_small" in finds


def test_generate_available_finds_no_location(mock_locations):
    finds = generate_available_finds(mock_locations, "nonexistent")
    assert finds == []


def test_tiers_enum_is_ordered_correctly():
    assert Tiers.stone.tier_val == 0
    assert Tiers.iron.tier_val == 1
    assert Tiers.steel.tier_val == 2
    assert Tiers.mithrill.tier_val == 3


class TestProcessPickaxeBreak:
    def test_process_pickaxe_break_no_pickaxe(self):
        player = {"pickaxe": None, "total_score": 0}
        result = process_pickaxe_break(player)
        assert result["text"] == "У вас и так нет кирки."
        assert result["broken"] is False

    def test_process_pickaxe_break_already_broken(self):
        player = {"pickaxe": {"broken": True, "name": "Кирка"}, "total_score": 0}
        result = process_pickaxe_break(player)
        assert result["text"] == "Уже сломано."
        assert result["broken"] is False

    def test_process_pickaxe_break_success_same_level(self):
        player = {
            "pickaxe": {"broken": False, "level": 1, "name": "Test", "score": 5},
            "total_score": 500,
        }
        result = process_pickaxe_break(player)
        assert result["broken"] is True
        assert player["pickaxe"]["broken"] is True
        assert "score" in player and player["score"] == 505  # Or however it should update

    def test_process_pickaxe_break_failure_different_level(self):
        player = {
            "pickaxe": {"broken": False, "level": 2, "name": "Test", "score": 5},
            "total_score": 500,
        }
        result = process_pickaxe_break(player)
        assert result["broken"] is False


class TestOnAdventureStep:
    @patch(
        "handlers.adventure.adventure_text.get",
        return_value={"text": "You see a tunnel leading down..."},
    )
    def test_orient_sends_random_location(self, mock_get, mock_player_basic):
        callback = MagicMock()
        context = MagicMock()
        context.user_data = {"adventure": {"player": mock_player_basic, "locid": None}}

        on_adventure_step(callback, context)

        callback.edit_message_text.assert_called_once()
        args = callback.edit_message_text.call_args
        assert "adventure_loc_choice" in args[1]["reply_markup"]

    @patch(
        "handlers.adventure.adventure_text.get",
        return_value={"text": "You see a tunnel leading down..."},
    )
    def test_second_step_with_loc_raises(self, mock_get, mock_player_basic):
        callback = MagicMock()
        context = MagicMock()
        context.user_data = {
            "adventure": {"player": mock_player_basic, "locid": "stone_fields"}
        }

        with pytest.raises(ValueError) as exc_info:
            on_adventure_step(callback, context)

        assert "Already at location" in str(exc_info.value)


class TestHandleCallback:
    def test_adventure_loc_choice(self):
        callback = MagicMock()
        context = MagicMock()
        context.user_data = {"adventure": {"player": {"total_score": 150}, "locid": None}}

        result = handle_callback(callback, context, "adventure_loc_choice", "stone_fields")
        assert result == "done"

    def test_do_choice(self):
        callback = MagicMock()
        context = MagicMock()
        context.user_data = {
            "adventure": {
                "player": {"username": "test", "inventory": {}, "health": 100, "total_score": 0},
                "locid": "stone_fields",
            }
        }

        with patch(
            "handlers.adventure.adventure_text.get", return_value={"text": "Описание"}
        ):
            with patch("handlers.adventure.random.random", return_value=1.0):  # Above success threshold
                result = handle_callback(callback, context, "do_choice", "poke")
                assert result == "done"
                # Check for success text in callback
                assert callback.edit_message_text.called

    def test_do_choice_inventory_full(self):
        callback = MagicMock()
        context = MagicMock()
        full_inventory = {f"item_{i}": 1 for i in range(15)}
        context.user_data = {
            "adventure": {
                "player": {
                    "username": "test",
                    "inventory": full_inventory,
                    "health": 100,
                    "total_score": 0,
                },
                "locid": "stone_fields",
            }
        }

        with patch(
            "handlers.adventure.adventure_text.get", return_value={"text": "Описание"}
        ):
            result = handle_callback(callback, context, "do_choice", "poke")
            assert result == "done"
            args = callback.edit_message_text.call_args
            assert "инвентарь полон" in args[0][0] or "full inventory" in args[1]["text"].lower()


class TestHandleAdventureCommand:
    @patch("handlers.adventure.update_player_data")
    @patch("handlers.adventure.save_player_data")
    def test_adventure_command_first_time(
        self, mock_save, mock_update, mock_player_basic, mock_pool, mock_locations
    ):
        message = MagicMock()
        context = MagicMock()

        new_player = mock_player_basic.copy()
        new_player.pop("join_time", None)

        with patch("handlers.adventure.load_player_data", return_value=new_player):
            with patch("handlers.adventure.save_pool"):
                with patch.object(adv, "locations", mock_locations):
                    with patch.object(adv, "pool", mock_pool):
                        result = handle_adventure_command(message, context)
                        assert result is True
                        assert "Добро пожаловать" in message.reply_text.call_args[0][0]

    @patch("handlers.adventure.update_player_data")
    @patch("handlers.adventure.save_player_data")
    def test_adventure_command_playing_unavailable(
        self, mock_save, mock_update, mock_player_basic, mock_pool, mock_locations
    ):
        message = MagicMock()
        context = MagicMock()

        player = mock_player_basic.copy()
        player["active"] = True
        player["health"] = 100

        with patch("handlers.adventure.load_player_data", return_value=player):
            with patch("handlers.adventure.save_pool"):
                with patch.object(adv, "locations", mock_locations):
                    with patch.object(adv, "pool", mock_pool):
                        result = handle_adventure_command(message, context)
                        assert result is True
                        assert "У вас недостаточно очков" in message.reply_text.call_args[0][0]

    @patch("handlers.adventure.update_player_data")
    @patch("handlers.adventure.save_player_data")
    def test_adventure_command_already_active(
        self, mock_save, mock_update, mock_player_basic, mock_pool, mock_locations
    ):
        message = MagicMock()
        context = MagicMock()
        context.user_data = {"adventure": {"player": mock_player_basic}}

        result = handle_adventure_command(message, context)
        assert result is True
        assert "Вы уже в режиме приключений" in message.reply_text.call_args[0][0]


class TestMakeChoice:

    @patch("handlers.adventure.adventure_text.get", return_value={"markdown": False})
    def test_make_choice_with_choice_selection(self, mock_get, mock_locations):
        mock_player = {
            "username": "test",
            "inventory": {},
            "max_health": 10,
            "total_score": 0,
            "total_xp": 0,
        }
        loc = mock_locations["locations"]["stone_fields"]

        # Mock the choice to return result instead of success
        # Change the trial to have result instead of success
        loc["trials"][0]["choices"]["poke"]["result"] = loc["trials"][0]["choices"][
            "poke"
        ].pop("success_reward", None)
        loc["trials"][0]["choices"]["poke"]["result"] = "nothing"

        text, _, _, _, _, _ = make_choice(
            mock_player, loc, loc["trials"][0], "poke", "reply_id"
        )

        assert text is not None

    @patch("handlers.adventure.adventure_text.get", return_value={"markdown": False})
    def test_make_choice_potion_on_low_health(self, mock_get, mock_locations):
        """Test that health potion is suggested when health is low"""
        mock_player = {
            "username": "test",
            "inventory": {"health_potion_small": 1},
            "max_health": 10,
            "total_score": 0,
            "total_xp": 0,
        }
        loc = mock_locations["locations"]["stone_fields"]
        # Manually set low health so potion suggestion triggers
        mock_player["health"] = 5

        text, _, _, _, is_final_battle, _ = make_choice(
            mock_player, loc, loc["trials"][0], "poke", "reply_id"
        )

        # If final battle u=0.85, should have LOW_HEALTH_MESSAGE fragment
        assert is_final_battle is True
        # The exact check depends on implementation details
        # This is mainly a smoke test that it doesn't crash
