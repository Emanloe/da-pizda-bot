import logging  # логирование апдейтов и ошибок
from datetime import time  # время ежедневного пидора дня
import pytz  # часовой пояс для джобов
import nest_asyncio  # вложенный asyncio-цикл (PyCharm и т.п.)

# Описание пунктов меню «/» и скоупы, в каких чатах их показывать
from telegram import (
    BotCommand,  # имя и описание одной команды
    BotCommandScopeAllGroupChats,  # меню в группах
    BotCommandScopeAllPrivateChats,  # меню в личке
    BotCommandScopeDefault,  # запасной скоуп, если более узкий не задан
    Update,  # типы апдейтов для polling, включая chosen_inline_result
)

# Сборка бота и маршрутизация апдейтов
from telegram.ext import (
    Application,  # тип приложения (для post_init)
    ApplicationBuilder,  # создание приложения по токену
    CommandHandler,  # сообщения вида /команда
    MessageHandler,  # обычный текст и медиа
    CallbackQueryHandler,  # нажатия inline-кнопок
    InlineQueryHandler,  # инлайн: набор текста в поле ввода без отправки команды
    ChosenInlineResultHandler,  # пользователь выбрал инлайн-результат
    filters,  # отбор апдейтов: текст, фото, тип чата и т.д.
)

# Токен, время пидора дня и часовой пояс джобов
from config import BOT_TOKEN, GAME_HOUR, GAME_MINUTE, DUEL_TIMEZONE
from database import init_db  # создание таблиц SQLite при старте

# Основные команды: старт, топ, админка, дни рождения, тумблеры чата
from handlers.commands import (
    start_command,
    top_command,
    force_pidor_command,
    set_bday_command,
    toggle_forward_reply_command,
    toggle_autodelete_command,
)
from handlers.game import daily_beauty_job  # ежедневный пидор дня
from handlers.past_pizda import schedule_past_pizda_job  # отложенные «пизда» на старые «да»
from handlers.triggers import respond_trigger  # реакции на обычный текст в чате
from handlers.utils import get_file_id_handler, error_handler  # file_id в личке и лог ошибок
from handlers.weather import (
    weather_inline_query,
    weather_chosen_inline_result,
    weather_stub_callback,
)

# Гномья дуэль: бой, выбор соперника, интерактивные ходы, статы, топ, удаление игрока
from handlers.duel import (
    duel_command,
    duel_select_callback,
    duel_action_callback,
    duel_stats_command,
    duel_top_command,
    duel_delete_command,
)

# Нужно, чтобы polling работал внутри уже запущенного asyncio-цикла (PyCharm и т.п.)
nest_asyncio.apply()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Подсказки в меню «/» у Telegram (личка и группы)
BOT_COMMANDS = [
    BotCommand("start", "Старт"),
    BotCommand("top", "Топ пидоров чата"),
    BotCommand("duel", "Гномья дуэль"),
    BotCommand("duel_stats", "Статистика дуэлей"),
    BotCommand("duel_top", "Топ дуэлянтов"),
    BotCommand("force_pidor", "Запустить пидора дня"),
    BotCommand("setbday", "Задать день рождения"),
    BotCommand("duel_delete", "Удалить игрока из дуэлей"),
    BotCommand("toggle_forward", "Реакция на форварды"),
    BotCommand("toggle_autodelete", "Автоудаление команд"),
]


async def post_init(application: Application) -> None:
    """Публикует список команд в Telegram, чтобы они появились в подсказках «/»."""
    for scope in (
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
    ):
        await application.bot.set_my_commands(BOT_COMMANDS, scope=scope)


def main():
    # Создаёт таблицы SQLite, если их ещё нет
    init_db()

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Ежедневный пидор дня и отложенные «пизда» на старые «да»
    tz = pytz.timezone(DUEL_TIMEZONE)
    target_time = time(hour=GAME_HOUR, minute=GAME_MINUTE, second=0, tzinfo=tz)

    if application.job_queue:
        application.job_queue.run_daily(daily_beauty_job, time=target_time)
        schedule_past_pizda_job(application.job_queue)

    # Основные команды чата
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(CommandHandler("force_pidor", force_pidor_command))
    application.add_handler(CommandHandler("setbday", set_bday_command))
    application.add_handler(CommandHandler("toggle_forward", toggle_forward_reply_command))
    application.add_handler(CommandHandler("toggle_autodelete", toggle_autodelete_command))

    # Инлайн-погода: заглушка в выборе, пасхалка подменяется после отправки
    application.add_handler(InlineQueryHandler(weather_inline_query))
    application.add_handler(ChosenInlineResultHandler(weather_chosen_inline_result))
    application.add_handler(CallbackQueryHandler(weather_stub_callback, pattern="^wx"))

    # Дуэли: команда, вызовы, кнопки атак/блоков, статы и админ-удаление
    application.add_handler(CommandHandler("duel", duel_command))
    application.add_handler(CallbackQueryHandler(duel_select_callback, pattern="^start_duel_"))
    application.add_handler(CallbackQueryHandler(duel_action_callback, pattern="^duel_(strike|block)_"))
    application.add_handler(CommandHandler("duel_stats", duel_stats_command))
    application.add_handler(CommandHandler("duel_top", duel_top_command))
    application.add_handler(CommandHandler("duel_delete", duel_delete_command))

    # В личке админу отвечает file_id на фото/гиф/видео/документ
    media_filter = (
        filters.PHOTO | filters.ANIMATION | filters.VIDEO | filters.Document.ALL
    ) & filters.ChatType.PRIVATE
    application.add_handler(MessageHandler(media_filter, get_file_id_handler))

    # Текстовые триггеры (да/нет, ДР, let_do и т.д.), команды сюда не попадают
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, respond_trigger)
    )

    application.add_error_handler(error_handler)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()