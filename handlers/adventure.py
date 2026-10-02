"""
Adventure module: turn-based PvE fights at various locations.
Uses:
  data/adventure/locations.json   — location definitions & loot tables
  data/adventure/global_pool.json — global item pool references
  data/adventure/player_data.json — per-player HP / cooldowns / stats
  texts/adventure.yaml            — user-facing texts
"""

import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from text_resources import get_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADVENTURE_DIR = os.path.join(BASE_DIR, "data", "adventure")

LOCATIONS_PATH = os.path.join(ADVENTURE_DIR, "locations.json")
GLOBAL_POOL_PATH = os.path.join(ADVENTURE_DIR, "global_pool.json")
PLAYER_DATA_PATH = os.path.join(ADVENTURE_DIR, "player_data.json")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_locations() -> dict[str, Any]:
    return _load_json(LOCATIONS_PATH)


def load_global_pool() -> dict[str, Any]:
    return _load_json(GLOBAL_POOL_PATH)


def load_player_data() -> dict[str, Any]:
    if not os.path.exists(PLAYER_DATA_PATH):
        return {}
    return _load_json(PLAYER_DATA_PATH)


def save_player_data(data: dict[str, Any]) -> None:
    _save_json(PLAYER_DATA_PATH, data)


# ---------------------------------------------------------------------------
# Player helpers
# ---------------------------------------------------------------------------

def _player_key(user_id: int) -> str:
    return str(user_id)


def get_player(user_id: int, pool: dict | None = None) -> dict[str, Any]:
    data = load_player_data()
    key = _player_key(user_id)
    if key not in data:
        data[key] = {
            "hp": 100,
            "max_hp": 100,
            "lvl": 1,
            "xp": 0,
            "gold": 0,
            "last_fight_time": None,
            "total_fights": 0,
            "wins": 0,
            "inventory": [],
        }
        save_player_data(data)
    player = data[key]
    # ensure inventory field exists for older records
    if "inventory" not in player:
        player["inventory"] = []
    # ensure max_hp field exists
    if "max_hp" not in player:
        player["max_hp"] = 100
    return player


def update_player(user_id: int, **fields) -> None:
    data = load_player_data()
    key = _player_key(user_id)
    if key not in data:
        get_player(user_id)  # init
        data = load_player_data()
    data[key].update(fields)
    save_player_data(data)


# ---------------------------------------------------------------------------
# Cooldown helpers
# ---------------------------------------------------------------------------

COOLDOWN_MINUTES = 30


def _is_on_cooldown(player: dict[str, Any]) -> bool:
    last = player.get("last_fight_time")
    if not last:
        return False
    try:
        # ISO format with Z or +00:00
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return False
    return datetime.now(timezone.utc) < last_dt + timedelta(minutes=COOLDOWN_MINUTES)


def _cooldown_remaining(player: dict[str, Any]) -> int:
    last = player.get("last_fight_time")
    if not last:
        return 0
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return 0
    delta = (last_dt + timedelta(minutes=COOLDOWN_MINUTES)) - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds() // 60))


# ---------------------------------------------------------------------------
# Combat logic
# ---------------------------------------------------------------------------

def _roll_damage(base: int, variance: int = 3) -> int:
    return max(1, base + random.randint(-variance, variance))


def _resolve_fight(player_hp: int, boss: dict[str, Any]) -> dict[str, Any]:
    """
    Simplified turn-based fight.
    Returns dict with winner ('player' | 'boss'), player_hp_left, boss_hp_left,
    rounds list and initial player_hp.
    """
    boss_hp = boss.get("hp", 50)
    crit_chance = boss.get("crit_chance", 0)
    boss_name = boss.get("name", "Босс")

    player_max = player_hp
    current_player_hp = player_hp
    current_boss_hp = boss_hp
    rounds = []

    max_rounds = 20

    for r in range(1, max_rounds + 1):
        # player attacks boss
        dmg_to_boss = _roll_damage(8, 3)
        current_boss_hp -= dmg_to_boss

        # boss attacks player
        is_crit = random.random() < crit_chance
        dmg_to_player = _roll_damage(10 if is_crit else 6, 2)
        current_player_hp -= dmg_to_player

        rounds.append({
            "round": r,
            "player_dmg": dmg_to_boss,
            "boss_dmg": dmg_to_player,
            "boss_crit": is_crit,
            "player_hp": max(0, current_player_hp),
            "boss_hp": max(0, current_boss_hp),
        })

        if current_boss_hp <= 0:
            return {
                "winner": "player",
                "player_hp_left": max(0, current_player_hp),
                "boss_hp_left": 0,
                "rounds": rounds,
                "initial_player_hp": player_max,
                "boss_name": boss_name,
            }
        if current_player_hp <= 0:
            return {
                "winner": "boss",
                "player_hp_left": 0,
                "boss_hp_left": max(0, current_boss_hp),
                "rounds": rounds,
                "initial_player_hp": player_max,
                "boss_name": boss_name,
            }

    # draw -> boss wins by endurance
    return {
        "winner": "boss",
        "player_hp_left": max(0, current_player_hp),
        "boss_hp_left": max(0, current_boss_hp),
        "rounds": rounds,
        "initial_player_hp": player_max,
        "boss_name": boss_name,
    }


# ---------------------------------------------------------------------------
# Loot helpers
# ---------------------------------------------------------------------------

def _roll_loot(loot_table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    won = []
    for entry in loot_table:
        chance = entry.get("chance", 0.0)
        qty_min = entry.get("quantity_min", 1)
        qty_max = entry.get("quantity_max", 1)
        if random.random() < chance:
            qty = random.randint(qty_min, qty_max)
            won.append({**entry, "quantity": qty})
    return won


def _format_loot(loot: list[dict[str, Any]], pool: dict[str, Any]) -> str:
    lines = []
    for item in loot:
        item_id = item.get("item_id") or item.get("id")
        name = item.get("name")
        if not name and item_id and "items" in pool:
            name = pool["items"].get(item_id, {}).get("name", item_id)
        qty = item.get("quantity", 1)
        lines.append(f"• {name or item_id} x{qty}")
    return "\n".join(lines) if lines else get_text("adventure.no_loot")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _get_text(key: str, **kwargs) -> str:
    try:
        return get_text(key, **kwargs)
    except Exception:
        # fallback if key missing in adventure.yaml
        return key


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _locations_keyboard(locations: dict[str, Any]) -> InlineKeyboardMarkup:
    buttons = []
    for loc_id, loc in locations.items():
        btn_text = loc.get("name", loc_id)
        buttons.append(
            [InlineKeyboardButton(btn_text, callback_data=f"adv_loc_{loc_id}")]
        )
    return InlineKeyboardMarkup(buttons)


def _bosses_keyboard(bosses: list[dict[str, Any]], loc_id: str) -> InlineKeyboardMarkup:
    buttons = []
    for idx, boss in enumerate(bosses):
        btn_text = f"{boss.get('name', 'Босс')} | 💰{boss.get('price', 0)}"
        buttons.append(
            [InlineKeyboardButton(btn_text, callback_data=f"adv_boss_{loc_id}_{idx}")]
        )
    buttons.append(
        [InlineKeyboardButton(_get_text("adventure.back"), callback_data="adv_back")]
    )
    return InlineKeyboardMarkup(buttons)


def _confirm_keyboard(loc_id: str, boss_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _get_text("adventure.fight"),
                    callback_data=f"adv_fight_{loc_id}_{boss_idx}",
                )
            ],
            [
                InlineKeyboardButton(
                    _get_text("adventure.cancel"),
                    callback_data="adv_cancel",
                )
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def adventure_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: /adventure — show available locations."""
    locations = load_locations()
    text = _get_text("adventure.welcome")
    await update.effective_message.reply_text(
        text, reply_markup=_locations_keyboard(locations), parse_mode='HTML'
    )


async def adventure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all adventure inline callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user = update.effective_user
    if not user:
        return

    # ------------------------------------------------------------------
    # Back to locations list
    # ------------------------------------------------------------------
    if data == "adv_back":
        locations = load_locations()
        await query.edit_message_text(
            _get_text("adventure.welcome"),
            reply_markup=_locations_keyboard(locations),
            parse_mode='HTML',
        )
        return

    # ------------------------------------------------------------------
    # Cancel / close
    # ------------------------------------------------------------------
    if data == "adv_cancel":
        await query.edit_message_text(_get_text("adventure.cancelled"), parse_mode='HTML')
        return

    # ------------------------------------------------------------------
    # Location selected — show bosses
    # ------------------------------------------------------------------
    if data.startswith("adv_loc_"):
        loc_id = data[len("adv_loc_") :]
        locations = load_locations()
        loc = locations.get(loc_id)
        if not loc:
            await query.edit_message_text(_get_text("adventure.unknown"), parse_mode='HTML')
            return

        bosses = loc.get("bosses", [])
        if not bosses:
            await query.edit_message_text(_get_text("adventure.no_bosses"), parse_mode='HTML')
            return

        loc_text = _get_text(
            "adventure.location_info",
            name=loc.get("name", loc_id),
            desc=loc.get("description", ""),
        )
        await query.edit_message_text(
            loc_text,
            reply_markup=_bosses_keyboard(bosses, loc_id),
            parse_mode='HTML',
        )
        return

    # ------------------------------------------------------------------
    # Boss selected — show confirmation
    # ------------------------------------------------------------------
    if data.startswith("adv_boss_"):
        parts = data.split("_")
        if len(parts) < 4:
            return
        loc_id = parts[2]
        try:
            boss_idx = int(parts[3])
        except ValueError:
            return

        locations = load_locations()
        loc = locations.get(loc_id)
        if not loc:
            return
        bosses = loc.get("bosses", [])
        if boss_idx >= len(bosses):
            return

        boss = bosses[boss_idx]
        player = get_player(user.id)

        # cooldown check
        if _is_on_cooldown(player):
            mins = _cooldown_remaining(player)
            await query.edit_message_text(
                _get_text("adventure.cooldown", minutes=mins),
                parse_mode='HTML',
            )
            return

        # price check
        price = boss.get("price", 0)
        if player.get("gold", 0) < price:
            await query.edit_message_text(
                _get_text("adventure.not_enough_gold", price=price),
                parse_mode='HTML',
            )
            return

        # low hp warning
        hp = player.get("hp", 100)
        hp_warning = ""
        if hp < 20:
            hp_warning = _get_text("adventure.low_hp_warning", hp=hp) + "\n"

        confirm_text = _get_text(
            "adventure.confirm_fight",
            boss_name=boss.get("name", "Босс"),
            price=price,
            hp=boss.get("hp", "?"),
            crit=boss.get("crit_chance", 0) * 100,
        )
        await query.edit_message_text(
            hp_warning + confirm_text,
            reply_markup=_confirm_keyboard(loc_id, boss_idx),
            parse_mode='HTML',
        )
        return

    # ------------------------------------------------------------------
    # Fight!
    # ------------------------------------------------------------------
    if data.startswith("adv_fight_"):
        parts = data.split("_")
        if len(parts) < 4:
            return
        loc_id = parts[2]
        try:
            boss_idx = int(parts[3])
        except ValueError:
            return

        locations = load_locations()
        pool = load_global_pool()
        loc = locations.get(loc_id)
        if not loc:
            return
        bosses = loc.get("bosses", [])
        if boss_idx >= len(bosses):
            return

        boss = bosses[boss_idx]
        player = get_player(user.id)

        # re-checks
        if _is_on_cooldown(player):
            mins = _cooldown_remaining(player)
            await query.edit_message_text(
                _get_text("adventure.cooldown", minutes=mins),
                parse_mode='HTML',
            )
            return

        price = boss.get("price", 0)
        if player.get("gold", 0) < price:
            await query.edit_message_text(
                _get_text("adventure.not_enough_gold", price=price),
                parse_mode='HTML',
            )
            return

        # deduct price
        new_gold = player.get("gold", 0) - price
        update_player(user.id, gold=new_gold, last_fight_time=datetime.now(timezone.utc).isoformat())

        # resolve combat
        result = _resolve_fight(player.get("hp", 100), boss)

        # update player hp in db
        new_hp = result["player_hp_left"]
        player = get_player(user.id)
        player["hp"] = new_hp
        raw_data = load_player_data()
        raw_data[_player_key(user.id)] = player
        save_player_data(raw_data)

        if result["winner"] == "player":
            # loot
            loot_table = loc.get("loot_table", [])
            loot = _roll_loot(loot_table)
            loot_str = _format_loot(loot, pool)

            # save loot to inventory
            if loot:
                inv = player.get("inventory", [])
                for l in loot:
                    inv.append({
                        "item_id": l.get("item_id") or l.get("id"),
                        "name": l.get("name"),
                        "quantity": l.get("quantity", 1),
                    })
                player["inventory"] = inv
                raw_data = load_player_data()
                raw_data[_player_key(user.id)] = player
                save_player_data(raw_data)

            # xp
            xp_gain = boss.get("xp", 10)
            player["xp"] = player.get("xp", 0) + xp_gain
            player["total_fights"] = player.get("total_fights", 0) + 1
            player["wins"] = player.get("wins", 0) + 1
            raw_data = load_player_data()
            raw_data[_player_key(user.id)] = player
            save_player_data(raw_data)

            await query.edit_message_text(
                _get_text(
                    "adventure.victory",
                    boss_name=result["boss_name"],
                    rounds=len(result["rounds"]),
                    hp_left=new_hp,
                    loot=loot_str,
                    xp=xp_gain,
                ),
                parse_mode='HTML',
            )
            return
        else:
            # defeat
            player["total_fights"] = player.get("total_fights", 0) + 1
            raw_data = load_player_data()
            raw_data[_player_key(user.id)] = player
            save_player_data(raw_data)

            await query.edit_message_text(
                _get_text(
                    "adventure.defeat",
                    boss_name=result["boss_name"],
                    rounds=len(result["rounds"]),
                ),
                parse_mode='HTML',
            )
            return


async def adventure_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generic handler for adventure inline buttons (fallback)."""
    await adventure_callback(update, context)
