# UFAR study bot

Telegram bot that reads your UFAR Moodle / local timetable and sends:

- **Every day at 06:00** (`Asia/Yerevan`) — today's classes
- **Every Friday at 23:00** — the week view
- **Live feed** — 30 minutes before class, 5–1 minute RUSH, and class-ended / next-class notices
- **Homework** — reply to a live-feed message to attach homework to that class

## Setup

```powershell
cd C:\Users\Asus\Projects\ufaragent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env` must contain:

```
TELEGRAM_BOT_TOKEN=...
FERNET_KEY=...
TZ=Asia/Yerevan
MOODLE_BASE_URL=https://moodle.ufar.am
```

Fallback timetable files live in `data/fallback/` (PDF + group XLSX).

## Run

Keep this process running on your PC (needed for scheduled jobs and live reminders):

```powershell
python -m bot
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Moodle login + group (full name from roster, or `TP 1` / `TD 2` / …) |
| `/today` | Today's classes |
| `/week` | Full week |
| `/refresh` | Re-search Moodle, else fallback files |
| `/group <query>` | Change group |
| `/logout` | Delete stored data |

## Homework

Reply to any live-feed message (reminder or “class ended”) with the assignment text. It is shown on `/today`, `/week`, and reminders until that class happens again.
