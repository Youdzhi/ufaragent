from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Optional


WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


@dataclass
class ClassSlot:
    weekday: int  # 0=Monday .. 5=Saturday
    start: time
    end: time
    course_name: str
    teacher: str = ""
    room: str = ""
    column_key: str = ""  # e.g. CM_A, TP_1, TD_2, FR_101

    @property
    def course_key(self) -> str:
        return normalize_course_key(self.course_name)

    @property
    def start_label(self) -> str:
        return f"{self.start.hour}:{self.start.minute:02d}"

    @property
    def time_label(self) -> str:
        return (
            f"{self.start.hour}:{self.start.minute:02d}-"
            f"{self.end.hour}:{self.end.minute:02d}"
        )


@dataclass
class StudentGroups:
    """Resolved memberships for one student from the Excel roster."""

    display_name: str
    cm: str = "CM_A"
    tp: Optional[str] = None  # TP_1 .. TP_4
    td: Optional[str] = None  # TD_1 .. TD_3 or TD_ADV
    french_room: Optional[str] = None  # FR_101 .. FR_104 / FR_200
    french_group: Optional[str] = None
    raw_query: str = ""

    def column_keys(self) -> set[str]:
        keys = {self.cm or "CM_A"}
        if self.tp:
            keys.add(self.tp)
        if self.td:
            keys.add(self.td)
        if self.french_room:
            keys.add(self.french_room)
        return keys


@dataclass
class ScheduleBundle:
    slots: list[ClassSlot] = field(default_factory=list)
    source: str = "fallback"
    week_label: str = ""
    error: Optional[str] = None


def normalize_course_key(name: str) -> str:
    text = (name or "").casefold().strip()
    replacements = {
        "mathématiques 1": "math1",
        "mathematiques 1": "math1",
        "մաթեմատիկա 1": "math1",
        "reinforced cs 1": "cs1",
        "informatique renforcée": "cs1",
        "ամրապնդված ինֆորմատիկա": "cs1",
        "français": "french",
        "ֆրանսերեն": "french",
        "géométrie analytique": "geo_alg",
        "անալիտիկ երկրաչափություն": "geo_alg",
        "anglais": "english",
        "անգլերեն": "english",
    }
    for needle, key in replacements.items():
        if needle in text:
            return key
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in text)
    return cleaned[:64] or "unknown"
