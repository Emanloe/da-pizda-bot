"""A single response for all inline choices offered by the bot."""

from telegram import Update
from telegram.ext import ContextTypes

from handlers.elite_ball import build_elite_ball_inline_result
from handlers.weather import build_weather_inline_result
from database import is_deleted_user


async def inline_query_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    if inline_query is None:
        return

    query = (inline_query.query or "").strip()
    if not query:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    weather_result, cache_time = build_weather_inline_result(query, context)
    results = [weather_result]
    user = inline_query.from_user
    if (user is not None and not getattr(user, "is_bot", False)
            and not is_deleted_user(user.id)):
        results.append(build_elite_ball_inline_result(user.id, query))
    await inline_query.answer(
        results,
        cache_time=cache_time,
        is_personal=True,
    )
