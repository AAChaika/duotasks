"""
GamifiedTaskBot — Step 3a: Users table + real /profile

What’s new in 3a
- SQLite database at /data/gamify.db (configurable via DB_PATH env)
- users table with xp/level/streak/badges/reminder flags
- /start now upserts the user (get-or-create)
- /profile reads real values from DB (no tasks/completions yet)

Deploy
- Env: TELEGRAM_TOKEN=<your token>
- Optional: DB_PATH=/data/gamify.db   (default)
- Start command: python main.py

Requirements
- python-telegram-bot>=20
- Python 3.10+
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Config ---
TZ = ZoneInfo("Europe/Belgrade")
BOT_NAME = "GamifiedTaskBot"
DB_PATH = os.getenv("DB_PATH", "/data/gamify.db")

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(BOT_NAME)


# ========================= DB LAYER =========================

def get_conn() -> sqlite3.Connection:
    # Ensure directory exists
    db_dir = os.path.dirname(DB_PATH) or "."
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                username TEXT,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                streak_current INTEGER NOT NULL DEFAULT 0,
                streak_best INTEGER NOT NULL DEFAULT 0,
                best_badge_tier INTEGER NOT NULL DEFAULT 0,
                last_activity_date TEXT,
                reminder_enabled INTEGER NOT NULL DEFAULT 1,
                ready_list_bonus_date TEXT
            );
            """
        )


@dataclass
class UserProfile:
    user_id: int
    chat_id: int
    username: str | None
    xp: int
    level: int
    streak_current: int
    streak_best: int
    best_badge_tier: int
    last_activity_date: str | None
    reminder_enabled: int
    ready_list_bonus_date: str | None


def get_or_create_user(teleg_user) -> UserProfile:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT user_id, chat_id, username, xp, level, streak_current, streak_best, best_badge_tier, last_activity_date, reminder_enabled, ready_list_bonus_date "
            "FROM users WHERE user_id=?",
            (teleg_user.id,),
        )
        row = cur.fetchone()
        if row:
            # Update chat_id/username if changed
            if row[1] != teleg_user.id or row[2] != teleg_user.username:
                conn.execute(
                    "UPDATE users SET chat_id=?, username=? WHERE user_id=?",
                    (teleg_user.id, teleg_user.username, teleg_user.id),
                )
            return UserProfile(*row)

        # Insert if not exists
        conn.execute(
            "INSERT INTO users (user_id, chat_id, username) VALUES (?, ?, ?)",
            (teleg_user.id, teleg_user.id, teleg_user.username),
        )
        return UserProfile(
            user_id=teleg_user.id,
            chat_id=teleg_user.id,
            username=teleg_user.username,
            xp=0,
            level=1,
            streak_current=0,
            streak_best=0,
            best_badge_tier=0,
            last_activity_date=None,
            reminder_enabled=1,
            ready_list_bonus_date=None,
        )


# ========================= BOT HANDLERS =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()  # idempotent, safe to call
    user = update.effective_user
    profile = get_or_create_user(user)
    now_local = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")

    msg = (
        "👋 Welcome! I turn your tasks into XP, levels, streaks and badges — like Duolingo, but for productivity.\n\n"
        "Try /addtask (coming next), /list (coming next), and /profile to view stats.\n"
        f"Local time: <b>{now_local}</b> (Europe/Belgrade)."
    )
    await update.message.reply_html(msg)
    logger.info("/start by %s (%s)", user.username, user.id)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Commands:\n"
        "/start — Start and set up\n"
        "/help — Commands and tips\n"
        "/addtask — Add a new task (next step)\n"
        "/list — View active tasks (next step)\n"
        "/remove — Delete a task (next step)\n"
        "/profile — XP, Level, Streak, Badges\n"
        "/reminder — Daily reminders toggle (next steps)\n"
    )
    await update.message.reply_text(text)


def _badge_name_for_streak(streak: int) -> str | None:
    # Mirrors MVP v1.1 thresholds
    tiers = [
        (300, "Elephant 🐘"),
        (200, "Tiger 🐯"),
        (150, "Lion 🦁"),
        (100, "Jaguar 🐆🏅"),
        (75, "Leopard 🐆✨"),
        (50, "Cheetah 🐆"),
        (30, "Caracal 😼"),
        (21, "Wolf 🐺"),
        (14, "Fox 🦊"),
        (7, "Lynx 🐱"),
        (3, "Meerkat 🐹"),
        (1, "Jerboa 🐭"),
    ]
    for threshold, name in tiers:
        if streak >= threshold:
            return name
    return None


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    user = update.effective_user
    up = get_or_create_user(user)

    current_badge = _badge_name_for_streak(up.streak_current) or "—"
    best_badge = _badge_name_for_streak(up.streak_best) or "—"

    text = (
        "👤 <b>Profile</b>\n"
        f"XP: <b>{up.xp}</b>\n"
        f"Level: <b>{up.level}</b>\n"
        f"Streak: <b>{up.streak_current}</b> (best {up.streak_best})\n"
        f"Current badge: <b>{current_badge}</b> · Best badge: <b>{best_badge}</b>\n"
        f"Active tasks: <b>0</b>\n\n"  # tasks will be real in Step 3b
        "(Data is live for users; tasks arrive next.)"
    )
    await update.message.reply_html(text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling an update: %s", context.error)


# ========================= MAIN =========================
def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Please set TELEGRAM_TOKEN env variable")

    init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))

    app.add_error_handler(error_handler)

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

# --- requirements.txt (for your repo) ---
# python-telegram-bot>=20
# tzdata  # only needed on Windows or alpine linux
