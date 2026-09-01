from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot

from bot.formatters import (
    PARSE_MODE,
    format_class_ended,
    format_day,
    format_live_now,
    format_upcoming,
    format_week,
    minutes_since_midnight,
)
from config import TZ
from storage.db import Storage

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(ZoneInfo(TZ))


async def _send(bot: Bot, chat_id: int, text: str):
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=PARSE_MODE,
        disable_web_page_preview=True,
    )


def _refresh_all_schedules_sync(storage: Storage) -> None:
    """Blocking Moodle crawl — must not run on the asyncio loop."""
    from bot.schedule_ops import refresh_user_schedule

    for user in storage.list_users():
        groups = storage.get_groups(user["telegram_id"])
        if not groups:
            continue
        try:
            password = storage.decrypt_password(user["moodle_password_enc"] or "")
        except Exception:
            password = ""
        try:
            refresh_user_schedule(
                storage,
                user["telegram_id"],
                user["chat_id"],
                user["moodle_username"] or "",
                password,
                user["group_query"] or "",
                groups,
            )
        except Exception:
            log.exception("schedule refresh failed for %s", user["telegram_id"])


async def refresh_all_schedules(storage: Storage) -> None:
    """Re-search Moodle / re-parse PDFs so a new timetable is picked up the same day."""
    await asyncio.to_thread(_refresh_all_schedules_sync, storage)


async def send_today_digest(bot: Bot, storage: Storage, telegram_id: int | None = None) -> None:
    await refresh_all_schedules(storage)
    users = storage.list_users()
    now = _now()
    weekday = now.weekday()  # Mon=0
    for user in users:
        if telegram_id is not None and user["telegram_id"] != telegram_id:
            continue
        slots = [s for s in storage.get_slots(user["telegram_id"]) if s.weekday == weekday]
        if not slots:
            continue
        hw = storage.all_homework(user["telegram_id"])
        text = format_day(slots, hw, weekday=weekday)
        if not text:
            continue
        try:
            await _send(bot, user["chat_id"], text)
        except Exception:
            log.exception("Failed today digest for %s", user["telegram_id"])


async def send_week_digest(bot: Bot, storage: Storage, telegram_id: int | None = None) -> None:
    await refresh_all_schedules(storage)
    users = storage.list_users()
    for user in users:
        if telegram_id is not None and user["telegram_id"] != telegram_id:
            continue
        slots = storage.get_slots(user["telegram_id"])
        hw = storage.all_homework(user["telegram_id"])
        text = format_week(slots, hw)
        try:
            await _send(bot, user["chat_id"], text)
        except Exception:
            log.exception("Failed week digest for %s", user["telegram_id"])


async def live_feed_tick(bot: Bot, storage: Storage) -> None:
    now = _now()
    weekday = now.weekday()
    if weekday > 5:
        return
    now_min = now.hour * 60 + now.minute

    for user in storage.list_users():
        tid = user["telegram_id"]
        chat_id = user["chat_id"]
        day_slots = sorted(
            [s for s in storage.get_slots(tid) if s.weekday == weekday],
            key=lambda s: s.start,
        )
        if not day_slots:
            continue
        hw_map = storage.all_homework(tid)

        for idx, slot in enumerate(day_slots):
            start_m = minutes_since_midnight(slot.start)
            end_m = minutes_since_midnight(slot.end)
            mins_until = start_m - now_min
            fp = f"{weekday}-{start_m}-{slot.course_key}"

            # 30-minute reminder
            if mins_until == 30:
                kind = "m30"
                if not storage.was_reminder_sent(tid, fp, kind):
                    text = format_upcoming(slot, 30, hw_map.get(slot.course_key))
                    try:
                        msg = await _send(bot, chat_id, text)
                        storage.map_live_message(chat_id, msg.message_id, tid, slot.course_key, fp)
                        storage.mark_reminder_sent(tid, fp, kind)
                    except Exception:
                        log.exception("live 30m failed")

            # 5..1 RUSH
            if 1 <= mins_until <= 5:
                kind = f"m{mins_until}"
                if not storage.was_reminder_sent(tid, fp, kind):
                    text = format_upcoming(slot, mins_until, hw_map.get(slot.course_key))
                    try:
                        msg = await _send(bot, chat_id, text)
                        storage.map_live_message(chat_id, msg.message_id, tid, slot.course_key, fp)
                        storage.mark_reminder_sent(tid, fp, kind)
                    except Exception:
                        log.exception("live rush failed")

            # Class ended — fire once when now_min == end_m
            if now_min == end_m:
                kind = "ended"
                if not storage.was_reminder_sent(tid, fp, kind):
                    nxt = day_slots[idx + 1] if idx + 1 < len(day_slots) else None
                    until = (minutes_since_midnight(nxt.start) - now_min) if nxt else None
                    text = format_class_ended(slot, nxt, until)
                    try:
                        msg = await _send(bot, chat_id, text)
                        # Map to the class that ended (homework for that course)
                        storage.map_live_message(chat_id, msg.message_id, tid, slot.course_key, fp)
                        storage.mark_reminder_sent(tid, fp, kind)
                        # Clear homework for this course after the class happened again
                        storage.clear_homework(tid, slot.course_key)
                    except Exception:
                        log.exception("live ended failed")


async def send_live_now(bot: Bot, storage: Storage, telegram_id: int, chat_id: int) -> str:
    """Force a live-feed snapshot for one user (does not consume reminder flags)."""
    now = _now()
    weekday = now.weekday()
    now_min = now.hour * 60 + now.minute
    if weekday > 5:
        text = "📅 No classes today."
        await _send(bot, chat_id, text)
        return text

    day_slots = sorted(
        [s for s in storage.get_slots(telegram_id) if s.weekday == weekday],
        key=lambda s: s.start,
    )
    hw_map = storage.all_homework(telegram_id)
    text, slot = format_live_now(day_slots, now_min, hw_map)
    msg = await _send(bot, chat_id, text)
    if slot is not None:
        start_m = minutes_since_midnight(slot.start)
        fp = f"{weekday}-{start_m}-{slot.course_key}-now"
        storage.map_live_message(chat_id, msg.message_id, telegram_id, slot.course_key, fp)
    return text
