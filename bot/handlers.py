from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from html import escape as h

from bot.formatters import (
    PARSE_MODE,
    format_day,
    format_homework_list,
    format_hw_cleared,
    format_hw_saved,
    format_week,
)
from bot.jobs import send_live_now
from bot.schedule_ops import refresh_user_schedule
from config import TZ
from schedule.models import normalize_course_key
from schedule.service import resolve_user_group
from storage.db import Storage

log = logging.getLogger(__name__)

ASK_USER, ASK_PASS, ASK_GROUP = range(3)


def _storage(context: ContextTypes.DEFAULT_TYPE) -> Storage:
    return context.application.bot_data["storage"]


async def _reply(update: Update, text: str):
    return await update.message.reply_text(
        text, parse_mode=PARSE_MODE, disable_web_page_preview=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await _reply(
        update,
        "👋 <b>UFAR study bot</b>\n\n"
        "Send your Moodle username (<code>moodle.ufar.am</code>):",
    )
    return ASK_USER


async def ask_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["moodle_username"] = (update.message.text or "").strip()
    await _reply(update, "Send your Moodle password:")
    return ASK_PASS


async def ask_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text or ""
    context.user_data["moodle_password"] = password
    # Delete password message for privacy
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.message.chat.send_message(
        "✅ Password received (message deleted if possible).\n\n"
        "Send your group — full name as in the roster (e.g. <i>Adamyan Aren</i>), "
        "or a code like <code>TP 1</code> / <code>TD 2</code> / <code>TD Advanced</code>:",
        parse_mode=PARSE_MODE,
        disable_web_page_preview=True,
    )
    return ASK_GROUP


async def ask_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = (update.message.text or "").strip()
    groups = resolve_user_group(query)
    if groups is None:
        await _reply(
            update,
            "Couldn't find your group. Try again (full name or TP/TD code):",
        )
        return ASK_GROUP

    storage = _storage(context)
    username = context.user_data.get("moodle_username", "")
    password = context.user_data.get("moodle_password", "")
    tid = update.effective_user.id
    chat_id = update.effective_chat.id

    await _reply(update, "⏳ Saving and loading your schedule…")
    try:
        slots, note = refresh_user_schedule(
            storage, tid, chat_id, username, password, query, groups
        )
    except Exception:
        log.exception("refresh failed")
        await _reply(update, "Something went wrong while loading the schedule. Try /start again.")
        return ConversationHandler.END

    context.user_data.clear()
    summary = (
        f"✅ You're set as <b>{h(groups.display_name)}</b>\n"
        f"TP <code>{h(groups.tp or '—')}</code> · "
        f"TD <code>{h(groups.td or '—')}</code> · "
        f"French <code>{h(groups.french_room or '—')}</code>\n\n"
        f"{h(note)}\n"
        f"Loaded <b>{len(slots)}</b> class slot(s).\n\n"
        "<b>Commands</b>\n"
        "/today  /tomorrow  /week  /now\n"
        "/homework  /clearhw\n"
        "/refresh  /group  /logout"
    )
    await _reply(update, summary)
    if slots:
        hw = storage.all_homework(tid)
        await _reply(update, format_week(slots, hw))
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await _reply(update, "Cancelled.")
    return ConversationHandler.END


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    user = storage.get_user(tid)
    if not user:
        await _reply(update, "Please /start first.")
        return
    weekday = datetime.now(ZoneInfo(TZ)).weekday()
    slots = storage.get_slots(tid)
    await _reply(
        update,
        format_day(slots, storage.all_homework(tid), weekday=weekday),
    )


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    if not storage.get_user(tid):
        await _reply(update, "Please /start first.")
        return
    weekday = (datetime.now(ZoneInfo(TZ)).weekday() + 1) % 7
    slots = storage.get_slots(tid)
    await _reply(
        update,
        format_day(
            slots,
            storage.all_homework(tid),
            weekday=weekday,
            title="Tomorrow",
            icon="🌙",
        ),
    )


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    if not storage.get_user(tid):
        await _reply(update, "Please /start first.")
        return
    slots = storage.get_slots(tid)
    await _reply(update, format_week(slots, storage.all_homework(tid)))


async def cmd_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    user = storage.get_user(tid)
    if not user:
        await _reply(update, "Please /start first.")
        return
    try:
        await send_live_now(context.bot, storage, tid, update.effective_chat.id)
    except Exception:
        log.exception(" /now failed")
        await _reply(update, "Could not build the live feed. Try /today.")


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    user = storage.get_user(tid)
    if not user:
        await _reply(update, "Please /start first.")
        return
    groups = storage.get_groups(tid)
    if not groups:
        await _reply(update, "No group saved. Use /group or /start.")
        return
    try:
        password = storage.decrypt_password(user["moodle_password_enc"])
    except Exception:
        await _reply(update, "Could not decrypt stored password. Please /start again.")
        return
    await _reply(update, "⏳ Refreshing from Moodle…")
    slots, note = refresh_user_schedule(
        storage,
        tid,
        user["chat_id"],
        user["moodle_username"],
        password,
        user["group_query"],
        groups,
    )
    await _reply(update, f"{h(note)}\nLoaded <b>{len(slots)}</b> slot(s).")


async def cmd_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    user = storage.get_user(tid)
    if not user:
        await _reply(update, "Please /start first.")
        return
    if not context.args:
        await _reply(update, "Usage: <code>/group &lt;name or TP/TD code&gt;</code>")
        return
    query = " ".join(context.args).strip()
    groups = resolve_user_group(query)
    if groups is None:
        await _reply(update, "Couldn't find your group.")
        return
    try:
        password = storage.decrypt_password(user["moodle_password_enc"])
    except Exception:
        password = ""
    slots, note = refresh_user_schedule(
        storage,
        tid,
        user["chat_id"],
        user["moodle_username"] or "",
        password,
        query,
        groups,
    )
    await _reply(
        update,
        f"✅ Updated to <b>{h(groups.display_name)}</b>\n"
        f"{h(note)}\nLoaded <b>{len(slots)}</b> slot(s).",
    )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    storage.delete_user(update.effective_user.id)
    await _reply(update, "Logged out. Your data was deleted. Use /start to begin again.")


_LIVE_COURSE_PATTERNS = [
    re.compile(r"You have (.+?) now\b"),
    re.compile(r"You have (.+?) in \d+"),
    re.compile(r"(?:✅\s*)?(.+?) has ended", re.M),
    re.compile(r"^(.+?) ends soon", re.M),
    re.compile(r"Homework saved for (.+?):"),
    re.compile(r"Homework removed for (.+)\."),
]


def _course_key_from_live_text(text: str, telegram_id: int, storage: Storage) -> str | None:
    blob = text or ""
    if re.search(r"Homework saved for that class", blob, re.I):
        hw = storage.all_homework(telegram_id)
        if len(hw) == 1:
            return next(iter(hw))
    name = None
    for pat in _LIVE_COURSE_PATTERNS:
        m = pat.search(blob)
        if m:
            name = m.group(1).strip()
            break
    if not name:
        return None
    key = normalize_course_key(name)
    slots = storage.get_slots(telegram_id)
    known = {s.course_key for s in slots}
    if key in known or not slots:
        return key
    for s in slots:
        if s.course_name.casefold() in name.casefold() or name.casefold() in s.course_name.casefold():
            return s.course_key
    return key


_CLEAR_WORDS = {"clear", "remove", "delete", "none", "cancel", "clearhw"}


def _course_label(storage: Storage, telegram_id: int, course_key: str) -> str:
    return next(
        (s.course_name for s in storage.get_slots(telegram_id) if s.course_key == course_key),
        course_key,
    )


def _resolve_reply_course(update, storage: Storage, tid: int) -> str | None:
    reply = update.message.reply_to_message if update.message else None
    if not reply:
        return None
    mapped = storage.get_live_message(update.effective_chat.id, reply.message_id)
    if mapped:
        return mapped["course_key"]
    if reply.from_user and reply.from_user.is_bot:
        return _course_key_from_live_text(reply.text or "", tid, storage)
    return None


async def _confirm_cleared(update, storage: Storage, tid: int, course_key: str) -> None:
    label = _course_label(storage, tid, course_key)
    msg = await _reply(update, format_hw_cleared(label))
    storage.map_live_message(
        update.effective_chat.id, msg.message_id, tid, course_key, "hw-cleared"
    )


async def cmd_homework(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    if not storage.get_user(tid):
        await _reply(update, "Please /start first.")
        return
    hw = storage.all_homework(tid)
    items = [(_course_label(storage, tid, key), text) for key, text in hw.items()]
    await _reply(update, format_homework_list(items))


async def cmd_clearhw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = _storage(context)
    tid = update.effective_user.id
    if not storage.get_user(tid):
        await _reply(update, "Please /start first.")
        return
    args = " ".join(context.args or []).strip()
    if args.casefold() in {"all", "*"}:
        n = storage.clear_all_homework(tid)
        await _reply(update, f"🗑 Removed <b>{n}</b> homework item(s).")
        return

    course_key = _resolve_reply_course(update, storage, tid)
    if not course_key and args:
        course_key = normalize_course_key(args)
        slots = storage.get_slots(tid)
        match = next(
            (
                s.course_key
                for s in slots
                if args.casefold() in s.course_name.casefold() or s.course_key == course_key
            ),
            course_key,
        )
        course_key = match

    if not course_key:
        await _reply(
            update,
            "Reply to a live-feed or /now message with /clearhw,\n"
            "or <code>/clearhw all</code>, or <code>/clearhw &lt;class name&gt;</code>.",
        )
        return
    if not storage.get_homework(tid, course_key):
        await _reply(
            update,
            f"No homework saved for <b>{h(_course_label(storage, tid, course_key))}</b>.",
        )
        return
    storage.clear_homework(tid, course_key)
    await _confirm_cleared(update, storage, tid, course_key)


async def on_reply_homework(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.reply_to_message:
        return
    storage = _storage(context)
    chat_id = update.effective_chat.id
    tid = update.effective_user.id
    reply = update.message.reply_to_message
    text = (update.message.text or "").strip()
    if not text:
        return

    mapped = storage.get_live_message(chat_id, reply.message_id)
    course_key = mapped["course_key"] if mapped else None
    if not course_key and reply.from_user and reply.from_user.is_bot:
        course_key = _course_key_from_live_text(reply.text or "", tid, storage)
    if not course_key and text.casefold().strip().lstrip("/") in _CLEAR_WORDS:
        hw = storage.all_homework(tid)
        if len(hw) == 1:
            course_key = next(iter(hw))
    if not course_key:
        return

    if text.casefold().strip().lstrip("/") in _CLEAR_WORDS:
        if not storage.get_homework(tid, course_key):
            await _reply(
                update,
                f"No homework saved for <b>{h(_course_label(storage, tid, course_key))}</b>.",
            )
            return
        storage.clear_homework(tid, course_key)
        await _confirm_cleared(update, storage, tid, course_key)
        return

    storage.set_homework(tid, course_key, text)
    label = _course_label(storage, tid, course_key)
    msg = await _reply(update, format_hw_saved(label, text))
    storage.map_live_message(
        chat_id, msg.message_id, tid, course_key, "hw-saved"
    )


def build_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_user)],
            ASK_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_pass)],
            ASK_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_group)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
