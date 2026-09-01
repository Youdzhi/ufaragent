from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, time
from pathlib import Path
from typing import Iterator, Optional

from cryptography.fernet import Fernet

from config import DB_PATH, FERNET_KEY
from schedule.models import ClassSlot, StudentGroups


def _fernet() -> Fernet:
    if not FERNET_KEY:
        raise RuntimeError("FERNET_KEY is not set in .env")
    return Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)


class Storage:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    moodle_username TEXT,
                    moodle_password_enc TEXT,
                    group_query TEXT,
                    group_json TEXT,
                    schedule_source TEXT,
                    week_label TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    weekday INTEGER NOT NULL,
                    start_min INTEGER NOT NULL,
                    end_min INTEGER NOT NULL,
                    course_name TEXT NOT NULL,
                    course_key TEXT NOT NULL,
                    teacher TEXT,
                    room TEXT,
                    column_key TEXT,
                    FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS homework (
                    telegram_id INTEGER NOT NULL,
                    course_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY(telegram_id, course_key)
                );

                CREATE TABLE IF NOT EXISTS live_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    course_key TEXT NOT NULL,
                    slot_fingerprint TEXT,
                    created_at TEXT,
                    PRIMARY KEY(chat_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS reminder_log (
                    telegram_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sent_at TEXT,
                    PRIMARY KEY(telegram_id, fingerprint, kind)
                );
                """
            )

    def encrypt_password(self, password: str) -> str:
        return _fernet().encrypt(password.encode("utf-8")).decode("utf-8")

    def decrypt_password(self, token: str) -> str:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")

    def upsert_user(
        self,
        telegram_id: int,
        chat_id: int,
        moodle_username: str,
        moodle_password: str,
        group_query: str,
        groups: StudentGroups,
        schedule_source: str = "",
        week_label: str = "",
    ) -> None:
        now = datetime.utcnow().isoformat()
        enc = self.encrypt_password(moodle_password)
        group_json = json.dumps(asdict(groups), ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users(
                    telegram_id, chat_id, moodle_username, moodle_password_enc,
                    group_query, group_json, schedule_source, week_label, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    moodle_username=excluded.moodle_username,
                    moodle_password_enc=excluded.moodle_password_enc,
                    group_query=excluded.group_query,
                    group_json=excluded.group_json,
                    schedule_source=excluded.schedule_source,
                    week_label=excluded.week_label,
                    updated_at=excluded.updated_at
                """,
                (
                    telegram_id,
                    chat_id,
                    moodle_username,
                    enc,
                    group_query,
                    group_json,
                    schedule_source,
                    week_label,
                    now,
                    now,
                ),
            )

    def update_group(self, telegram_id: int, group_query: str, groups: StudentGroups) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE users SET group_query=?, group_json=?, updated_at=?
                WHERE telegram_id=?
                """,
                (group_query, json.dumps(asdict(groups), ensure_ascii=False), datetime.utcnow().isoformat(), telegram_id),
            )

    def get_user(self, telegram_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM users"))

    def delete_user(self, telegram_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM slots WHERE telegram_id=?", (telegram_id,))
            conn.execute("DELETE FROM homework WHERE telegram_id=?", (telegram_id,))
            conn.execute("DELETE FROM live_messages WHERE telegram_id=?", (telegram_id,))
            conn.execute("DELETE FROM reminder_log WHERE telegram_id=?", (telegram_id,))
            conn.execute("DELETE FROM users WHERE telegram_id=?", (telegram_id,))

    def replace_slots(self, telegram_id: int, slots: list[ClassSlot]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM slots WHERE telegram_id=?", (telegram_id,))
            for s in slots:
                conn.execute(
                    """
                    INSERT INTO slots(
                        telegram_id, weekday, start_min, end_min, course_name,
                        course_key, teacher, room, column_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telegram_id,
                        s.weekday,
                        s.start.hour * 60 + s.start.minute,
                        s.end.hour * 60 + s.end.minute,
                        s.course_name,
                        s.course_key,
                        s.teacher,
                        s.room,
                        s.column_key,
                    ),
                )

    def get_slots(self, telegram_id: int) -> list[ClassSlot]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM slots WHERE telegram_id=? ORDER BY weekday, start_min",
                (telegram_id,),
            ).fetchall()
        out: list[ClassSlot] = []
        for r in rows:
            sh, sm = divmod(r["start_min"], 60)
            eh, em = divmod(r["end_min"], 60)
            out.append(
                ClassSlot(
                    weekday=r["weekday"],
                    start=time(sh, sm),
                    end=time(eh, em),
                    course_name=r["course_name"],
                    teacher=r["teacher"] or "",
                    room=r["room"] or "",
                    column_key=r["column_key"] or "",
                )
            )
        return out

    def set_homework(self, telegram_id: int, course_key: str, text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO homework(telegram_id, course_key, text, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id, course_key) DO UPDATE SET
                    text=excluded.text, updated_at=excluded.updated_at
                """,
                (telegram_id, course_key, text.strip(), datetime.utcnow().isoformat()),
            )

    def get_homework(self, telegram_id: int, course_key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT text FROM homework WHERE telegram_id=? AND course_key=?",
                (telegram_id, course_key),
            ).fetchone()
        return row["text"] if row else None

    def clear_homework(self, telegram_id: int, course_key: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM homework WHERE telegram_id=? AND course_key=?",
                (telegram_id, course_key),
            )
            return cur.rowcount > 0

    def clear_all_homework(self, telegram_id: int) -> int:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM homework WHERE telegram_id=?", (telegram_id,))
            return cur.rowcount

    def all_homework(self, telegram_id: int) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT course_key, text FROM homework WHERE telegram_id=?",
                (telegram_id,),
            ).fetchall()
        return {r["course_key"]: r["text"] for r in rows}

    def map_live_message(
        self,
        chat_id: int,
        message_id: int,
        telegram_id: int,
        course_key: str,
        fingerprint: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO live_messages(
                    chat_id, message_id, telegram_id, course_key, slot_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chat_id, message_id, telegram_id, course_key, fingerprint, datetime.utcnow().isoformat()),
            )

    def get_live_message(self, chat_id: int, message_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM live_messages WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            ).fetchone()

    def was_reminder_sent(self, telegram_id: int, fingerprint: str, kind: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM reminder_log WHERE telegram_id=? AND fingerprint=? AND kind=?",
                (telegram_id, fingerprint, kind),
            ).fetchone()
        return row is not None

    def mark_reminder_sent(self, telegram_id: int, fingerprint: str, kind: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO reminder_log(telegram_id, fingerprint, kind, sent_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, fingerprint, kind, datetime.utcnow().isoformat()),
            )

    def get_groups(self, telegram_id: int) -> Optional[StudentGroups]:
        user = self.get_user(telegram_id)
        if not user or not user["group_json"]:
            return None
        data = json.loads(user["group_json"])
        return StudentGroups(**data)
