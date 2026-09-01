from __future__ import annotations

from pathlib import Path
from typing import Optional

from moodle.client import try_fetch_moodle_schedules
from schedule.groups import load_group_index, resolve_group, find_fallback_xlsx
from schedule.models import ClassSlot, StudentGroups
from schedule.pdf_parser import parse_timetable_pdf, filter_slots_for_student, find_fallback_pdf
from schedule.service import build_personal_schedule
from config import FALLBACK_DIR, CACHE_DIR
from storage.db import Storage


def refresh_user_schedule(
    storage: Storage,
    telegram_id: int,
    chat_id: int,
    username: str,
    password: str,
    group_query: str,
    groups: StudentGroups,
) -> tuple[list[ClassSlot], str]:
    """Fetch Moodle files if possible, else fallback; persist slots."""
    login_ok, moodle_msg, files = try_fetch_moodle_schedules(username, password)

    pdf_path: Optional[Path] = None
    xlsx_path: Optional[Path] = None
    for f in files:
        low = f.name.casefold()
        if low.endswith(".pdf") and pdf_path is None:
            pdf_path = f
        if low.endswith(".xlsx") and xlsx_path is None:
            xlsx_path = f

    # Re-resolve group against best xlsx (moodle or fallback)
    index_path = xlsx_path or find_fallback_xlsx(FALLBACK_DIR)
    if index_path:
        index = load_group_index(index_path)
        resolved = resolve_group(index, group_query)
        if resolved:
            groups = resolved

    if pdf_path:
        bundle = parse_timetable_pdf(pdf_path)
        if bundle.error:
            slots, bundle = build_personal_schedule(groups, pdf_path=None)
            note = f"{moodle_msg} Couldn't parse Moodle PDF; using local fallback."
        else:
            slots = filter_slots_for_student(bundle, groups)
            note = moodle_msg
    else:
        slots, bundle = build_personal_schedule(groups, pdf_path=None)
        note = moodle_msg if login_ok else f"{moodle_msg} Using local fallback files."

    if not slots and not bundle.error:
        # Group mismatch
        note = "Couldn't find your group." if not groups.tp and not groups.td else note

    storage.upsert_user(
        telegram_id=telegram_id,
        chat_id=chat_id,
        moodle_username=username,
        moodle_password=password,
        group_query=group_query,
        groups=groups,
        schedule_source=bundle.source,
        week_label=bundle.week_label,
    )
    storage.replace_slots(telegram_id, slots)
    return slots, note
