"""
GamifiedTaskBot — Step 2 Skeleton

Purpose: Verify bot + hosting + CI/CD. No database yet.
Commands implemented:
- /start   → welcome + short how-to
- /help    → command list
- /profile → placeholder stats (static)

Run locally:
  export TELEGRAM_TOKEN=123456:ABC...
  pip install "python-telegram-bot>=20"
  python main.py

Deploy (Railway/Render): set start command to `python main.py`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Config ---
TZ = ZoneInfo("Europe/Belgrade")
BOT_NAME = "GamifiedTaskBot"

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(BOT_NAME)


# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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
        "/profile — XP, Level, Streak, Badges (placeholder now)\n"
        "/reminder — Daily reminders toggle (next step)\n"
    )
    await update.message.reply_text(text)


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Placeholder values — DB will arrive in Step 3
    text = (
        "👤 <b>Profile</b>\n"
        "XP: <b>0</b>\n"
        "Level: <b>1</b>\n"
        "Streak: <b>0</b> (best 0)\n"
        "Current badge: — · Best badge: —\n"
        "Active tasks: <b>0</b>\n\n"
        "(Stats are placeholders for infrastructure test.)"
    )
    await update.message.reply_html(text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling an update: %s", context.error)


# --- Main ---
def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Please set TELEGRAM_TOKEN env variable")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))

    app.add_error_handler(error_handler)

    # Polling is fine for MVP hosting; we can switch to webhooks later if needed.
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

# --- requirements.txt (for your repo) ---
# python-telegram-bot>=20
# tzdata  # only needed on Windows or alpine linux
