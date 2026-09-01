from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
FERNET_KEY = os.getenv("FERNET_KEY", "")
TZ = os.getenv("TZ", "Asia/Yerevan")
MOODLE_BASE_URL = os.getenv("MOODLE_BASE_URL", "https://moodle.ufar.am").rstrip("/")

DATA_DIR = ROOT / "data"
FALLBACK_DIR = DATA_DIR / "fallback"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "ufaragent.db"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
