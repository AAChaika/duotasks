"""
GamifiedTaskBot — Step 3c: Completions + XP + Streaks + Badges

What’s new in 3c
- `completions` table
- Inline ✅ DONE now records a completion
- XP formula: 10 × difficulty + 2 × streak (streak bonus capped at +20)
- Streak rule: first completion of a day sets/keeps the streak; missing a day resets
- Level: 1 + xp // 100
- Badge unlocks by streak thresholds (Jerboa→Elephant)
- Double-tap guard: ignore duplicate DONE for the same task within 2 seconds

Next steps (3d/3e): reminders and ready-list bonus

Deploy
- Env: TELEGRAM_TOKEN=<token>
- Optional: DB_PATH=/data/gamify.db (default)
- Start command: python main.py
"""
from __future__ import annotations

# top of file
import asyncio
DB_LOCK = asyncio.Lock()
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

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
    db_dir = os.path.dirname(DB_PATH) or "."
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")  # better concurrency
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

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                difficulty INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                completed_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
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
            if row[1] != teleg_user.id or row[2] != teleg_user.username:
                conn.execute(
                    "UPDATE users SET chat_id=?, username=? WHERE user_id=?",
                    (teleg_user.id, teleg_user.username, teleg_user.id),
                )
            return UserProfile(*row)

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


# ========================= BADGE UTILS =========================

BADGE_TIERS = [
    (300, "Elephant 🐘"),
    (200, "Tiger 🐯"),
    (150, "Lion 🦁"),
    (100, "Jaguar 🐆🏅"),
    (75,  "Leopard 🐆✨"),
    (50,  "Cheetah 🐆"),
    (30,  "Caracal 😼"),
    (21,  "Wolf 🐺"),
    (14,  "Fox 🦊"),
    (7,   "Lynx 🐱"),
    (3,   "Meerkat 🐹"),
    (1,   "Jerboa 🐭"),
]


def badge_name_for_streak(streak: int) -> str | None:
    for threshold, name in BADGE_TIERS:
        if streak >= threshold:
            return name
    return None


def badge_tier_for_streak(streak: int) -> int:
    for threshold, _ in BADGE_TIERS:
        if streak >= threshold:
            return threshold
    return 0


# ========================= BOT HANDLERS =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    user = update.effective_user
    get_or_create_user(user)
    now_local = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    msg = (
        "👋 Welcome! I turn your tasks into XP, levels, streaks and badges — like Duolingo, but for productivity.\n\n"
        "Use /addtask to add a task, /list to view them, and /profile for stats.\n"
        f"Local time: <b>{now_local}</b> (Europe/Belgrade)."
    )
    await update.message.reply_html(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Commands:\n"
        "/start — Start and set up\n"
        "/help — Commands and tips\n"
        "/addtask — Add a new task\n"
        "/list — View active tasks\n"
        "/remove — Delete a task\n"
        "/profile — XP, Level, Streak, Badges\n"
        "/reminder — Daily reminders toggle (next steps)\n"
    )
    await update.message.reply_text(text)


# ---- /profile ----
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    user = update.effective_user
    up = get_or_create_user(user)

    current_badge = badge_name_for_streak(up.streak_current) or "—"
    best_badge = badge_name_for_streak(up.streak_best) or "—"

    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND active=1", (user.id,))
        (active_count,) = cur.fetchone()

    text = (
        "👤 <b>Profile</b>\n"
        f"XP: <b>{up.xp}</b>\n"
        f"Level: <b>{up.level}</b>\n"
        f"Streak: <b>{up.streak_current}</b> (best {up.streak_best})\n"
        f"Current badge: <b>{current_badge}</b> · Best badge: <b>{best_badge}</b>\n"
        f"Active tasks: <b>{active_count}</b>\n"
    )
    await update.message.reply_html(text)


# ---- /addtask (wizard) ----
TITLE, DIFF = range(2)

async def addtask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user)
    await update.message.reply_text("What’s the task title?")
    return TITLE


async def addtask_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_title"] = (update.message.text or "").strip()[:120]
    await update.message.reply_text("Difficulty? Send 1 (easy), 2 (medium), or 3 (hard). Default is 1.")
    return DIFF


async def addtask_diff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text not in {"1", "2", "3"}:
        difficulty = 1
    else:
        difficulty = int(text)

    title = context.user_data.get("new_title", "Untitled")
    user_id = update.effective_user.id

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (user_id, title, difficulty, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (user_id, title, difficulty, datetime.now(TZ).isoformat()),
        )

    await update.message.reply_html(f"Added: <b>{title}</b> (difficulty {difficulty}).")
    context.user_data.pop("new_title", None)
    return ConversationHandler.END


async def addtask_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_title", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ---- /list & /remove ----

def build_task_kb(rows: list[tuple[int, str]]):
    buttons = []
    for tid, title in rows:
        buttons.append([
            InlineKeyboardButton(f"✅ Done: {title}", callback_data=f"DONE_{tid}"),
            InlineKeyboardButton("🗑️", callback_data=f"DEL_{tid}"),
        ])
    return InlineKeyboardMarkup(buttons) if buttons else None


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, title FROM tasks WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 25",
            (user_id,),
        )
        rows = cur.fetchall()

    if not rows:
        await update.message.reply_text(
            "No active tasks. Use /addtask to create one.\n\n"
            "Ideas: quick chores, 10‑min study, a movie to start, 3 pages to read."
        )
        return

    await update.message.reply_text("Your tasks:", reply_markup=build_task_kb(rows))


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, title FROM tasks WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 25",
            (user_id,),
        )
        rows = cur.fetchall()

    if not rows:
        await update.message.reply_text("Nothing to remove. Use /addtask to create one.")
        return

    buttons = [[InlineKeyboardButton(f"🗑️ {title}", callback_data=f"DEL_{tid}")] for tid, title in rows]
    await update.message.reply_text("Choose a task to delete:", reply_markup=InlineKeyboardMarkup(buttons))


# ========================= COMPLETION LOGIC =========================

def _today() -> date:
    return datetime.now(TZ).date()


def _award_xp_and_streak(user_id: int, base_points: int) -> tuple[int, int, int, bool]:
    """
    Returns (gained_xp, new_level, new_streak, badge_unlocked_bool)
    """
    today = _today()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT xp, level, streak_current, streak_best, last_activity_date, best_badge_tier FROM users WHERE user_id=?",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("User not found")
        xp, level, streak_current, streak_best, last_date_str, best_badge_tier = row

        # Streak
        if last_date_str:
            last_date = date.fromisoformat(last_date_str)
            if last_date == today:
                # already counted today; streak unchanged
                pass
            elif last_date == today - timedelta(days=1):
                streak_current += 1
            else:
                streak_current = 1
        else:
            streak_current = 1

        if streak_current > streak_best:
            streak_best = streak_current

        # XP
        streak_bonus = min(streak_current, 10) * 2
        gained = base_points + streak_bonus
        xp += gained
        new_level = 1 + xp // 100

        # Badge unlock check
        new_tier = badge_tier_for_streak(streak_current)
        unlocked = new_tier > best_badge_tier
        if unlocked:
            best_badge_tier = new_tier

        conn.execute(
            "UPDATE users SET xp=?, level=?, streak_current=?, streak_best=?, last_activity_date=?, best_badge_tier=? WHERE user_id=?",
            (xp, new_level, streak_current, streak_best, today.isoformat(), best_badge_tier, user_id),
        )

    return gained, new_level, streak_current, unlocked


async def _handle_completion(task_id: int, user_id: int) -> tuple[int, bool, str]:
    now = datetime.now(TZ)
    now_iso = now.isoformat()
    async with DB_LOCK:
        with get_conn() as conn:
            # start a write transaction immediately
            conn.execute("BEGIN IMMEDIATE;")

            # 1) validate task & get difficulty (active=1)
            cur = conn.execute(
                "SELECT difficulty FROM tasks WHERE id=? AND user_id=? AND active=1",
                (task_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                raise ValueError("Task not found or inactive")
            difficulty = int(row[0])

            # 2) double-tap guard (2s)
            cur = conn.execute(
                "SELECT completed_at FROM completions WHERE task_id=? AND user_id=? "
                "ORDER BY id DESC LIMIT 1",
                (task_id, user_id),
            )
            last = cur.fetchone()
            if last:
                try:
                    last_dt = datetime.fromisoformat(last[0])
                    if (now - last_dt).total_seconds() <= 2:
                        conn.rollback()
                        return 0, False, ""  # ignore
                except Exception:
                    pass

            # 3) insert completion
            conn.execute(
                "INSERT INTO completions (task_id, user_id, completed_at) VALUES (?, ?, ?)",
                (task_id, user_id, now_iso),
            )

            # 4) archive the task so it disappears from /list
            conn.execute(
                "UPDATE tasks SET active=0 WHERE id=? AND user_id=?",
                (task_id, user_id),
            )

            # 5) fetch user for streak/xp
            cur = conn.execute(
                "SELECT xp, level, streak_current, streak_best, last_activity_date, best_badge_tier "
                "FROM users WHERE user_id=?",
                (user_id,),
            )
            xp, level, streak_current, streak_best, last_date_str, best_badge_tier = cur.fetchone()

            # 6) streak math (Europe/Belgrade day)
            today = now.date()
            if last_date_str:
                last_date = date.fromisoformat(last_date_str)
                if last_date == today:
                    pass
                elif last_date == today - timedelta(days=1):
                    streak_current += 1
                else:
                    streak_current = 1
            else:
                streak_current = 1

            if streak_current > streak_best:
                streak_best = streak_current

            # 7) xp/level
            base_points = 10 * max(1, min(difficulty, 3))
            streak_bonus = min(streak_current, 10) * 2
            gained = base_points + streak_bonus
            xp += gained
            level = 1 + xp // 100

            # 8) badge unlock
            def _tier(s: int) -> int:
                for threshold, _ in BADGE_TIERS:
                    if s >= threshold:
                        return threshold
                return 0
            new_tier = _tier(streak_current)
            unlocked = new_tier > best_badge_tier
            if unlocked:
                best_badge_tier = new_tier

            # 9) persist user update
            conn.execute(
                "UPDATE users SET xp=?, level=?, streak_current=?, streak_best=?, "
                "last_activity_date=?, best_badge_tier=? WHERE user_id=?",
                (xp, level, streak_current, streak_best, today.isoformat(), best_badge_tier, user_id),
            )

            conn.commit()

    badge_name = badge_name_for_streak(streak_current) if unlocked else ""
    return gained, unlocked, badge_name or ""

# ---- Callbacks for inline buttons ----
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id

    try:
        if data.startswith("DEL_"):
            tid = int(data.split("_", 1)[1])
            with get_conn() as conn:
                conn.execute("UPDATE tasks SET active=0 WHERE id=? AND user_id=?", (tid, user_id))
            await query.edit_message_text("🗑️ Task removed.")

        elif data.startswith("DONE_"):
            tid = int(data.split("_", 1)[1])
            gained, unlocked, badge_name = await _handle_completion(tid, user_id)
            if gained == 0:
                await query.edit_message_text("⏱️ Already counted.")
                return
            msg = f"✅ Task completed! You gained <b>{gained} XP</b>."
            if unlocked and badge_name:
                msg += f"\n🎉 New badge unlocked: <b>{badge_name}</b>!"
            await query.edit_message_text(msg, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.exception("Callback error: %s", e)
        await query.edit_message_text(f"⚠️ {e}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling an update: %s", context.error)


# ========================= MAIN =========================
def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Please set TELEGRAM_TOKEN env variable")

    init_db()

    app = Application.builder().token(token).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))

    # Add-task conversation
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("addtask", addtask_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_title)],
            DIFF: [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_diff)],
        },
        fallbacks=[CommandHandler("cancel", addtask_cancel)],
        name="addtask_conversation",
        persistent=False,
    )
    app.add_handler(add_conv)

    # Lists and callbacks
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CallbackQueryHandler(on_button))

    app.add_error_handler(error_handler)

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

# --- requirements.txt (for your repo) ---
# python-telegram-bot>=20
# tzdata  # only needed on Windows or alpine linux
