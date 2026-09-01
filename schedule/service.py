from __future__ import annotations

from pathlib import Path
from typing import Optional

from config import CACHE_DIR, FALLBACK_DIR
from schedule.groups import (
    find_fallback_xlsx,
    load_group_index,
    resolve_group,
)
from schedule.models import ClassSlot, ScheduleBundle, StudentGroups
from schedule.pdf_parser import (
    filter_slots_for_student,
    find_fallback_pdf,
    parse_timetable_pdf,
)


def load_fallback_bundle() -> ScheduleBundle:
    pdf = find_fallback_pdf(FALLBACK_DIR)
    if not pdf:
        return ScheduleBundle(error="Couldn't find the schedule")
    return parse_timetable_pdf(pdf)


def load_group_index_from_fallback():
    xlsx = find_fallback_xlsx(FALLBACK_DIR)
    if not xlsx:
        return None
    return load_group_index(xlsx)


def resolve_user_group(query: str) -> Optional[StudentGroups]:
    index = load_group_index_from_fallback()
    if index is None:
        return None
    return resolve_group(index, query)


def build_personal_schedule(
    groups: StudentGroups,
    pdf_path: Optional[Path] = None,
) -> tuple[list[ClassSlot], ScheduleBundle]:
    if pdf_path and pdf_path.exists():
        bundle = parse_timetable_pdf(pdf_path)
    else:
        # Prefer cached moodle pdf if present
        cached = sorted(CACHE_DIR.glob("*.pdf"))
        if cached:
            bundle = parse_timetable_pdf(cached[-1])
            if bundle.error:
                bundle = load_fallback_bundle()
        else:
            bundle = load_fallback_bundle()

    if bundle.error:
        return [], bundle

    slots = filter_slots_for_student(bundle, groups)
    return slots, bundle
