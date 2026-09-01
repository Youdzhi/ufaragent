from __future__ import annotations

from collections import defaultdict
from datetime import time
from html import escape as esc
from typing import Optional

from schedule.models import WEEKDAY_NAMES, ClassSlot

PARSE_MODE = "HTML"


def _time_range(slot: ClassSlot) -> str:
    return (
        f"{slot.start.hour}:{slot.start.minute:02d}–"
        f"{slot.end.hour}:{slot.end.minute:02d}"
    )


def _slot_card(slot: ClassSlot, homework: Optional[str]) -> str:
    lines = [
        f"<code>{esc(_time_range(slot))}</code>  <b>{esc(slot.course_name)}</b>"
    ]
    meta: list[str] = []
    if slot.teacher:
        meta.append(esc(slot.teacher))
    if slot.room:
        meta.append(esc(slot.room))
    if meta:
        lines.append(f"   <i>{' · '.join(meta)}</i>")
    if homework:
        lines.append(f"   📝 {esc(homework)}")
    return "\n".join(lines)


def format_day(
    slots: list[ClassSlot],
    homework_map: dict[str, str],
    weekday: Optional[int] = None,
    *,
    title: str = "Today",
    icon: str = "📅",
) -> str:
    if weekday is not None:
        slots = [s for s in slots if s.weekday == weekday]
    slots = sorted(slots, key=lambda s: s.start)
    day_name = WEEKDAY_NAMES[weekday] if weekday is not None else ""
    header = f"{icon} <b>{esc(title)}</b>"
    if day_name:
        header += f" · {esc(day_name)}"
    if not slots:
        return f"{header}\n\nNo classes."
    cards = [_slot_card(s, homework_map.get(s.course_key)) for s in slots]
    return header + "\n\n" + "\n\n".join(cards)


def format_week(slots: list[ClassSlot], homework_map: dict[str, str]) -> str:
    if not slots:
        return "🗓 <b>This week</b>\n\nCouldn't find the schedule."
    by_day: dict[int, list[ClassSlot]] = defaultdict(list)
    for s in slots:
        by_day[s.weekday].append(s)

    blocks = ["🗓 <b>This week</b>"]
    for day in range(0, 6):
        day_slots = sorted(by_day.get(day, []), key=lambda s: s.start)
        name = WEEKDAY_NAMES[day]
        if not day_slots:
            blocks.append(f"<b>{esc(name)}</b>  —  off")
            continue
        lines = [f"<b>{esc(name)}</b>"]
        for s in day_slots:
            lines.append(_slot_card(s, homework_map.get(s.course_key)))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_upcoming(slot: ClassSlot, minutes: int, homework: Optional[str]) -> str:
    course = esc(slot.course_name)
    if 1 <= minutes <= 5:
        return f"⚡ You have <b>{course}</b> in {minutes} minutes, RUSH!"
    lines = [f"🔔 You have <b>{course}</b> in {minutes} minutes"]
    lines.append(f"<code>{esc(_time_range(slot))}</code>")
    if slot.room:
        lines.append(f"<i>{esc(slot.room)}</i>")
    if homework:
        lines.append(f"📝 last homework: {esc(homework)}")
    return "\n".join(lines)


def format_class_ended(
    ended: ClassSlot,
    nxt: Optional[ClassSlot],
    minutes_until_next: Optional[int],
) -> str:
    course = esc(ended.course_name)
    if nxt is None or minutes_until_next is None:
        return f"✅ {course} has ended\nNo more classes today."
    return (
        f"✅ {course} has ended\n"
        f"⏭ next class is <b>{esc(nxt.course_name)}</b> in {minutes_until_next} minutes."
    )


def format_live_now(
    day_slots: list[ClassSlot],
    now_min: int,
    homework_map: dict[str, str],
) -> tuple[str, Optional[ClassSlot]]:
    """Current live-feed snapshot. Returns (text, slot to attach homework to)."""
    slots = sorted(day_slots, key=lambda s: s.start)
    if not slots:
        return "📅 No classes today.", None

    current = None
    upcoming = None
    ended: Optional[ClassSlot] = None
    for s in slots:
        start_m = minutes_since_midnight(s.start)
        end_m = minutes_since_midnight(s.end)
        if start_m <= now_min < end_m:
            current = s
        elif start_m > now_min and upcoming is None:
            upcoming = s
        elif end_m <= now_min:
            ended = s

    if current is not None:
        left = minutes_since_midnight(current.end) - now_min
        hw = homework_map.get(current.course_key)
        lines = [
            f"▶️ You have <b>{esc(current.course_name)}</b> now",
            f"⏱ {left} minutes left · <code>{esc(_time_range(current))}</code>",
        ]
        if current.room:
            lines.append(f"<i>{esc(current.room)}</i>")
        if hw:
            lines.append(f"📝 last homework: {esc(hw)}")
        if upcoming is not None:
            until = minutes_since_midnight(upcoming.start) - now_min
            lines.append("")
            lines.append(
                f"{esc(current.course_name)} ends soon, next class is "
                f"<b>{esc(upcoming.course_name)}</b> in {until} minutes."
            )
        return "\n".join(lines), current

    if upcoming is not None:
        mins = minutes_since_midnight(upcoming.start) - now_min
        hw = homework_map.get(upcoming.course_key)
        return format_upcoming(upcoming, mins, hw), upcoming

    if ended is not None:
        return format_class_ended(ended, None, None), ended

    return "📅 No more classes today.", None


def format_homework_list(items: list[tuple[str, str]]) -> str:
    if not items:
        return "📚 <b>Homework</b>\n\nNothing saved."
    lines = ["📚 <b>Saved homework</b>", ""]
    for label, text in items:
        lines.append(f"<b>{esc(label)}</b>")
        lines.append(esc(text))
        lines.append("")
    lines.append("Reply <b>clear</b> to a class message, or use /clearhw.")
    return "\n".join(lines).rstrip()


def format_hw_saved(label: str, text: str) -> str:
    return (
        f"✅ Homework saved for {esc(label)}:\n"
        f"<i>{esc(text)}</i>\n\n"
        "Reply <b>clear</b> to remove it."
    )


def format_hw_cleared(label: str) -> str:
    return f"🗑 Homework removed for {esc(label)}."


def minutes_since_midnight(t: time) -> int:
    return t.hour * 60 + t.minute
