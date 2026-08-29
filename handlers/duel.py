import asyncio
import json
import random
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import DICK_STEAL_CHANCE, TOP_SORT_BY, ADMIN_IDS, WINNER_100_PTS_GIF, MAX_DAILY_POINTS
from database import (
    get_or_create_duel_user,
    get_duel_user_by_username,
    delete_duel_user_by_username,
    execute_duel_transaction,
    get_duel_top,
    format_user_title
)

AUTO_DELETE_DELAY = 60
MOVE_TIMEOUT = 10  # 10 секунд на ход

_DWARFS_FACTS_PATH = Path(__file__).resolve().parent.parent / "data" / "dwarfs_facts.json"
with open(_DWARFS_FACTS_PATH, encoding="utf-8") as _facts_file:
    DWARFS_FACTS = tuple(json.load(_facts_file)["facts"])

# Хранилище активных дуэлей в памяти: state[chat_id] = { ... }
ACTIVE_DUELS = {}

# --- СЛОВАРИ ДЛЯ КНОПОК И ТЕКСТА ---
TARGET_NAMES = {
    "head": "Голова 🧠",
    "body": "Торс 🛡️",
    "dick": "Хуй 🍆"
}

ATTACK_PHRASES = [
    "замахивается кухонным тесаком",
    "делает резкий выпад финкой",
    "целится заточенным кованым ножом",
    "производит стремительный выпад с ножом",
    "пытается нанести коварный тычок",
    "выполняет молниеносную атаку клинком"
]

HIT_PHRASES = [
    "точно вонзает лезвие в цель!",
    "пробивает оборону и наносит сокрушительный удар!",
    "находит уязвимое место и чисто побеждает!",
    "сбивает соперника с ног отличным выпадом!",
    "завершает поединок точным попаданием!"
]

BLOCK_PHRASES = [
    "успевает подставить гарду и парирует удар!",
    "ловко отскакивает и блокирует выпад!",
    "предугадывает траекторию и защищает уязвимое место!",
    "слышит звон стали — блок сработал идеально!",
    "замечает замах и вовремя перекрывает зону атаки!"
]

MISS_PHRASES = [
    "поскальзывается на ровном месте и режет воздух!",
    "теряет равновесие и чиркает ножом по стене!",
    "промахивается буквально в миллиметре от цели!",
    "зацепляется сапогом за корень и шлепается мимо!",
    "выпускает нож из влажных ладоней, теряя момент!"
]

SUICIDE_PHRASES = [
    "пытается сделать крученый финт, но вонзает нож себе в ногу!",
    "спотыкается о собственный плащ и падая натыкается на свое же лезвие!",
    "решает подбросить нож для красоты, но не ловит его и выбивает сам себя!",
    "выполняет опасный кульбит и случайно нокаутирует себя рукоятью!",
    "переусердствовал с замахом и наносит критический урон самому себе!"
]

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def delete_messages_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data.get("chat_id")
    message_ids = job_data.get("message_ids", [])

    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


def schedule_auto_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list[int]):
    if context.job_queue:
        context.job_queue.run_once(
            delete_messages_job,
            when=AUTO_DELETE_DELAY,
            data={"chat_id": chat_id, "message_ids": message_ids}
        )


def _extract_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if context.args:
        return context.args[0].strip().lstrip("@")

    if update.message and update.message.text:
        parts = update.message.text.split()
        if len(parts) > 1:
            return parts[1].strip().lstrip("@")

    return None


async def send_and_schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML"
):
    chat_id = update.effective_chat.id
    msg_id_to_delete = update.message.message_id if update.message else None

    try:
        if update.message:
            bot_msg = await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            bot_msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        bot_msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)

    to_delete = [bot_msg.message_id]
    if msg_id_to_delete:
        to_delete.append(msg_id_to_delete)

    schedule_auto_delete(context, chat_id=chat_id, message_ids=to_delete)


# --- ЛОГИКА ИНТЕРАКТИВНОГО БОЯ ---

def _get_strike_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🎯 Голова", callback_data="duel_strike_head"),
            InlineKeyboardButton("🛡️ Торс", callback_data="duel_strike_body"),
            InlineKeyboardButton("🍆 Хуй", callback_data="duel_strike_dick"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def _get_block_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🛡️ Голова", callback_data="duel_block_head"),
            InlineKeyboardButton("🛡️ Торс", callback_data="duel_block_body"),
            InlineKeyboardButton("🛡️ Хуй", callback_data="duel_block_dick"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


async def _start_interactive_fight(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    attacker_tg,
    defender_tg,
    attacker_data: dict,
    defender_data: dict,
    original_msg_id: int = None
):
    duel_state = {
        "attacker_tg": attacker_tg,
        "defender_tg": defender_tg,
        "attacker_data": attacker_data,
        "defender_data": defender_data,
        "phase": "attack",  # "attack" или "block"
        "attack_zone": None,
        "round": 1,
        "message_id": None,
        "turn_task": None,
        "original_msg_id": original_msg_id
    }
    ACTIVE_DUELS[chat_id] = duel_state

    att_title = format_user_title(attacker_data)
    def_title = format_user_title(defender_data)

    text = (
        f"🗡️ <b>Гномья дуэль начинается!</b>\n\n"
        f"⚔️ Атакует: <b>{att_title}</b>\n"
        f"🛡️ Защищается: <b>{def_title}</b>\n\n"
        f"⏳ У <b>{att_title}</b> есть {MOVE_TIMEOUT} секунд, чтобы выбрать точку удара:"
    )

    bot_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=_get_strike_keyboard()
    )

    duel_state["message_id"] = bot_msg.message_id

    task = asyncio.create_task(_auto_move_timer(context, chat_id, duel_state["round"], phase="attack"))
    duel_state["turn_task"] = task


async def _auto_move_timer(context: ContextTypes.DEFAULT_TYPE, chat_id: int, round_num: int, phase: str):
    await asyncio.sleep(MOVE_TIMEOUT)
    duel = ACTIVE_DUELS.get(chat_id)

    if duel and duel.get("round") == round_num and duel.get("phase") == phase:
        random_choice = random.choice(["head", "body", "dick"])
        
        if phase == "attack":
            att_title = format_user_title(duel["attacker_data"])
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏰ <b>{att_title}</b> зазевался! Гномий синедрион делает случайный выбор атаки...",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await _process_attack_choice(context, chat_id, random_choice)
        elif phase == "block":
            def_title = format_user_title(duel["defender_data"])
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏰ <b>{def_title}</b> зазевался! Гномий синедрион делает случайный выбор блока...",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await _process_block_choice(context, chat_id, random_choice)


async def duel_strike_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    chat_id = update.effective_chat.id
    duel = ACTIVE_DUELS.get(chat_id)

    if not duel:
        await query.answer("Дуэль не найдена или уже завершена.", show_alert=True)
        return

    # ОБРАБОТКА ШАГА 1: ВЫБОР АТАКИ
    if query.data.startswith("duel_strike_"):
        if duel["phase"] != "attack":
            await query.answer("Атака уже выбрана! Ждем блок.", show_alert=True)
            return

        if query.from_user.id != duel["attacker_tg"].id:
            await query.answer("Сейчас не ваш ход для атаки!", show_alert=True)
            return

        await query.answer()

        if duel.get("turn_task") and not duel["turn_task"].done():
            duel["turn_task"].cancel()

        target_zone = query.data.replace("duel_strike_", "")
        await _process_attack_choice(context, chat_id, target_zone)
        return

    # ОБРАБОТКА ШАГА 2: ВЫБОР БЛОКА
    if query.data.startswith("duel_block_"):
        if duel["phase"] != "block":
            await query.answer("Сейчас не фаза блока!", show_alert=True)
            return

        if query.from_user.id != duel["defender_tg"].id:
            await query.answer("Сейчас не ваш ход для защиты!", show_alert=True)
            return

        await query.answer()

        if duel.get("turn_task") and not duel["turn_task"].done():
            duel["turn_task"].cancel()

        block_zone = query.data.replace("duel_block_", "")
        await _process_block_choice(context, chat_id, block_zone)
        return


# Алиас для совместимости с bot.py
duel_action_callback = duel_strike_callback


async def _process_attack_choice(context: ContextTypes.DEFAULT_TYPE, chat_id: int, strike_zone: str):
    duel = ACTIVE_DUELS.get(chat_id)
    if not duel:
        return

    duel["attack_zone"] = strike_zone
    duel["phase"] = "block"

    att_title = format_user_title(duel["attacker_data"])
    def_title = format_user_title(duel["defender_data"])

    text = (
        f"🗡️ <b>Гномья дуэль! Раунд {duel['round']}</b>\n\n"
        f"⚔️ <b>{att_title}</b> наносит замах!\n"
        f"🛡️ <b>{def_title}</b>, выберите зону защиты!\n\n"
        f"⏳ У <b>{def_title}</b> есть {MOVE_TIMEOUT} секунд на выбор блока:"
    )

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=duel["message_id"],
            text=text,
            parse_mode="HTML",
            reply_markup=_get_block_keyboard()
        )
    except Exception:
        bot_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_get_block_keyboard()
        )
        duel["message_id"] = bot_msg.message_id

    task = asyncio.create_task(_auto_move_timer(context, chat_id, duel["round"], phase="block"))
    duel["turn_task"] = task


async def _process_block_choice(context: ContextTypes.DEFAULT_TYPE, chat_id: int, block_zone: str):
    duel = ACTIVE_DUELS.get(chat_id)
    if not duel:
        return

    strike_zone = duel["attack_zone"]
    attacker_data = duel["attacker_data"]
    defender_data = duel["defender_data"]
    att_title = format_user_title(attacker_data)
    def_title = format_user_title(defender_data)

    # 1. Шанс 1% — Самоубийство атаковавшего
    if random.random() < 0.01:
        suicide_phrase = random.choice(SUICIDE_PHRASES)
        res_text = (
            f"💥 <b>НЕВЕРОЯТНЫЙ ИСХОД!</b>\n\n"
            f"<b>{att_title}</b> {suicide_phrase}\n\n"
            f"🏆 Победитель по глупости соперника: <b>{def_title}</b>!"
        )
        await _finish_duel(
            context, 
            chat_id, 
            winner=defender_data, 
            loser=attacker_data, 
            custom_text=res_text,
            strike_zone=strike_zone,
            block_zone=block_zone
        )
        return

    # 2. Шанс 5% — Промах
    if random.random() < 0.05:
        miss_phrase = random.choice(MISS_PHRASES)
        att_action = random.choice(ATTACK_PHRASES)
        
        # Смена ролей
        duel["attacker_tg"], duel["defender_tg"] = duel["defender_tg"], duel["attacker_tg"]
        duel["attacker_data"], duel["defender_data"] = duel["defender_data"], duel["attacker_data"]
        duel["phase"] = "attack"
        duel["attack_zone"] = None
        duel["round"] += 1

        new_att_title = format_user_title(duel["attacker_data"])
        new_def_title = format_user_title(duel["defender_data"])

        text = (
            f"💨 <b>ПРОМАХ!</b>\n"
            f"<b>{att_title}</b> {att_action} в зону ({TARGET_NAMES[strike_zone]}), но {miss_phrase}\n\n"
            f"🔄 <b>Смена ролей!</b>\n"
            f"⚔️ Атакует: <b>{new_att_title}</b>\n"
            f"🛡️ Защищается: <b>{new_def_title}</b>\n\n"
            f"⏳ У <b>{new_att_title}</b> есть {MOVE_TIMEOUT} секунд на удар:"
        )

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=duel["message_id"],
                text=text,
                parse_mode="HTML",
                reply_markup=_get_strike_keyboard()
            )
        except Exception:
            bot_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=_get_strike_keyboard()
            )
            duel["message_id"] = bot_msg.message_id

        task = asyncio.create_task(_auto_move_timer(context, chat_id, duel["round"], phase="attack"))
        duel["turn_task"] = task
        return

    # 3. Сравнение УДАРА и БЛОКА
    if strike_zone == block_zone:
        block_phrase = random.choice(BLOCK_PHRASES)
        att_action = random.choice(ATTACK_PHRASES)

        # Смена ролей
        duel["attacker_tg"], duel["defender_tg"] = duel["defender_tg"], duel["attacker_tg"]
        duel["attacker_data"], duel["defender_data"] = duel["defender_data"], duel["attacker_data"]
        duel["phase"] = "attack"
        duel["attack_zone"] = None
        duel["round"] += 1

        new_att_title = format_user_title(duel["attacker_data"])
        new_def_title = format_user_title(duel["defender_data"])

        text = (
            f"🛡️ <b>БЛОК СРАБОТАЛ!</b>\n"
            f"<b>{att_title}</b> {att_action} в зону ({TARGET_NAMES[strike_zone]}), но <b>{def_title}</b> {block_phrase}\n\n"
            f"🔄 <b>Инициатива переходит!</b>\n"
            f"⚔️ Атакует: <b>{new_att_title}</b>\n"
            f"🛡️ Защищается: <b>{new_def_title}</b>\n\n"
            f"⏳ У <b>{new_att_title}</b> есть {MOVE_TIMEOUT} секунд на удар:"
        )

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=duel["message_id"],
                text=text,
                parse_mode="HTML",
                reply_markup=_get_strike_keyboard()
            )
        except Exception:
            bot_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=_get_strike_keyboard()
            )
            duel["message_id"] = bot_msg.message_id

        task = asyncio.create_task(_auto_move_timer(context, chat_id, duel["round"], phase="attack"))
        duel["turn_task"] = task

    else:
        hit_phrase = random.choice(HIT_PHRASES)
        att_action = random.choice(ATTACK_PHRASES)

        res_text = (
            f"💥 <b>ТОЧНЫЙ УДАР!</b>\n"
            f"<b>{att_title}</b> {att_action} в зону ({TARGET_NAMES[strike_zone]}), а <b>{def_title}</b> блокировал ({TARGET_NAMES[block_zone]}).\n"
            f"<b>{att_title}</b> {hit_phrase}\n"
        )
        await _finish_duel(
            context, 
            chat_id, 
            winner=attacker_data, 
            loser=defender_data, 
            custom_text=res_text,
            strike_zone=strike_zone,
            block_zone=block_zone
        )


async def _finish_duel(
    context: ContextTypes.DEFAULT_TYPE, 
    chat_id: int, 
    winner: dict, 
    loser: dict, 
    custom_text: str,
    strike_zone: str = None,
    block_zone: str = None
):
    duel = ACTIVE_DUELS.pop(chat_id, None)

    # Динамический расчет шанса кражи
    steal_chance = DICK_STEAL_CHANCE
    if strike_zone == "dick":
        steal_chance *= 2.0  # Повышаем шанс при ударе в пах
    if block_zone == "dick":
        steal_chance /= 4.0  # Кратно понижаем шанс при блоке паха

    steal_chance = max(0.0, min(1.0, steal_chance))
    is_dick_stolen = random.random() < steal_chance

    try:
        w_after, l_after = execute_duel_transaction(
            chat_id=chat_id,
            winner_user=winner,
            loser_user=loser,
            is_dick_stolen=is_dick_stolen
        )
    except Exception:
        bot_msg = await context.bot.send_message(chat_id, "⚠️ Ошибка проведения дуэли. Попробуйте снова.")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    win_title = format_user_title(winner)
    lose_title = format_user_title(loser)

    res_msg = (
        f"{custom_text}\n"
        f"🗡️ <b>Результаты дуэли:</b>\n\n"
        f"Победитель: <b>{win_title}</b>\n"
        f"Проигравший: <b>{lose_title}</b>\n\n"
        f"<b>{win_title}</b>: +10 очков ({w_after}/100)\n"
        f"<b>{lose_title}</b>: -5 очков ({l_after}/100)\n"
    )

    if is_dick_stolen:
        fact = random.choice(DWARFS_FACTS)
        res_msg += (
            f"\n💀 <b>И ВДОБАВОК У НЕГО УКРАЛИ ХУЙ.</b>\n\n"
            f"Сегодня {lose_title} больше не может драться.\n\n"
            f"📖 <i>{fact}</i>"
        )

    if duel and duel.get("message_id"):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=duel["message_id"])
        except Exception:
            pass

    bot_msg = await context.bot.send_message(chat_id, res_msg, parse_mode="HTML")

    to_delete = []
    if not is_dick_stolen:
        to_delete.append(bot_msg.message_id)
    if duel and duel.get("original_msg_id"):
        to_delete.append(duel["original_msg_id"])

    if to_delete:
        schedule_auto_delete(context, chat_id, to_delete)

    reached_max = winner["points"] < MAX_DAILY_POINTS and w_after >= MAX_DAILY_POINTS
    if reached_max and WINNER_100_PTS_GIF:
        try:
            await context.bot.send_animation(
                chat_id=chat_id,
                animation=WINNER_100_PTS_GIF,
                caption=f"🏆 <b>{win_title}</b> набрал {MAX_DAILY_POINTS} очков!",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _process_duel_fight(
    context: ContextTypes.DEFAULT_TYPE,
    initiator_tg,
    target_username: str,
    chat_id: int,
    original_msg_id: int = None
):
    if chat_id in ACTIVE_DUELS:
        bot_msg = await context.bot.send_message(chat_id, "⚔️ В этом чате уже идет дуэль! Дождитесь ее окончания.")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    if initiator_tg.username and initiator_tg.username.lower() == target_username.lower():
        bot_msg = await context.bot.send_message(chat_id, "⚔️ Нельзя вызвать на дуэль самого себя!")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    initiator = get_or_create_duel_user(initiator_tg, chat_id)
    init_title = format_user_title(initiator)

    if initiator["dick_stolen_today"]:
        bot_msg = await context.bot.send_message(chat_id, f"💀 <b>{init_title}</b> сегодня уже без хуя. До завтра драться нельзя.", parse_mode="HTML")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    if initiator["points"] <= 0:
        bot_msg = await context.bot.send_message(chat_id, "⚔️ У вас 0 очков. Вы больше не можете драться сегодня.")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    opponent = get_duel_user_by_username(target_username, chat_id)
    if not opponent:
        bot_msg = await context.bot.send_message(chat_id, f"❌ Пользователь <b>{target_username}</b> не найден в базе этого чата.", parse_mode="HTML")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    if opponent["user_id"] == initiator["user_id"]:
        bot_msg = await context.bot.send_message(chat_id, "⚔️ Нельзя вызвать на дуэль самого себя!")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    opp_title = format_user_title(opponent)

    if opponent["dick_stolen_today"]:
        bot_msg = await context.bot.send_message(chat_id, f"💀 <b>{opp_title}</b> сегодня уже без хуя. До завтра драться нельзя.", parse_mode="HTML")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    if opponent["points"] <= 0:
        bot_msg = await context.bot.send_message(chat_id, f"⚔️ <b>{opp_title}</b> больше не может драться сегодня — у него 0 очков.", parse_mode="HTML")
        schedule_auto_delete(context, chat_id, [bot_msg.message_id])
        return

    class SimpleTGUser:
        def __init__(self, uid, uname):
            self.id = uid
            self.username = uname

    opponent_tg = SimpleTGUser(opponent["user_id"], opponent["username"])

    await _start_interactive_fight(
        context=context,
        chat_id=chat_id,
        attacker_tg=initiator_tg,
        defender_tg=opponent_tg,
        attacker_data=initiator,
        defender_data=opponent,
        original_msg_id=original_msg_id
    )


# --- КОМАНДЫ ХЭНДЛЕРА ---

async def duel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user or not update.message.chat:
        return

    chat_id = update.message.chat_id
    initiator_tg = update.message.from_user
    target_username = _extract_username(update, context)

    if not target_username:
        top_list = get_duel_top(chat_id=chat_id, limit=20)
        keyboard = []
        for row in top_list:
            username, display_name, _, _, _ = row

            if not username:
                continue

            if initiator_tg.username and initiator_tg.username.lower() == username.lower():
                continue

            opponent = get_duel_user_by_username(username, chat_id)
            if not opponent:
                continue

            if opponent["points"] <= 0 or opponent["dick_stolen_today"]:
                continue

            clean_label = (display_name or username).lstrip("@")
            label = f"⚔️ {clean_label}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"start_duel_{username}")])

        if not keyboard:
            await send_and_schedule(update, context, "❌ В чате нет доступных соперников для дуэли (все без очков или без хуев).")
            return

        reply_markup = InlineKeyboardMarkup(keyboard)
        await send_and_schedule(update, context, "🗡️ <b>Выберите соперника для дуэли:</b>", reply_markup=reply_markup)
        return

    await _process_duel_fight(context, initiator_tg, target_username, chat_id, original_msg_id=update.message.message_id)


async def duel_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("start_duel_"):
        return

    target_username = query.data.replace("start_duel_", "")
    initiator_tg = query.from_user
    chat_id = update.effective_chat.id

    await query.answer()

    try:
        await query.message.delete()
    except Exception:
        pass

    await _process_duel_fight(context, initiator_tg, target_username, chat_id)


async def duel_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user or not update.message.chat:
        return

    chat_id = update.message.chat_id
    user = get_or_create_duel_user(update.message.from_user, chat_id)
    title = format_user_title(user)

    status = "Без хуя 💀" if user["dick_stolen_today"] else "С хуем 🍆"

    text = (
        f"📊 <b>Статистика дуэлей: {title}</b>\n\n"
        f"Очки: <b>{user['points']} / 100</b>\n"
        f"Побед: <b>{user['wins']}</b>\n"
        f"Поражений: <b>{user['losses']}</b>\n"
        f"Украдено хуев: <b>{user['stolen_dicks_count']}</b>\n"
        f"Статус на сегодня: <b>{status}</b>"
    )

    await send_and_schedule(update, context, text)


async def duel_top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.chat:
        return

    chat_id = update.message.chat_id
    top = get_duel_top(chat_id=chat_id, limit=10)

    if not top:
        await send_and_schedule(update, context, "🏆 Таблица лидеров чата пока пуста.")
        return

    sort_label = "очкам" if TOP_SORT_BY == "points" else "победам"
    text = f"🏆 <b>Топ-10 гномьих дуэлянтов чата (по {sort_label}):</b>\n\n"

    for idx, row in enumerate(top, 1):
        username, display_name, wins, losses, points = row
        raw_name = display_name or username or "Гном"
        clean_name = raw_name.lstrip("@")
        text += f"{idx}. <b>{clean_name}</b> — {points} очков ({wins}W / {losses}L)\n"

    await send_and_schedule(update, context, text)


async def duel_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user or not update.message.chat:
        return

    user_id = update.message.from_user.id
    if user_id not in ADMIN_IDS:
        await send_and_schedule(update, context, "⛔ Недостаточно прав.")
        return

    target_username = _extract_username(update, context)
    if not target_username:
        await send_and_schedule(update, context, "⚠️ Укажите ник: <code>/duel_delete username</code>")
        return

    chat_id = update.message.chat_id
    deleted = delete_duel_user_by_username(target_username, chat_id)

    clean_target = target_username.lstrip("@")
    if deleted:
        await send_and_schedule(update, context, f"✅ Пользователь {clean_target} удален из базы дуэлей этого чата.")
    else:
        await send_and_schedule(update, context, f"❌ Пользователь {clean_target} не найден в базе этого чата.")