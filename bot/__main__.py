from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.handlers import (
    build_conversation,
    cmd_clearhw,
    cmd_group,
    cmd_homework,
    cmd_logout,
    cmd_now,
    cmd_refresh,
    cmd_today,
    cmd_tomorrow,
    cmd_week,
    on_reply_homework,
)
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from bot.jobs import live_feed_tick, refresh_all_schedules, send_today_digest, send_week_digest
from config import TELEGRAM_BOT_TOKEN, TZ
from storage.db import Storage

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("ufaragent")


async def _job_today(context) -> None:
    await send_today_digest(context.bot, context.application.bot_data["storage"])


async def _job_week(context) -> None:
    await send_week_digest(context.bot, context.application.bot_data["storage"])


async def _job_live(context) -> None:
    await live_feed_tick(context.bot, context.application.bot_data["storage"])


async def _job_refresh(context) -> None:
    await refresh_all_schedules(context.application.bot_data["storage"])


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("today", "Today's classes"),
            BotCommand("tomorrow", "Tomorrow's classes"),
            BotCommand("week", "This week's schedule"),
            BotCommand("now", "What's happening now"),
            BotCommand("homework", "Saved homework"),
            BotCommand("clearhw", "Clear homework"),
            BotCommand("refresh", "Reload timetable"),
            BotCommand("group", "Change group"),
            BotCommand("logout", "Delete your data"),
        ]
    )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in .env")

    storage = Storage()
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    app.bot_data["storage"] = storage

    app.add_handler(build_conversation())
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("now", cmd_now))
    app.add_handler(CommandHandler("homework", cmd_homework))
    app.add_handler(CommandHandler("clearhw", cmd_clearhw))
    app.add_handler(CommandHandler("clearhw", cmd_clearhw), group=-1)
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("group", cmd_group))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(
        MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, on_reply_homework),
        group=-1,
    )

    jq = app.job_queue
    if jq is None:
        raise SystemExit("JobQueue unavailable — install python-telegram-bot[job-queue]")

    tz = ZoneInfo(TZ)
    # Daily 06:00 Asia/Yerevan
    jq.run_daily(
        _job_today,
        time=dt_time(hour=6, minute=0, tzinfo=tz),
        name="daily_today",
    )
    # Friday 23:00
    jq.run_daily(
        _job_week,
        time=dt_time(hour=23, minute=0, tzinfo=tz),
        days=(4,),  # Friday
        name="friday_week",
    )
    # Live feed every minute
    jq.run_repeating(
        _job_live,
        interval=60,
        first=10,
        name="live_feed",
    )
    # Re-download / re-parse timetables (Moodle files change often)
    jq.run_repeating(
        _job_refresh,
        interval=2 * 60 * 60,
        first=15 * 60,
        name="moodle_refresh",
    )

    log.info("UFAR study bot starting (TZ=%s)", TZ)
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
