import random
import uuid
import requests
from telegram import (
    Update,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InputTextMessageContent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaAnimation,
)
from telegram.ext import ContextTypes
from config import (
    OREL_GIF_IDS,
    RUSSIA_GIF_FILE_ID,
    PERM_PHOTO_IDS,
    MOSCOW_PHOTO_IDS,
    SPB_PHOTO_IDS,
    NSK_PHOTO_IDS,
    WEATHER_STUB_PHOTO_ID,
)

WEATHER_CODES = {
    0: ("☀️", "Ясно"),
    1: ("🌤️", "Преимущественно ясно"),
    2: ("⛅", "Переменная облачность"),
    3: ("☁️", "Пасмурно"),
    45: ("🌫️", "Туман"),
    48: ("🌫️", "Оседающий туман"),
    51: ("🌧️", "Лёгкая морось"),
    53: ("🌧️", "Морось"),
    55: ("🌧️", "Плотная морось"),
    61: ("☔", "Слабый дождь"),
    63: ("☔", "Умеренный дождь"),
    65: ("🌧️", "Сильный дождь"),
    71: ("❄️", "Слабый снег"),
    73: ("❄️", "Снегопад"),
    75: ("❄️", "Сильный снегопад"),
    80: ("🌦️", "Ливень"),
    95: ("⛈️", "Гроза"),
}

SPB_ALIASES = (
    "питер",
    "спб",
    "петербург",
    "санкт-петербург",
    "санкт петербург",
    "spb",
    "petersburg",
)
NSK_ALIASES = ("новосибирск", "новосиб", "нск", "nsk", "novosibirsk")
MOSCOW_ALIASES = ("москва", "moscow", "мск", "msk")
PERM_ALIASES = ("пермь", "perm")

# Короткие названия → то, что уходит в геокодинг
CITY_GEO_QUERY = {}
for _alias in SPB_ALIASES:
    CITY_GEO_QUERY[_alias] = "Санкт-Петербург"
for _alias in MOSCOW_ALIASES:
    CITY_GEO_QUERY[_alias] = "Москва"
for _alias in NSK_ALIASES:
    CITY_GEO_QUERY[_alias] = "Новосибирск"
for _alias in PERM_ALIASES:
    CITY_GEO_QUERY[_alias] = "Пермь"
for _alias in ("орел", "орёл"):
    CITY_GEO_QUERY[_alias] = "Орёл"


def _geo_query_name(city: str) -> str:
    """Каноническое имя города для Geocoding API."""
    return CITY_GEO_QUERY.get(city.strip().lower(), city.strip())


def _bonus_photo(query: str, api_city_name: str = "") -> str | None:
    """Фото-пасхалка по тому городу, который уйдёт в прогноз."""
    names = [query.strip().lower(), api_city_name.strip().lower()]
    for city_normalized in names:
        if not city_normalized:
            continue
        if city_normalized in MOSCOW_ALIASES and MOSCOW_PHOTO_IDS:
            return random.choice(MOSCOW_PHOTO_IDS)
        if city_normalized in SPB_ALIASES and SPB_PHOTO_IDS:
            return random.choice(SPB_PHOTO_IDS)
        if city_normalized in NSK_ALIASES and NSK_PHOTO_IDS:
            return random.choice(NSK_PHOTO_IDS)
        if city_normalized in PERM_ALIASES and PERM_PHOTO_IDS:
            return random.choice(PERM_PHOTO_IDS)
    return None


def _bonus_gif(query: str, api_city_name: str = "") -> str | None:
    """Гиф-пасхалка по тому городу, который уйдёт в прогноз."""
    names = [query.strip().lower(), api_city_name.strip().lower()]
    for city_normalized in names:
        if not city_normalized:
            continue
        if city_normalized in ["орел", "орёл"] and OREL_GIF_IDS:
            return random.choice(OREL_GIF_IDS)
        if city_normalized == "россия" and RUSSIA_GIF_FILE_ID:
            return RUSSIA_GIF_FILE_ID
    return None


def _fetch_weather_html(city: str) -> tuple[str, str] | None:
    """Текст прогноза в HTML и имя города из API, либо None."""
    geo_name = _geo_query_name(city)

    # 1. Поиск координат через Geocoding API
    geo_res = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": geo_name, "count": 1, "language": "ru"},
        timeout=5,
    ).json()

    if not geo_res.get("results"):
        return None

    location = geo_res["results"][0]
    lat, lon = location["latitude"], location["longitude"]
    city_name = location.get("name", geo_name)
    country = location.get("country", "")

    # 2. Запрос погодных данных (ветер в м/с)
    weather_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&current_weather=true&"
        f"hourly=relativehumidity_2m,apparent_temperature&"
        f"windspeed_unit=ms&timezone=auto"
    )
    w_res = requests.get(weather_url, timeout=5).json()
    current = w_res.get("current_weather", {})

    temp = round(current.get("temperature", 0))
    wind_speed = round(current.get("windspeed", 0))
    code = current.get("weathercode", 0)

    emoji, desc = WEATHER_CODES.get(code, ("🌡️", "Неизвестно"))
    apparent = round(w_res.get("hourly", {}).get("apparent_temperature", [temp])[0])

    return (
        f"<b>Погода в {city_name}</b> {country}\n\n"
        f"{emoji} <b>{desc}</b>\n"
        f"🌡️ Температура: <b>{temp}°C</b> (ощущается как {apparent}°C)\n"
        f"💨 Ветер: <b>{wind_speed} м/с</b>\n"
    ), city_name


def _inline_id() -> str:
    return uuid.uuid4().hex


_ATTACH_STORE = "weather_attach"


def _stub_keyboard(result_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("\u2060", callback_data="wx" + result_id[:16])]]
    )


def _store_attach(
    context: ContextTypes.DEFAULT_TYPE,
    result_id: str,
    kind: str,
    file_id: str,
    caption: str,
) -> None:
    pending = context.bot_data.setdefault(_ATTACH_STORE, {})
    pending[result_id] = (kind, file_id, caption)
    extra = len(pending) - 200
    if extra > 0:
        for key in list(pending)[:extra]:
            pending.pop(key, None)


def _peek_attach(context: ContextTypes.DEFAULT_TYPE, result_id: str | None):
    pending = context.bot_data.get(_ATTACH_STORE) or {}
    if result_id and result_id in pending:
        return pending[result_id]
    return None


def _pop_attach(context: ContextTypes.DEFAULT_TYPE, result_id: str | None, short_id: str | None):
    pending = context.bot_data.get(_ATTACH_STORE) or {}
    if result_id and result_id in pending:
        return pending.pop(result_id)
    if short_id:
        for key in list(pending):
            if key.startswith(short_id):
                return pending.pop(key)
    return None


async def _replace_stub_media(context: ContextTypes.DEFAULT_TYPE, inline_message_id: str, attach) -> None:
    kind, file_id, caption = attach
    if kind == "photo":
        media = InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML")
    else:
        media = InputMediaAnimation(media=file_id, caption=caption, parse_mode="HTML")
    await context.bot.edit_message_media(
        inline_message_id=inline_message_id,
        media=media,
        reply_markup=None,
    )


async def weather_replace_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await _replace_stub_media(context, data["imid"], data["attach"])
        _pop_attach(context, data.get("result_id"), None)
    except Exception:
        attempt = data.get("attempt", 1)
        if attempt < 6 and context.job_queue:
            context.job_queue.run_once(
                weather_replace_job,
                when=0.5,
                data={**data, "attempt": attempt + 1},
            )


async def _replace_now_or_retry(
    context: ContextTypes.DEFAULT_TYPE,
    inline_message_id: str,
    attach,
    result_id: str | None,
) -> None:
    try:
        await _replace_stub_media(context, inline_message_id, attach)
        _pop_attach(context, result_id, None)
    except Exception:
        if context.job_queue:
            context.job_queue.run_once(
                weather_replace_job,
                when=0.4,
                data={
                    "imid": inline_message_id,
                    "attach": attach,
                    "result_id": result_id,
                    "attempt": 1,
                },
            )


async def weather_chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """После отправки заглушки подменяет её на пасхалку. Нужен /setinlinefeedback."""
    chosen = update.chosen_inline_result
    if not chosen or not chosen.inline_message_id:
        return
    attach = _peek_attach(context, chosen.result_id)
    if not attach:
        return
    await _replace_now_or_retry(
        context, chosen.inline_message_id, attach, chosen.result_id
    )


async def weather_stub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запасной путь, если фидбек не пришёл, а кнопку на заглушке нажали."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("wx"):
        return
    await query.answer()
    if not query.inline_message_id:
        return
    attach = _pop_attach(context, None, query.data[2:])
    if not attach:
        return
    try:
        await _replace_stub_media(context, query.inline_message_id, attach)
    except Exception:
        pass


async def weather_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Инлайн: город пишется в поле ввода после @бота, в чат уходит только выбранный результат."""
    inline_query = update.inline_query
    if not inline_query:
        return

    city = (inline_query.query or "").strip()
    if not city:
        # Пустой запрос: без результатов, иначе «подсказка» уходит в чат как сообщение
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    try:
        fetched = _fetch_weather_html(city)
        text, api_city_name = fetched if fetched else (None, "")
        gif_id = _bonus_gif(city, api_city_name)
        photo_id = None if gif_id else _bonus_photo(city, api_city_name)

        if fetched is None and not gif_id:
            await inline_query.answer(
                [
                    InlineQueryResultArticle(
                        id=_inline_id(),
                        title=f"Город «{city}» не найден",
                        input_message_content=InputTextMessageContent(
                            f"❌ Город '{city}' не найден."
                        ),
                    )
                ],
                cache_time=5,
                is_personal=True,
            )
            return

        display_name = api_city_name or city
        if not text:
            text = f"<b>Погода в {display_name}</b>"

        result_id = _inline_id()
        easter_kind = None
        easter_id = None
        if gif_id:
            easter_kind, easter_id = "gif", gif_id
        elif photo_id:
            easter_kind, easter_id = "photo", photo_id

        if easter_kind and WEATHER_STUB_PHOTO_ID:
            _store_attach(context, result_id, easter_kind, easter_id, text)
            result = InlineQueryResultCachedPhoto(
                id=result_id,
                photo_file_id=WEATHER_STUB_PHOTO_ID,
                title=f"Погода в {display_name}",
                caption=text,
                parse_mode="HTML",
                reply_markup=_stub_keyboard(result_id),
            )
        else:
            result = InlineQueryResultArticle(
                id=result_id,
                title=f"Погода в {display_name}",
                input_message_content=InputTextMessageContent(text, parse_mode="HTML"),
            )
        await inline_query.answer([result], cache_time=1, is_personal=True)

    except Exception:
        await inline_query.answer(
            [
                InlineQueryResultArticle(
                    id=_inline_id(),
                    title="Не удалось получить погоду",
                    input_message_content=InputTextMessageContent(
                        "⚠️ Не удалось получить данные о погоде."
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
