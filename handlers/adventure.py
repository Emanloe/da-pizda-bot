"""
Adventure module: turn-based PvE fights at various locations.
Uses:
  data/adventure/locations.json   — location definitions & loot tables
  data/adventure/global_pool.json — global item pool references
  data/adventure/player_data.json — per-player HP / cooldowns / stats
  data/adventure/mobs.json        — mob definitions for encounters
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
MOBS_PATH = os.path.join(ADVENTURE_DIR, "mobs.json")


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


def load_mobs() -> dict[str, Any]:
    return _load_json(MOBS_PATH)


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
            "hp": 200,
            "max_hp": 200,
            "lvl": 1,
            "xp": 0,
            "gold": 0,
            "last_fight_time": None,
            "total_fights": 0,
            "wins": 0,
            "inventory": [],
            "active_expedition": None,
        }
        save_player_data(data)
    player = data[key]
    # ensure inventory field exists for older records
    if "inventory" not in player:
        player["inventory"] = []
    # ensure max_hp field exists
    if "max_hp" not in player:
        player["max_hp"] = 200
    # ensure active_expedition field exists
    if "active_expedition" not in player:
        player["active_expedition"] = None
    return player


def update_player(user_id: int, **fields) -> None:
    data = load_player_data()
    key = _player_key(user_id)
    if key not in data:
        get_player(user_id)  # init
        data = load_player_data()
    data[key].update(fields)
    save_player_data(data)


def _roll_damage(base: int, variance: int = 3) -> int:
    return max(1, base + random.randint(-variance, variance))


# ---------------------------------------------------------------------------
# Combat system (from duel mechanics with zones and blocks)
# ---------------------------------------------------------------------------

def _resolve_encounter_fight(player_hp: int, player_max_hp: int, mob: dict[str, Any]) -> dict[str, Any]:
    """
    Turn-based fight for encounters. Uses zones and blocks.
    Returns dict with winner ('player' | 'mob'), player_hp_left, mob_hp_left,
    rounds list and encounter_result.
    """
    mob_hp = mob.get("hp", 30)
    mob_name = mob.get("name", "Моб")
    mob_dmg_base = mob.get("dmg", 5)
    mob_level = mob.get("level", 1)
    mob_tactics = mob.get("tactics", {})
    mob_debuff_chance = mob_tactics.get("debuff_chance", 0)
    mob_has_overpower = mob_tactics.get("overpower", False)  # like head_on_spider_legs
    
    player_max = player_max_hp
    current_player_hp = player_hp
    current_mob_hp = mob_hp
    rounds = []
    player_debuffed = False  # "Паранойя" debuff
    overpower_stacks = 0
    
    max_rounds = 10
    
    for r in range(1, max_rounds + 1):
        round_data = {"round": r}
        
        # Player's turn
        player_action = random.choice(["attack", "attack", "block"])  # 66% attack, 33% block
        if player_action == "block":
            round_data["player_action"] = "block"
            damage_reduction = 0.5
        else:
            round_data["player_action"] = "attack"
            damage_reduction = 1.0
        
        # Player attacks mob
        if player_action != "block":
            dmg_to_mob = _roll_damage(10, 3)
            current_mob_hp -= dmg_to_mob
            round_data["player_dmg"] = dmg_to_mob
        else:
            round_data["player_dmg"] = 0
        
        # Mob's turn
        if player_debuffed and random.random() < 0.3:
            # Player is stunned by paranoia
            round_data["player_stunned"] = True
            player_debuffed = False
        else:
            # Mob attacks player
            is_crit = random.random() < mob.get("crit_chance", 0.1)
            base_dmg = mob_dmg_base if not is_crit else mob_dmg_base * 2
            dmg_to_player = _roll_damage(int(base_dmg), 2)
            dmg_to_player = int(dmg_to_player * damage_reduction)
            current_player_hp -= dmg_to_player
            
            round_data["mob_dmg"] = dmg_to_player
            round_data["mob_crit"] = is_crit
        
        # Check for overpower (head_on_spider_legs special)
        if mob_has_overpower and mob_hp - current_mob_hp >= 15:
            overpower_stacks += 1
            if overpower_stacks >= 3:
                # Extra damage
                current_player_hp -= 5
                round_data["overpower_damage"] = 5
        
        rounds.append({
            "round": r,
            "player_action": round_data.get("player_action"),
            "player_dmg": round_data.get("player_dmg", 0),
            "mob_dmg": round_data.get("mob_dmg", 0),
            "player_hp": max(0, current_player_hp),
            "mob_hp": max(0, current_mob_hp),
            "player_stunned": round_data.get("player_stunned", False),
        })
        
        # Check debuff chance (bush_with_requirements)
        if mob_debuff_chance > 0 and random.random() < mob_debuff_chance:
            player_debuffed = True
            round_data["player_debuff"] = True
        
        if current_mob_hp <= 0:
            return {
                "winner": "player",
                "player_hp_left": max(0, current_player_hp),
                "mob_hp_left": 0,
                "rounds": rounds,
                "mob_name": mob_name,
                "encounter_result": "victory",
            }
        if current_player_hp <= 0:
            return {
                "winner": "mob",
                "player_hp_left": 0,
                "mob_hp_left": max(0, current_mob_hp),
                "rounds": rounds,
                "mob_name": mob_name,
                "encounter_result": "defeat",
            }
    
    # Draw -> mob escapes
    return {
        "winner": "draw",
        "player_hp_left": max(0, current_player_hp),
        "mob_hp_left": max(0, current_mob_hp),
        "rounds": rounds,
        "mob_name": mob_name,
        "encounter_result": "mob_escaped",
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
# Expedition helpers
# ---------------------------------------------------------------------------

def _select_encounter_mob(loc_id: str, mobs_data: dict[str, Any]) -> dict[str, Any] | None:
    """Select a random mob based on encounter_chance for the location."""
    # Get specific mobs for this location
    specific_mobs = mobs_data.get("specific_mobs", {}).get(loc_id, [])
    
    if not specific_mobs:
        # Fallback to generic mob generation if no specific mobs
        mob_types = mobs_data.get("mob_types", {})
        encounter_weights = mobs_data.get("encounter_weights", {})
        
        # Select rarity based on weights
        rand = random.random()
        rarity = "common"
        cumulative = 0
        for r, weight in encounter_weights.items():
            cumulative += weight
            if rand < cumulative:
                rarity = r
                break
        
        rarity_data = mob_types.get(rarity, {}).get("stats", {})
        if not rarity_data:
            return None
        
        # Generate random mob
        names = mob_types.get(rarity, {}).get("names", {}).get(loc_id, [f"{rarity.capitalize()} Mob"])
        name = random.choice(names)
        
        return {
            "name": name,
            "hp": random.randint(rarity_data.get("hp", {}).get("min", 20), rarity_data.get("hp", {}).get("max", 50)),
            "attack": random.randint(rarity_data.get("attack", {}).get("min", 5), rarity_data.get("attack", {}).get("max", 10)),
            "defense": random.randint(rarity_data.get("defense", {}).get("min", 1), rarity_data.get("defense", {}).get("max", 3)),
            "speed": random.randint(rarity_data.get("speed", {}).get("min", 1), rarity_data.get("speed", {}).get("max", 5)),
            "crit_chance": rarity_data.get("crit_chance", {}).get("min", 0.05),
            "dmg": random.randint(rarity_data.get("attack", {}).get("min", 5), rarity_data.get("attack", {}).get("max", 10)),
            "level": 1,
            "tactics": {},
        }
    
    # Build weighted pool from specific mobs
    weighted_pool = []
    for mob in specific_mobs:
        weight = mob.get("encounter_chance", 1)
        mob_weighted_entry = {
            "name": mob.get("name", "Unknown Mob"),
            "hp": mob.get("hp", 30),
            "attack": mob.get("attack", 5),
            "defense": mob.get("defense", 1),
            "speed": mob.get("speed", 3),
            "crit_chance": mob.get("crit_chance", 0.0),
            "dmg": mob.get("attack", 5),  # dmg from attack
            "level": mob.get("min_level", 1),
            "tactics": mob.get("tactics", {}),
            "special_ability": mob.get("special_ability"),
        }
        for _ in range(int(weight * 100)):  # Scale up for better randomness
            weighted_pool.append(mob_weighted_entry)
    
    if not weighted_pool:
        return None
    
    return random.choice(weighted_pool)


def _format_resource(resource: dict[str, Any]) -> str:
    """Format a resource for display."""
    name = resource.get("name", resource.get("item_id", "Unknown"))
    qty = resource.get("quantity", 1)
    return f"• {name} x{qty}"


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


def _location_details_keyboard(loc_id: str, has_encounters: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    buttons.append(
        [InlineKeyboardButton("⚔️ Отправить гнома в экспедицию", callback_data=f"adv_expedition_{loc_id}")]
    )
    if has_encounters:
        buttons.append(
            [InlineKeyboardButton("◀️ Назад", callback_data="adv_back")]
        )
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# Main handlers
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
    # Location selected — show details
    # ------------------------------------------------------------------
    if data.startswith("adv_loc_"):
        loc_id = data[len("adv_loc_") :]
        locations = load_locations()
        loc = locations.get(loc_id)
        if not loc:
            await query.edit_message_text(_get_text("adventure.unknown"), parse_mode='HTML')
            return
        
        player = get_player(user.id)
        required_level = loc.get("required_level", 1)
        
        # Level check
        if player.get("lvl", 1) < required_level:
            await query.edit_message_text(
                _get_text("adventure.low_level", level=required_level),
                parse_mode='HTML',
            )
            return

        loc_text = _get_text(
            "adventure.location_info",
            name=loc.get("name", loc_id),
            desc=loc.get("description", ""),
        )
        await query.edit_message_text(
            loc_text,
            reply_markup=_location_details_keyboard(loc_id),
            parse_mode='HTML',
        )
        return

    # ------------------------------------------------------------------
    # Start expedition
    # ------------------------------------------------------------------
    if data.startswith("adv_expedition_"):
        loc_id = data[len("adv_expedition_") :]
        locations = load_locations()
        loc = locations.get(loc_id)
        if not loc:
            return

        player = get_player(user.id)
        
        # Check if already on expedition
        if player.get("active_expedition"):
            await query.edit_message_text(
                "⏳ Вы уже в экспедиции! Дождитесь окончания.",
                parse_mode='HTML'
            )
            return

        # Check level
        required_level = loc.get("required_level", 1)
        if player.get("lvl", 1) < required_level:
            await query.edit_message_text(
                _get_text("adventure.low_level", level=required_level),
                parse_mode='HTML',
            )
            return

        duration_seconds = loc.get("duration", 300)  # default 5 minutes
        duration_minutes = duration_seconds // 60
        
        # Create expedition record
        expedition_start = datetime.now(timezone.utc)
        expedition_end = expedition_start + timedelta(seconds=duration_seconds)
        
        expedition_data = {
            "location_id": loc_id,
            "location_name": loc.get("name", loc_id),
            "start_time": expedition_start.isoformat(),
            "end_time": expedition_end.isoformat(),
            "duration_seconds": duration_seconds,
        }
        
        update_player(user.id, active_expedition=expedition_data)
        
        # Delete the location message and send expedition start message
        await query.delete_message()
        
        expedition_text = _get_text(
            "adventure.expedition_start",
            username=user.username or user.first_name,
            location_name=loc.get("name", loc_id),
            duration=duration_minutes
        )
        await update.effective_message.reply_text(
            expedition_text,
            parse_mode='HTML'
        )
        
        # Schedule completion message
        await context.job_queue.run(
            _expedition_complete_callback,
            duration_seconds,
            data={"user_id": user.id, "loc_id": loc_id, "username": user.username or user.first_name}
        )
        
        return

    # ------------------------------------------------------------------
    # Legacy boss-related callbacks (deprecated but kept for compatibility)
    # ------------------------------------------------------------------
    if data.startswith("adv_boss_") or data.startswith("adv_fight_"):
        await query.edit_message_text(
            "⚠️ Этот функционал обновлен. Используйте экспедиции!",
            parse_mode='HTML'
        )
        return


async def _expedition_complete_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when expedition timer expires."""
    job = context.job
    user_id = job.data.get("user_id")
    loc_id = job.data.get("loc_id")
    username = job.data.get("username", "Гном")
    
    locations = load_locations()
    loc = locations.get(loc_id, {})
    mobs_data = load_mobs()
    pool = load_global_pool()
    
    player = get_player(user_id)
    current_hp = player.get("hp", 200)
    max_hp = player.get("max_hp", 200)
    
    # Roll resources from location
    loot_table = loc.get("loot_table", [])
    resources = _roll_loot(loot_table)
    
    # 50% chance for encounter during expedition
    has_encounter = random.random() < 0.5
    encounter_result = None
    encounter_mob_name = None
    encounter_hp_left = current_hp
    xp_gained = 0
    hp_healed = 0
    
    if has_encounter:
        mob = _select_encounter_mob(loc_id, mobs_data)
        if mob:
            # Start fight
            fight_result = _resolve_encounter_fight(current_hp, max_hp, mob)
            encounter_result = fight_result["encounter_result"]
            encounter_mob_name = fight_result["mob_name"]
            encounter_hp_left = fight_result["player_hp_left"]
            
            if fight_result["winner"] == "player":
                # Victory rewards
                xp_gained += 10
                hp_healed = min(20, max_hp - encounter_hp_left)
                encounter_hp_left += hp_healed
            else:
                # Defeat - additional HP loss
                encounter_hp_left = max(1, encounter_hp_left - 5)
    
    # Update player stats
    new_hp = min(max_hp, encounter_hp_left)
    update_player(
        user_id,
        hp=new_hp,
        xp=player.get("xp", 0) + xp_gained,
        active_expedition=None,
        total_expeditions=player.get("total_expeditions", 0) + 1,
    )
    
    # Save resources to player inventory
    if resources:
        inventory = player.get("inventory", [])
        for res in resources:
            inventory.append(res)
        update_player(user_id, inventory=inventory)
    
    # Build result message using texts from adventure.yaml
    result_lines = []
    
    # Resources
    if resources:
        res_list = "\n".join([_format_resource(r) for r in resources])
        result_lines.append(_get_text("adventure.expedition_resources", resources=res_list))
    else:
        result_lines.append(_get_text("adventure.expedition_no_resources"))
    
    # Encounter summary
    encounters_summary = []
    encounter_message = None
    if has_encounter and encounter_mob_name:
        if encounter_result == "victory":
            encounters_summary.append(_get_text("adventure.mob_killed", mob_name=encounter_mob_name))
            encounters_summary.append(_get_text("adventure.reward_xp", xp=xp_gained))
            if hp_healed > 0:
                encounters_summary.append(f"❤️‍🩹 +{hp_healed} HP")
        elif encounter_result == "defeat":
            encounters_summary.append(_get_text("adventure.mob_survived", mob_name=encounter_mob_name))
            encounter_message = _get_text("adventure.expedition_result_failure")
        else:
            encounters_summary.append(_get_text("adventure.mob_survived", mob_name=encounter_mob_name))
    
    # Determine expedition result status
    if encounter_result == "defeat":
        result_status = _get_text("adventure.expedition_result_fail")
    elif encounter_result == "victory" and resources:
        result_status = _get_text("adventure.expedition_result_success")
    elif resources:
        result_status = _get_text("adventure.expedition_result_success")
    else:
        result_status = _get_text("adventure.expedition_result_failure")
    
    # Build final summary text
    summary_lines = [
        _get_text("adventure.expedition_final_summary",
            username=username,
            hp=new_hp,
            max_hp=max_hp,
            total_xp=xp_gained,
            resources_summary="\n".join(result_lines),
            encounters_summary="\n".join(encounters_summary) if encounters_summary else ""
        )
    ]
    
    # Send completion message
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="\n".join(summary_lines),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error sending expedition completion to user {user_id}: {e}")
