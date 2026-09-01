from __future__ import annotations

import re
from datetime import time
from pathlib import Path
from typing import Optional

import fitz

from schedule.models import ClassSlot, ScheduleBundle, StudentGroups

TIME_RE = re.compile(r"(\d{1,2})[։:](\d{2})\s*[-–]\s*(\d{1,2})[։:](\d{2})")
ROOM_RE = re.compile(r"(ԻԱՊԻ|IAPI)\s*(\d+)", re.I)
IAPI_TOKEN_RE = re.compile(r"^(ԻԱՊԻ|IAPI)$", re.I)

DAY_MARKERS = [
    (0, ("լունդի", "lundi", "երկուշաբթի", "idnul", "իթբաշւոկրե")),
    (1, ("մարդի", "mardi", "երեքշաբթի", "idram", "իթբաշքերե")),
    (2, ("մերկրեդի", "mercredi", "չորեքշաբթի", "idercrem", "իթբաշեքրոչ")),
    (3, ("ժյոդի", "jeudi", "հինգշաբթի", "iduej", "իթբաշգնիհ")),
    (4, ("վանդրեդի", "vendredi", "ուրբաթ", "iderdnev", "թաբրու")),
    (5, ("սամեդի", "samedi", "շաբաթ", "idemas", "թաբաշ")),
]

COURSE_PREFER = [
    (re.compile(r"Informatique renforc[ée]e\s*[-–]?\s*1", re.I), "Informatique renforcée -1"),
    (re.compile(r"Reinforced CS\s*1", re.I), "Reinforced CS 1"),
    (re.compile(r"Ամրապնդված ինֆորմատիկա", re.I), "Informatique renforcée -1"),
    (re.compile(r"Mathématiques 1|Մաթեմատիկա 1", re.I), "Mathématiques 1"),
    (re.compile(r"Géométrie analytique|Անալիտիկ երկրաչափություն", re.I), "Géométrie analytique et algèbre"),
    (re.compile(r"Français|Ֆրանսերեն", re.I), "Français"),
]

REGULAR_GROUPS = ["CM_A", "TP_1", "TP_2", "TP_3", "TP_4", "TD_1", "TD_2", "TD_3"]
CONTENT_X_MIN = 108.0
TIME_X_MAX = 108.0
HEADER_PAIR_DX = 28.0
CLUSTER_GAP = 38.0
DAY_LOOKBACK = 90.0


def find_fallback_pdf(fallback_dir: Path) -> Optional[Path]:
    files = sorted(fallback_dir.glob("*.pdf"))
    return files[0] if files else None


def _parse_time(label: str) -> Optional[tuple[time, time]]:
    if not label:
        return None
    compact = label.replace(" ", "")
    m = TIME_RE.search(compact)
    if not m:
        return None
    return time(int(m.group(1)), int(m.group(2))), time(int(m.group(3)), int(m.group(4)))


def _detect_day(cell: str) -> Optional[int]:
    if not cell:
        return None
    blob = cell.casefold() + " " + cell[::-1].casefold()
    for weekday, markers in DAY_MARKERS:
        for m in markers:
            if m in blob:
                return weekday
    return None


def _is_break_slot(start: time, end: time) -> bool:
    duration = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return start.hour == 13 and start.minute == 30 and duration <= 40


def _word_text(w) -> str:
    return w[4]


def _join_words(words: list) -> str:
    return " ".join(_word_text(w) for w in sorted(words, key=lambda x: (x[1], x[0])))


def _display_course(text: str) -> tuple[str, str, str, bool]:
    """Return course_name, teacher, room, is_advanced from a block's raw text."""
    is_adv = bool(re.search(r"avancé|advanced", text, re.I))
    room = ""
    rm = ROOM_RE.search(text)
    if rm:
        room = f"ԻԱՊԻ {rm.group(2)}"

    teacher = ""
    tm = re.search(r"\b([A-Z]\.\s*[A-Z][a-zA-Z\-]+)\b", text)
    if tm:
        teacher = tm.group(1)
    else:
        tm = re.search(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b", text)
        if tm and not re.search(
            r"Math|Reinforced|Inform|Initiation|Introduction|Computer",
            tm.group(0),
            re.I,
        ):
            teacher = tm.group(0)

    name = ""
    for pat, label in COURSE_PREFER:
        if pat.search(text):
            name = label
            break
    if not name:
        latin = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'’\-]*(?:\s+[A-Za-zÀ-ÿ0-9'’\-]+){0,6}", text)
        skip = {"Avancé", "Advanced", "IAPI"}
        latin = [p for p in latin if p not in skip and not re.match(r"^[A-Z]\.\s", p)]
        if latin:
            name = max(latin, key=len).strip()
        else:
            name = text.split("\n")[0][:80].strip() or "Class"
    return name, teacher, room, is_adv


def _find_header_anchors(words: list) -> list[tuple[str, float]]:
    """Detect TP/TD/CM column centers from whatever header row this PDF printed."""
    anchors: list[tuple[str, float]] = []
    for w in words:
        tag = _word_text(w).strip().upper()
        if tag not in {"TP", "TD", "CM"}:
            continue
        y, x1 = w[1], w[2]
        neighbors = [
            o
            for o in words
            if abs(o[1] - y) < 5 and o[0] >= x1 - 2 and o[0] <= x1 + HEADER_PAIR_DX
        ]
        neighbors.sort(key=lambda o: o[0])
        if not neighbors:
            continue
        nxt = _word_text(neighbors[0]).strip()
        cx = (w[0] + neighbors[0][2]) / 2
        if tag == "CM" and nxt.upper() == "A":
            anchors.append(("CM_A", cx))
        elif nxt.isdigit():
            anchors.append((f"{tag}_{int(nxt)}", cx))

    # Keep the first (topmost) occurrence of each key — that's the real header.
    by_key: dict[str, tuple[str, float]] = {}
    for key, cx in sorted(anchors, key=lambda a: a[1]):
        if key not in by_key:
            by_key[key] = (key, cx)
    return list(by_key.values())


def _cluster_by_x(words: list) -> list[list]:
    """Split merged visual blocks. Use midpoints so a long word cannot bridge columns."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (w[0] + w[2]) / 2)
    clusters = [[ordered[0]]]
    for w in ordered[1:]:
        prev_mid = max((c[0] + c[2]) / 2 for c in clusters[-1])
        mid = (w[0] + w[2]) / 2
        if mid - prev_mid > CLUSTER_GAP:
            clusters.append([w])
        else:
            clusters[-1].append(w)
    return clusters


def _overlapping_keys(x0: float, x1: float, anchors: list[tuple[str, float]]) -> list[str]:
    pad = 18.0
    hits = [key for key, cx in anchors if (x0 - pad) <= cx <= (x1 + pad)]
    return hits


def _assign_keys_for_slot(
    clusters: list[dict],
    anchors: list[tuple[str, float]],
) -> None:
    """Mutates cluster['keys'] using layout + Avancé-as-stream rules."""
    french = [c for c in clusters if c["is_french"]]
    academic = [c for c in clusters if not c["is_french"]]
    advs = [c for c in academic if c["is_adv"]]
    regs = [c for c in academic if not c["is_adv"]]

    for c in french:
        rooms = c.get("rooms") or []
        c["keys"] = [f"FR_{n}" for n in rooms] or ["FR_101", "FR_102", "FR_103", "FR_104"]

    # Whole-row lecture (geometry / CM): one wide block
    content_left = min((a[1] for a in anchors), default=130)
    content_right = max((a[1] for a in anchors), default=360)
    span = content_right - content_left

    for c in academic:
        geo = bool(re.search(r"Géométrie|Անալիտիկ երկրաչափություն", c["text"], re.I))
        wide = (c["x1"] - c["x0"]) >= 0.62 * span if span else False
        # "Avancé" is a label in the cell, not a grid column — only the
        # Advanced roster (Excel) attends those blocks.
        if c["is_adv"]:
            c["keys"] = ["TD_ADV"]
            continue
        if geo or (wide and len(academic) == 1):
            c["keys"] = ["CM_A"]
            continue
        if advs and regs:
            c["keys"] = list(REGULAR_GROUPS)
            continue
        keys = _overlapping_keys(c["x0"], c["x1"], anchors)
        keys = [k for k in keys if not k.startswith("FR_")]
        c["keys"] = keys or list(REGULAR_GROUPS)


def parse_timetable_pdf(pdf_path: Path) -> ScheduleBundle:
    slots: list[ClassSlot] = []
    week_label = ""
    global_anchors: list[tuple[str, float]] = []
    last_weekday: Optional[int] = None

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return ScheduleBundle(error="Couldn't find the schedule", source=str(pdf_path))

    with doc:
        for page in doc:
            words = list(page.get_text("words"))
            text = page.get_text() or ""
            m = re.search(r"(\d{2}\.\d{2}\.\d{2})\s*[-–]\s*(\d{2}\.\d{2}\.\d{2})", text)
            if m and not week_label:
                week_label = f"{m.group(1)} - {m.group(2)}"

            page_anchors = _find_header_anchors(words)
            if page_anchors:
                global_anchors = page_anchors
            anchors = global_anchors or page_anchors

            days: list[tuple[float, int]] = []
            times: list[tuple[float, time, time]] = []
            for w in words:
                raw = _word_text(w)
                d = _detect_day(raw)
                if d is not None and w[0] < TIME_X_MAX:
                    days.append((w[1], d))
                    continue
                if w[0] < TIME_X_MAX:
                    tpair = _parse_time(raw)
                    if tpair:
                        times.append((w[1], tpair[0], tpair[1]))

            days.sort()
            times.sort()

            # Day names often sit beside a later row; inherit from earlier on this/previous page.
            def weekday_for(y: float) -> Optional[int]:
                chosen = last_weekday
                for dy, d in days:
                    if y >= dy - DAY_LOOKBACK:
                        chosen = d
                return chosen

            class_times = [t for t in times if not _is_break_slot(t[1], t[2])]
            first_time_y = class_times[0][0] if class_times else 0.0

            for ty, start, end in class_times:
                wd = weekday_for(ty)
                if wd is None:
                    continue
                last_weekday = wd
                band_words = []
                for w in words:
                    if w[0] < CONTENT_X_MIN:
                        continue
                    if w[1] < first_time_y - 18:
                        continue
                    tok = _word_text(w)
                    if tok.upper() in {"TP", "TD", "CM"}:
                        continue
                    wy = (w[1] + w[3]) / 2
                    nearest = min(class_times, key=lambda t: abs(t[0] - wy))
                    if nearest[0] != ty:
                        continue
                    if abs(wy - ty) > 38:
                        continue
                    if _detect_day(tok) is not None:
                        continue
                    band_words.append(w)

                if not band_words:
                    continue

                joined_all = _join_words(band_words)
                is_french_row = bool(re.search(r"Français|Ֆրանսերեն", joined_all, re.I))
                rooms_in_row = [int(n) for n in re.findall(r"\b(10[1-4]|200)\b", joined_all)]

                if is_french_row and not re.search(
                    r"Math|Reinforced|Inform|Géom|Ամրապ|Մաթեմ|Անալիտ", joined_all, re.I
                ):
                    for n in rooms_in_row or [101, 102, 103, 104]:
                        slots.append(
                            ClassSlot(
                                weekday=wd,
                                start=start,
                                end=end,
                                course_name="Français",
                                room=f"ԻԱՊԻ {n}",
                                column_key=f"FR_{n}",
                            )
                        )
                    continue

                clusters_words = _cluster_by_x(band_words)
                parsed_clusters: list[dict] = []
                for cw in clusters_words:
                    text_c = _join_words(cw)
                    if not text_c.strip():
                        continue
                    if IAPI_TOKEN_RE.fullmatch(text_c.strip()) or re.fullmatch(r"\d{2,4}", text_c.strip()):
                        continue
                    name, teacher, room, is_adv = _display_course(text_c)
                    if name in {"Class", ""} and not is_adv:
                        continue
                    xs = [w[0] for w in cw]
                    xe = [w[2] for w in cw]
                    parsed_clusters.append(
                        {
                            "text": text_c,
                            "name": name,
                            "teacher": teacher,
                            "room": room,
                            "is_adv": is_adv,
                            "is_french": bool(re.search(r"Français|Ֆրանսերեն", text_c, re.I)),
                            "x0": min(xs),
                            "x1": max(xe),
                            "rooms": [int(n) for n in re.findall(r"\b(10[1-4]|200)\b", text_c)],
                            "keys": [],
                        }
                    )

                # Drop tiny fragments (lone room leftovers)
                parsed_clusters = [c for c in parsed_clusters if len(c["text"]) > 8 or c["is_adv"]]
                if not parsed_clusters:
                    continue

                _assign_keys_for_slot(parsed_clusters, anchors)
                for c in parsed_clusters:
                    for key in c["keys"] or ["CM_A"]:
                        slots.append(
                            ClassSlot(
                                weekday=wd,
                                start=start,
                                end=end,
                                course_name=c["name"],
                                teacher=c["teacher"],
                                room=c["room"],
                                column_key=key,
                            )
                        )

    uniq: dict[tuple, ClassSlot] = {}
    for s in slots:
        if _is_break_slot(s.start, s.end):
            continue
        k = (s.weekday, s.start, s.end, s.course_name, s.column_key)
        uniq[k] = s

    result = sorted(uniq.values(), key=lambda s: (s.weekday, s.start, s.column_key))
    if not result:
        return ScheduleBundle(
            slots=[],
            source=str(pdf_path),
            week_label=week_label,
            error="Couldn't find the schedule",
        )
    return ScheduleBundle(slots=result, source=str(pdf_path), week_label=week_label)


def _pick_slot_for_student(cands: list[ClassSlot], groups: StudentGroups) -> Optional[ClassSlot]:
    """One class per time. Avancé students take the Avancé cell when it exists."""
    french = [c for c in cands if c.column_key.startswith("FR_")]
    if groups.french_room:
        french = [c for c in french if c.column_key == groups.french_room]
    academic = [c for c in cands if not c.column_key.startswith("FR_")]
    if groups.td != "TD_ADV":
        academic = [c for c in academic if c.column_key != "TD_ADV"]
    if not academic:
        return french[0] if french else None

    if groups.td == "TD_ADV":
        adv = [c for c in academic if c.column_key == "TD_ADV"]
        if adv:
            return adv[0]
    if groups.tp:
        tp = [c for c in academic if c.column_key == groups.tp]
        if tp:
            return tp[0]
    if groups.td:
        td = [c for c in academic if c.column_key == groups.td]
        if td:
            return td[0]
    cm = [c for c in academic if c.column_key == "CM_A"]
    if cm:
        return cm[0]
    return academic[0]


def filter_slots_for_student(bundle: ScheduleBundle, groups: StudentGroups) -> list[ClassSlot]:
    keys = groups.column_keys() | {"CM_A"}
    by_time: dict[tuple, list[ClassSlot]] = {}
    for slot in bundle.slots:
        col = slot.column_key
        if col not in keys:
            continue
        if col.startswith("FR_") and groups.french_room and col != groups.french_room:
            continue
        by_time.setdefault((slot.weekday, slot.start), []).append(slot)

    result: list[ClassSlot] = []
    for _, cands in sorted(by_time.items()):
        picked = _pick_slot_for_student(cands, groups)
        if picked:
            result.append(picked)
    return result
