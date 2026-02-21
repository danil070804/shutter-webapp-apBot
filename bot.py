import asyncio
import sqlite3
import time
import math
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile, WebAppInfo, InputMediaPhoto,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from config import (
    TOKEN,
    ADMIN_IDS,
    ADMIN_CHAT_ID,
    PAYOUTS_CHANNEL_ID,
    PROFILE_IMAGE_PATH,
    DB_PATH,
    PROJECT_CHAT_ID,
    TIMEZONE,
    RANK_LEVELS,
    WEBAPP_URL,
)

try:
    from notifications_pro import init_notifier

    NOTIFICATIONS_ENABLED = True
except ImportError:
    NOTIFICATIONS_ENABLED = False

notifier = None


# ==========================
# FSM Состояния
# ==========================

class ApplicationForm(StatesGroup):
    q1 = State()
    q2 = State()
    q3 = State()


class ProfitIssue(StatesGroup):
    worker_id = State()
    amount = State()
    direction = State()
    percent = State()


class AdminLinks(StatesGroup):
    waiting_url = State()


class AdminRequisites(StatesGroup):
    waiting_text = State()


class WorkerStatsFSM(StatesGroup):
    waiting_user_id = State()


class RoleChangeFSM(StatesGroup):
    waiting_user_id = State()


class MenuButtonsFSM(StatesGroup):
    waiting_text = State()


class MenuButtonsPickFSM(StatesGroup):
    waiting_pick = State()


class BroadcastFSM(StatesGroup):
    waiting_message = State()
    waiting_confirm = State()


class AdminSetGoalFSM(StatesGroup):
    waiting_goal = State()


# ==========================
# БАЗА ДАННЫХ
# ==========================

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            q1 TEXT,
            q2 TEXT,
            q3 TEXT,
            profits_count INTEGER NOT NULL DEFAULT 0,
            profits_sum REAL NOT NULL DEFAULT 0,
            goal_profits INTEGER NOT NULL DEFAULT 0,
            current_streak INTEGER NOT NULL DEFAULT 0,
            max_streak INTEGER NOT NULL DEFAULT 0,
            last_profit_date TEXT,
            joined_at INTEGER,
            role TEXT NOT NULL DEFAULT 'worker',
            mentor_id INTEGER,
            referrer_id INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS profits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            total_amount REAL NOT NULL,
            worker_percent REAL NOT NULL,
            worker_amount REAL NOT NULL,
            direction TEXT,
            mentor_id INTEGER,
            mentor_amount REAL DEFAULT 0,
            referrer_id INTEGER,
            referrer_amount REAL NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_user_id INTEGER,
            details TEXT,
            created_at INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, username, status, q1, q2, q3,
               profits_count, profits_sum,
               goal_profits, current_streak, max_streak, last_profit_date,
               joined_at, role, mentor_id, referrer_id
        FROM users WHERE user_id = ?
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "user_id": row[0], "username": row[1], "status": row[2],
        "q1": row[3], "q2": row[4], "q3": row[5],
        "profits_count": row[6] or 0, "profits_sum": row[7] or 0,
        "goal_profits": row[8] or 0, "current_streak": row[9] or 0,
        "max_streak": row[10] or 0, "last_profit_date": row[11],
        "joined_at": row[12], "role": row[13] or "worker",
        "mentor_id": row[14], "referrer_id": row[15],
    }


def create_or_update_user(user_id: int, username: Optional[str], status: str,
                          referrer_id: Optional[int] = None) -> None:
    """Создаёт пользователя или обновляет username/status, не перезатирая роль."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Если пользователь уже есть — сохраняем текущую роль
    cur.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
    existing = cur.fetchone()
    if existing and existing[0]:
        role = existing[0]
    else:
        role = "admin" if user_id in ADMIN_IDS else "worker"

    cur.execute("""
        INSERT INTO users (user_id, username, status, role, referrer_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            status = excluded.status
    """, (user_id, username, status, role, referrer_id))

    if referrer_id is not None:
        cur.execute(
            "UPDATE users SET referrer_id = COALESCE(referrer_id, ?) WHERE user_id = ?",
            (referrer_id, user_id),
        )

    conn.commit()
    conn.close()


def update_user_answers(user_id: int, q1: Optional[str] = None, q2: Optional[str] = None,
                        q3: Optional[str] = None, status: Optional[str] = None) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    fields = []
    values = []
    if q1 is not None:
        fields.append("q1 = ?")
        values.append(q1)
    if q2 is not None:
        fields.append("q2 = ?")
        values.append(q2)
    if q3 is not None:
        fields.append("q3 = ?")
        values.append(q3)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if not fields:
        conn.close()
        return
    values.append(user_id)
    query = "UPDATE users SET " + ", ".join(fields) + " WHERE user_id = ?"
    cur.execute(query, values)
    conn.commit()
    conn.close()


def approve_user(user_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET status = ?, joined_at = ? WHERE user_id = ?",
        ("approved", int(time.time()), user_id),
    )
    conn.commit()
    conn.close()


def reject_user(user_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = ? WHERE user_id = ?", ("rejected", user_id))
    conn.commit()
    conn.close()


def set_user_role(user_id: int, role: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
    conn.commit()
    conn.close()


def get_mentor_profit_count(user_id: int, mentor_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM profits WHERE user_id = ? AND mentor_id = ?", (user_id, mentor_id))
    count = cur.fetchone()[0] or 0
    conn.close()
    return count


def get_workers_for_mentor(mentor_id: int) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, username, profits_count, profits_sum, current_streak, 
               max_streak, last_profit_date, goal_profits
        FROM users WHERE mentor_id = ? AND status = 'approved'
        ORDER BY profits_count DESC, profits_sum DESC
    """, (mentor_id,))
    rows = cur.fetchall()
    conn.close()
    return [{
        "user_id": r[0], "username": r[1], "profits_count": r[2] or 0,
        "profits_sum": r[3] or 0, "current_streak": r[4] or 0,
        "max_streak": r[5] or 0, "last_profit_date": r[6], "goal_profits": r[7] or 0,
    } for r in rows]


def get_all_mentors() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE role = 'mentor' AND status = 'approved'")
    rows = cur.fetchall()
    conn.close()
    return [{"user_id": r[0], "username": r[1]} for r in rows]


def parse_iso_date(s: str | None) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def get_inactive_workers_for_mentor(mentor_id: int, days: int) -> list[dict]:
    days = max(1, int(days))
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    workers = get_workers_for_mentor(mentor_id)
    inactive = []
    for w in workers:
        last_day = parse_iso_date(w.get("last_profit_date"))
        if last_day is None:
            w["_inactive_days"] = None
            inactive.append(w)
        else:
            delta = (today - last_day).days
            if delta >= days:
                w["_inactive_days"] = delta
                inactive.append(w)
    return inactive


def add_profit_record(user_id: int, admin_id: int, total_amount: float,
                      worker_percent: float, direction: str) -> Dict[str, Any]:
    user = get_user(user_id)
    if not user or user["status"] != "approved":
        raise ValueError("Worker not approved")

    mentor_id = user["mentor_id"]
    referrer_id = user.get("referrer_id")
    base_worker_amount = round(total_amount * worker_percent / 100.0, 2)
    mentor_amount = 0.0
    worker_amount = base_worker_amount
    referrer_amount = 0.0

    if mentor_id:
        used = get_mentor_profit_count(user_id, mentor_id)
        if used < 5:
            mentor_amount = round(base_worker_amount * 0.20, 2)
            worker_amount = base_worker_amount - mentor_amount

    if referrer_id:
        referrer_amount = round(base_worker_amount * 0.05, 2)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO profits (user_id, admin_id, total_amount, worker_percent, worker_amount,
            direction, mentor_id, mentor_amount, referrer_id, referrer_amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, admin_id, total_amount, worker_percent, worker_amount,
          direction, mentor_id, mentor_amount, referrer_id, referrer_amount, int(time.time())))

    cur.execute("""
        UPDATE users SET profits_count = profits_count + 1, profits_sum = profits_sum + ?
        WHERE user_id = ?
    """, (worker_amount, user_id))

    # Обновляем стрик
    profit_day = datetime.now(ZoneInfo(TIMEZONE)).date()
    cur.execute("SELECT current_streak, max_streak, last_profit_date FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()

    if row:
        current, max_s, last_s = int(row[0] or 0), int(row[1] or 0), row[2]
        last_day = parse_iso_date(last_s)

        if last_day == profit_day:
            new_current = current
        else:
            yesterday = profit_day - timedelta(days=1)
            if last_day == yesterday:
                new_current = current + 1 if current > 0 else 1
            else:
                new_current = 1

        new_max = max(max_s, new_current)
        cur.execute("""
            UPDATE users SET current_streak = ?, max_streak = ?, last_profit_date = ? 
            WHERE user_id = ?
        """, (new_current, new_max, profit_day.isoformat(), user_id))
    else:
        new_current, new_max = 1, 1

    conn.commit()
    conn.close()

    return {
        "worker_amount": worker_amount, "mentor_id": mentor_id,
        "mentor_amount": mentor_amount, "referrer_id": referrer_id,
        "referrer_amount": referrer_amount, "current_streak": new_current,
        "max_streak": new_max,
    }


def set_setting(key: str, value: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))
    conn.commit()
    conn.close()


def get_setting(key: str, default: str | None = None) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default


def get_approved_user_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE status = 'approved'")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_global_stats() -> Dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*), COUNT(CASE WHEN status = 'approved' THEN 1 END), COUNT(CASE WHEN status = 'pending' THEN 1 END) FROM users")
    total, approved, pending = cur.fetchone()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(total_amount), 0) FROM profits")
    profits, amount = cur.fetchone()
    conn.close()
    return {
        "total_users": total or 0, "total_approved": approved or 0,
        "total_pending": pending or 0, "profits_count": profits or 0,
        "total_amount": amount or 0,
    }




def get_users_stats() -> Dict[str, Any]:
    # Backward-compatible alias (admin dashboard expects this name)
    return get_global_stats()


def get_kassa_stats() -> Dict[str, float]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = int(time.time())
    day_start = int(datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0).timestamp())
    week_start = now - 7 * 24 * 3600
    month_start = now - 30 * 24 * 3600

    def sum_since(ts: int) -> float:
        cur.execute("SELECT COALESCE(SUM(total_amount), 0) FROM profits WHERE created_at >= ?", (ts,))
        return float(cur.fetchone()[0] or 0)

    stats = {"all_time": sum_since(0), "month": sum_since(month_start), "week": sum_since(week_start),
             "day": sum_since(day_start)}
    conn.close()
    return stats


def get_admin_logs(limit: int = 20) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, admin_id, action, target_user_id, details, created_at FROM admin_logs ORDER BY id DESC LIMIT ?",
        (limit,))
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "admin_id": r[1], "action": r[2], "target_user_id": r[3], "details": r[4] or "",
             "created_at": r[5]} for r in rows]


def format_ts(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=ZoneInfo(TIMEZONE)).strftime("%d.%m %H:%M")
    except Exception:
        return str(ts)


def kyiv_today() -> date:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def get_rank_for_profits(profits_count: int) -> Dict[str, str]:
    best = RANK_LEVELS[0] if RANK_LEVELS else {"emoji": "👤", "name": "Worker", "min_profits": 0}
    for lvl in (RANK_LEVELS or []):
        if profits_count >= int(lvl.get("min_profits", 0)):
            best = lvl
        else:
            break
    return {"emoji": str(best.get("emoji", "👤")), "name": str(best.get("name", "Worker"))}


def get_user_rank_position(user_id: int) -> int | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE status = 'approved' ORDER BY profits_count DESC, profits_sum DESC")
    rows = cur.fetchall()
    conn.close()
    for i, (uid,) in enumerate(rows, start=1):
        if int(uid) == int(user_id):
            return i
    return None


def format_last_profit_date(last_profit_date: str | None) -> str:
    if not last_profit_date:
        return "—"
    try:
        return datetime.fromisoformat(last_profit_date).date().strftime("%d.%m.%Y")
    except Exception:
        return str(last_profit_date)


def render_progress_bar(done: int, total: int, length: int = 10) -> str:
    if total <= 0:
        return ""
    done = max(0, min(done, total))
    filled = int(round((done / total) * length))
    return "█" * filled + "░" * (length - filled)


def set_user_goal(user_id: int, goal_profits: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET goal_profits = ? WHERE user_id = ?", (goal_profits, user_id))
    conn.commit()
    conn.close()


# ==========================
# КЛАВИАТУРЫ
# ==========================

def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="📊 NFT"), KeyboardButton(text="📈 TRADE"), KeyboardButton(text="📣 ESCORT")],
            [KeyboardButton(text="🧪 NARKO"), KeyboardButton(text="₿ BTC Search")],
            [KeyboardButton(text="🌐 Сайт Трейд"), KeyboardButton(text="🌐 Сайт NFT")],
        ],
    )


def admin_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="➕ Выдать профит"), KeyboardButton(text="📨 Заявки")],
            [KeyboardButton(text="📊 Общая статистика"), KeyboardButton(text="👥 Стата воркера")],
            [KeyboardButton(text="🎭 Роли пользователей")],
            [KeyboardButton(text="🔗 Настроить ссылки комьюнити")],
            [KeyboardButton(text="💳 Реквизиты Прамик")],
            [KeyboardButton(text="🧱 Кнопки меню")],
            [KeyboardButton(text="📢 Рассылка")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
    )


def admin_dashboard_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Выдать профит", callback_data="adm:profit"),
                InlineKeyboardButton(text="👤 Пользователь", callback_data="adm:user"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
                InlineKeyboardButton(text="🧾 Логи", callback_data="adm:logs"),
            ],
            [
                InlineKeyboardButton(text="📨 Заявки", callback_data="adm:apps"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm:settings"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back")],
        ]
    )



def admin_settings_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Реквизиты Прамик", callback_data="admset:req"),
            ],
            [
                InlineKeyboardButton(text="🧱 Кнопки меню", callback_data="admset:menu"),
            ],
            [
                InlineKeyboardButton(text="📢 Рассылка", callback_data="admset:mail"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:panel"),
            ],
        ]
    )


# ... (все импорты и функции БД остаются такими же как в предыдущем рабочем коде)

def dashboard_kb(user: Dict[str, Any]) -> InlineKeyboardMarkup:
    is_admin = user.get("user_id") in ADMIN_IDS
    is_mentor = user.get("role") == "mentor"

    # WebApp: не передаём данные в query (безопаснее) — WebApp сам подтянет профиль через initData
    webapp_url_with_data = WEBAPP_URL.rstrip('/') if WEBAPP_URL else ''

    buttons = [
        [
            InlineKeyboardButton(text="📊 Мой профиль", callback_data="ip:profile"),
            InlineKeyboardButton(text="🎯 Цель", callback_data="panel:goal"),
        ],
        [InlineKeyboardButton(text="🔥 Streak", callback_data="panel:streak")],
        [
            InlineKeyboardButton(text="🧑‍💻 Комьюнити", callback_data="ip:community"),
            InlineKeyboardButton(text="📟 Прямик", callback_data="ip:pramik"),
        ],
        [
            InlineKeyboardButton(text="🧑‍🏫 Наставники", callback_data="ip:mentors"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="ip:settings"),
        ],
        [InlineKeyboardButton(text="🧳 Реферальная ссылка", callback_data="ip:referral")],
    ]

    if webapp_url_with_data:
        buttons.insert(0,
                       [InlineKeyboardButton(text="🚀 Открыть в WebApp", web_app=WebAppInfo(url=webapp_url_with_data))])

    if is_mentor:
        buttons.append([
            InlineKeyboardButton(text="👥 Мои воркеры", callback_data="panel:myworkers"),
            InlineKeyboardButton(text="⏳ Неактивные", callback_data="panel:inactive"),
        ])

    if is_admin:
        buttons.append([InlineKeyboardButton(text="🛠 Админка", callback_data="ip:admin")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ... (остальной код бота остается без изменений)


def back_to_profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="ip:profile")]]
    )


def roles_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назначить наставником", callback_data="role:mentor")],
            [InlineKeyboardButton(text="Сделать воркером", callback_data="role:worker")],
        ]
    )


def community_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Чат проекта✉️", callback_data="setlink:chat")],
            [InlineKeyboardButton(text="Выплаты💸", callback_data="setlink:payouts")],
            [InlineKeyboardButton(text="Мануалы📚", callback_data="setlink:manuals")],
            [InlineKeyboardButton(text="Инфо канал🎩", callback_data="setlink:info")],
        ]
    )


def menu_buttons_settings_kb() -> InlineKeyboardMarkup:
    """Админ: настройка кнопок главного меню (ReplyKeyboard)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 NFT", callback_data="menuset:nft")],
            [InlineKeyboardButton(text="📈 TRADE", callback_data="menuset:trade")],
            [InlineKeyboardButton(text="📣 ESCORT", callback_data="menuset:escort")],
            [InlineKeyboardButton(text="₿ BTC Search", callback_data="menuset:btc")],
            [InlineKeyboardButton(text="🧪 NARKO", callback_data="menuset:narko")],
            [InlineKeyboardButton(text="🌐 Сайт Трейд", callback_data="menuset:site_trade")],
            [InlineKeyboardButton(text="🌐 Сайт NFT", callback_data="menuset:site_nft")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menuset:back")],
        ]
    )


async def send_menu_link(bot: Bot, chat_id: int, key: str, title: str):
    """Отправляет пользователю кнопку/ссылку для выбранного пункта меню."""
    url = (get_setting(f"menu_{key}_url", "") or "").strip()
    text = (get_setting(f"menu_{key}_text", "") or "").strip()

    if not url and not text:
        await bot.send_message(chat_id, f"{title}\n\n⚠️ Пока не настроено администрацией.")
        return

    kb = None
    if url:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть", url=url)]])
    await bot.send_message(
        chat_id,
        f"<b>{title}</b>\n\n{text or url}",
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def build_application_inline_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅Подтвердить!", callback_data=f"approve:{user_id}"),
                InlineKeyboardButton(text="❌Отклонить!", callback_data=f"reject:{user_id}"),
            ]
        ]
    )


# ==========================
# УТИЛИТЫ
# ==========================

async def safe_edit_message(bot: Bot, message: Message, text: str, reply_markup=None):
    """Безопасное редактирование сообщения (текст или фото)"""
    try:
        # Если сообщение с фото - редактируем caption
        if message.photo:
            await bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=message.message_id,
                caption=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            # Если обычное текстовое сообщение
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
    except Exception as e:
        # Если не удалось отредактировать, отправляем новое
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as e2:
            print(f"Ошибка отправки сообщения: {e2}")


# ==========================
# ОТПРАВКА ПРОФИЛЯ
# ==========================

async def send_profile(bot: Bot, chat_id: int, user_id: int, message: Message | None = None):
    user = get_user(user_id)
    if not user or user.get("status") != "approved":
        text = "Профиль будет доступен после одобрения вашей заявки."
        if message:
            await safe_edit_message(bot, message, text)
        else:
            await bot.send_message(chat_id, text)
        return

    days_in_team = 0
    if user.get("joined_at"):
        try:
            joined_date = datetime.utcfromtimestamp(int(user["joined_at"])).date()
            days_in_team = (datetime.utcnow().date() - joined_date).days
        except Exception:
            pass

    role_map = {"admin": "ГЛАВАРЬ", "mentor": "НАСТАВНИК", "worker": "ВОРКЕР"}
    role_text = role_map.get(user.get("role"), "ВОРКЕР")
    username = user.get("username")
    username_text = f"@{username}" if username else "АНОНИМ"

    profits_count = int(user.get("profits_count", 0) or 0)
    profits_sum = user.get("profits_sum", 0) or 0
    try:
        profits_sum_display = f"{float(profits_sum):,.0f}".replace(",", " ")
    except Exception:
        profits_sum_display = str(profits_sum)

    current_streak = int(user.get("current_streak", 0) or 0)
    max_streak = int(user.get("max_streak", 0) or 0)
    last_profit_text = format_last_profit_date(user.get("last_profit_date"))

    today_k = kyiv_today()
    last_profit_date = parse_iso_date(user.get("last_profit_date"))
    if last_profit_date and (today_k - last_profit_date).days <= 2:
        activity_state = "🟢 В СЕТИ"
    elif current_streak > 0:
        activity_state = "🟡 НА ПАУЗЕ"
    else:
        activity_state = "🔴 ОФФЛАЙН"

    rank = get_rank_for_profits(profits_count)
    rank_name = f"{rank.get('emoji', '👤')} {rank.get('name', 'Worker')}"
    pos = get_user_rank_position(user_id)
    rank_pos_text = f"#{pos}" if pos else "#—"

    # Расчет прогресса
    next_info = {"has_next": True, "to_next": 10, "next_min": profits_count + 10}
    if RANK_LEVELS:
        for i, lvl in enumerate(RANK_LEVELS):
            if profits_count >= lvl.get("min_profits", 0):
                if i + 1 < len(RANK_LEVELS):
                    next_info = {
                        "has_next": True,
                        "to_next": RANK_LEVELS[i + 1]["min_profits"] - profits_count,
                        "next_min": RANK_LEVELS[i + 1]["min_profits"]
                    }
                else:
                    next_info = {"has_next": False}

    level_max = 5
    if next_info.get("has_next"):
        exp_done = profits_count % 10
        exp_total = 10
        exp_pct = int((exp_done / exp_total) * 100)
        exp_bar = render_progress_bar(exp_done, exp_total, length=12)
        level = min(level_max, exp_done)
        next_line = f"⏳ ДО СЛЕД. РАНГА: {next_info['to_next']} ПРОФИТОВ"
    else:
        exp_pct = 100
        exp_bar = "█" * 12
        level = level_max
        next_line = "⏳ МАКСИМАЛЬНЫЙ РАНГ"

    goal = int(user.get("goal_profits", 0) or 0)
    if goal > 0:
        goal_done = min(profits_count, goal)
        goal_pct = int((goal_done / goal) * 100)
        goal_bar = render_progress_bar(goal_done, goal, length=12)
        goal_block = f"🎯 ЦЕЛЬ: {goal} ПРОФИТОВ ({goal_pct}%)\n📈 {goal_bar}"
    else:
        goal_block = "🎯 ЦЕЛЬ: НЕ УСТАНОВЛЕНА"

    wd = today_k.weekday()
    if wd in (5, 6):
        bonus_line = "🎁 БОНУС: ВЫХОДНОЙ КЭШБЭК"
    elif wd == 0:
        bonus_line = "🎁 БОНУС: ПОНЕДЕЛЬНИК РЕСЕТ"
    else:
        bonus_line = "🎁 БОНУС: —"

    text = f"""▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
▓▓▓ <b>🎭 SHUTTER ISLAND</b> ▓▓▓
▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓

👤 <b>ОПЕРАТИВНИК:</b> {username_text}
🏷 <b>ДОЛЖНОСТЬ:</b> <code>{role_text}</code>
🆔 <b>ID:</b> <code>{user_id}</code>
📆 <b>СТАЖ:</b> {days_in_team} ДН.

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬

💰 <b>ЗАРАБОТОК:</b> <b>{profits_sum_display}</b> ₽
📊 <b>СДЕЛОК:</b> <b>{profits_count}</b> ШТ.
🔥 <b>СЕРИЯ:</b> <b>{current_streak}Д</b> | РЕКОРД <b>{max_streak}Д</b>
🕒 <b>ПОСЛ. ПРОФИТ:</b> {last_profit_text}
<b>{activity_state}</b>
{bonus_line}

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬

🏆 <b>РЕЙТИНГ:</b> <b>{rank_pos_text}</b>  •  {rank_name}
⭐ <b>УРОВЕНЬ:</b> <b>{level}</b> / {level_max}
📈 <b>ОПЫТ:</b>  <code>{exp_bar}</code> {exp_pct}%
{next_line}

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬

{goal_block}"""

    if user.get("mentor_id"):
        mentor = get_user(user["mentor_id"])
        if mentor:
            uname = mentor.get("username")
            mentor_tag = f"@{uname}" if uname else f"ID {mentor.get('user_id')}"
            text += f"\n\n🤝 <b>НАСТАВНИК:</b> {mentor_tag}"

    kb = dashboard_kb(user)

    if message:
        # Если редактируем существующее сообщение
        if message.photo:
            try:
                await bot.edit_message_caption(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    caption=text,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
                return
            except Exception:
                pass
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=text,
                reply_markup=kb,
                parse_mode="HTML"
            )
            return
        except Exception:
            # Если не удалось редактировать, отправляем новое
            pass

    # Отправка нового сообщения
    try:
        photo = FSInputFile(PROFILE_IMAGE_PATH)
        await bot.send_photo(chat_id, photo=photo, caption=text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


# ==========================
# MAIN
# ==========================

async def main():
    global notifier

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    if NOTIFICATIONS_ENABLED:
        notifier = init_notifier(bot)
        await notifier.start()
        print("✅ Smart Notifications запущены")

    init_db()

    # ==========================
    # ОБРАБОТЧИКИ КОМАНД
    # ==========================

    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        user_id = message.from_user.id
        username = message.from_user.username

        referrer_id = None
        if message.text and len(message.text.split()) > 1:
            arg = message.text.split()[1]
            if arg.startswith("ref"):
                try:
                    referrer_id = int(arg[3:])
                except ValueError:
                    referrer_id = None

        # НЕ сбрасываем статус при каждом /start
        user = get_user(user_id)

        # Текст приветствия/анкеты (без поломанных переносов строк)
        intro_text = (
            "🎭 <b>Добро пожаловать в SHUTTER ISLAND!</b>\n\n"
            "Чтобы подать заявку, ответь на 3 вопроса:\n\n"
            "<b>1. Откуда узнали о нас? 🤔</b>"
        )

        if user is None:
            create_or_update_user(user_id, username, "pending", referrer_id)
            await message.answer(intro_text, parse_mode="HTML")
            await state.set_state(ApplicationForm.q1)
            return

        # Обновляем username/role, но сохраняем текущий status
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            role = "admin" if user_id in ADMIN_IDS else "worker"
            cur.execute(
                "UPDATE users SET username = COALESCE(?, username), role = ? WHERE user_id = ?",
                (username, role, user_id),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # Перечитываем после обновления
        user = get_user(user_id)
        status = (user or {}).get("status")

        if status == "approved":
            await state.clear()
            await send_profile(bot, message.chat.id, user_id)
            return

        if status == "rejected":
            # Даем возможность подать заявку заново
            update_user_answers(user_id, q1=None, q2=None, q3=None, status="pending")
            await message.answer(
                "📝 Ваша предыдущая заявка была отклонена.\n\n"
                "Давайте подадим новую.\n\n"
                "<b>1. Откуда узнали о нас? 🤔</b>",
                parse_mode="HTML",
            )
            await state.set_state(ApplicationForm.q1)
            return

        # pending
        if user and (user.get("q1") or user.get("q2") or user.get("q3")):
            await message.answer(
                "⏳ Ваша заявка уже отправлена и ожидает рассмотрения.\n\n"
                "Как только вас одобрят — станет доступна ворк‑панель."
            )
            await state.clear()
            return

        # pending, но ответов нет — начинаем анкету
        await message.answer(intro_text, parse_mode="HTML")
        await state.set_state(ApplicationForm.q1)

    @dp.message(Command("admin"))
    async def cmd_admin(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("⛔ Нет доступа")
            return

        stats = get_global_stats()
        kassa = get_kassa_stats()
        text = (
            f"🛠 <b>Админ-дашборд</b>\n\n"
            f"👥 Воркеров: <b>{stats['total_approved']}</b>\n"
            f"📅 Профитов сегодня: <b>{kassa['day']:,.0f} ₽</b>\n\n"
            f"Выберите действие:"
        )
        await message.answer(text, reply_markup=admin_dashboard_inline_kb())

    # ==========================
    # FSM ОБРАБОТЧИКИ (Анкета)
    # ==========================

    @dp.message(ApplicationForm.q1)
    async def process_q1(message: Message, state: FSMContext):
        await state.update_data(q1=message.text)
        await message.answer("<b>2. Где работали (какие направления, команды)?🎩</b>")
        await state.set_state(ApplicationForm.q2)

    @dp.message(ApplicationForm.q2)
    async def process_q2(message: Message, state: FSMContext):
        await state.update_data(q2=message.text)
        await message.answer("<b>3. Сколько времени готовы уделять Wor'ку?🕙</b>")
        await state.set_state(ApplicationForm.q3)

    @dp.message(ApplicationForm.q3)
    async def process_q3(message: Message, state: FSMContext):
        data = await state.get_data()
        user_id = message.from_user.id
        update_user_answers(user_id, q1=data.get('q1'), q2=data.get('q2'), q3=message.text)

        await message.answer(
            "✅ Заявка отправлена на рассмотрение!\nОжидайте решения администрации.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="👤 Профиль")]], resize_keyboard=True
            )
        )
        await state.clear()

        user = get_user(user_id)
        if user and ADMIN_CHAT_ID:
            text = (
                f"📩 <b>Новая заявка</b>\n\n"
                f"От: @{user.get('username') or user_id}\n"
                f"ID: <code>{user_id}</code>\n\n"
                f"<b>1.</b> {user.get('q1')}\n"
                f"<b>2.</b> {user.get('q2')}\n"
                f"<b>3.</b> {user.get('q3')}"
            )
            await bot.send_message(ADMIN_CHAT_ID, text, reply_markup=build_application_inline_kb(user_id))

    # ==========================
    # АДМИН: Выдача профита
    # ==========================

    @dp.message(F.text == "➕ Выдать профит")
    async def profit_start(message: Message, state: FSMContext):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer("Введите ID воркера:")
        await state.set_state(ProfitIssue.worker_id)

    @dp.message(ProfitIssue.worker_id)
    async def profit_worker_id(message: Message, state: FSMContext):
        try:
            worker_id = int(message.text)
            user = get_user(worker_id)
            if not user:
                await message.answer("❌ Пользователь не найден")
                await state.clear()
                return
            await state.update_data(worker_id=worker_id)
            await message.answer("Введите сумму профита:")
            await state.set_state(ProfitIssue.amount)
        except ValueError:
            await message.answer("❌ Введите числовой ID")

    @dp.message(ProfitIssue.amount)
    async def profit_amount(message: Message, state: FSMContext):
        try:
            amount = float(message.text)
            await state.update_data(amount=amount)
            await message.answer("Введите направление:")
            await state.set_state(ProfitIssue.direction)
        except ValueError:
            await message.answer("❌ Введите число")

    @dp.message(ProfitIssue.direction)
    async def profit_direction(message: Message, state: FSMContext):
        await state.update_data(direction=message.text)
        await message.answer("Введите процент воркера (например 70):")
        await state.set_state(ProfitIssue.percent)

    @dp.message(ProfitIssue.percent)
    async def profit_percent(message: Message, state: FSMContext):
        try:
            percent = float(message.text)
            data = await state.get_data()
            result = add_profit_record(data['worker_id'], message.from_user.id, data['amount'], percent,
                                       data['direction'])
            worker = get_user(data['worker_id'])

            await message.answer(
                f"✅ Профит выдан!\n\n"
                f"👤 Воркер: @{worker.get('username') or data['worker_id']}\n"
                f"💰 Сумма: {data['amount']} ₽\n"
                f"💵 Воркеру: {result['worker_amount']:.2f} ₽",
                reply_markup=admin_dashboard_inline_kb()
            )

            try:
                await bot.send_message(
                    data['worker_id'],
                    f"🎉 <b>Новый профит!</b>\n"
                    f"💰 Сумма: {data['amount']} ₽\n"
                    f"💵 Доход: {result['worker_amount']:.2f} ₽\n"
                    f"🔥 Серия: {result['current_streak']} дней",
                    parse_mode="HTML"
                )
            except:
                pass
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
        finally:
            await state.clear()

    # ==========================
    # АДМИН: Остальные команды
    # ==========================

    @dp.message(F.text == "📨 Заявки")
    async def admin_apps_cmd(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, q1 FROM users WHERE status = 'pending'")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await message.answer("📨 Нет заявок.")
            return

        for row in rows:
            uid, uname, q1 = row
            who = f"@{uname}" if uname else str(uid)
            ans = (q1 or "").strip()
            preview = (ans[:50] + "...") if ans else "—"
            text = f"📄 Заявка от {who}\nОтвет: {preview}"
            await message.answer(text, reply_markup=build_application_inline_kb(uid))

    @dp.message(F.text == "📊 Общая статистика")
    async def admin_stats_cmd(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        stats = get_global_stats()
        kassa = get_kassa_stats()
        text = (
            f"📊 <b>Статистика</b>\n\n"
            f"Всего: {stats['total_users']} | Одобрено: {stats['total_approved']}\n"
            f"💰 День: {kassa['day']:,.0f} ₽ | Неделя: {kassa['week']:,.0f} ₽"
        )
        await message.answer(text)

    @dp.message(F.text == "👥 Стата воркера")
    async def worker_stats_start(message: Message, state: FSMContext):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer("Введите ID воркера:")
        await state.set_state(WorkerStatsFSM.waiting_user_id)

    @dp.message(WorkerStatsFSM.waiting_user_id)
    async def worker_stats_process(message: Message, state: FSMContext):
        try:
            user_id = int(message.text)
            await send_profile(bot, message.chat.id, user_id)
        except ValueError:
            await message.answer("❌ Введите число")
        await state.clear()

    @dp.message(F.text == "🎭 Роли пользователей")
    async def roles_start(message: Message, state: FSMContext):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer("Введите ID пользователя:")
        await state.set_state(RoleChangeFSM.waiting_user_id)

    @dp.message(RoleChangeFSM.waiting_user_id)
    async def roles_process(message: Message, state: FSMContext):
        try:
            user_id = int(message.text)
            user = get_user(user_id)
            if not user:
                await message.answer("❌ Не найден")
                await state.clear()
                return
            await state.update_data(target_user_id=user_id)
            await message.answer(f"Пользователь: {user.get('username') or user_id}", reply_markup=roles_inline_kb())
        except ValueError:
            await message.answer("❌ Введите число")
            await state.clear()

    @dp.callback_query(F.data.startswith("role:"))
    async def role_callback(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            return
        role = callback.data.split(":")[1]
        data = await state.get_data()
        target_id = data.get('target_user_id')
        if target_id:
            set_user_role(target_id, role)
            await callback.message.edit_text(f"✅ Роль изменена на: {role}")
        await state.clear()
        await callback.answer()

    @dp.message(F.text == "🔗 Настроить ссылки комьюнити")
    async def links_start(message: Message):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer("Выберите что настроить:", reply_markup=community_settings_kb())

    @dp.callback_query(F.data.startswith("setlink:"))
    async def setlink_callback(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            return
        link_type = callback.data.split(":")[1]
        await state.update_data(link_type=link_type)
        await callback.message.edit_text(f"Введите ссылку для {link_type}:")
        await state.set_state(AdminLinks.waiting_url)
        await callback.answer()

    @dp.message(AdminLinks.waiting_url)
    async def setlink_process(message: Message, state: FSMContext):
        data = await state.get_data()
        set_setting(f'{data.get("link_type")}_link', message.text)
        await message.answer("✅ Сохранено")
        await state.clear()

    @dp.message(F.text == "💳 Реквизиты Прамик")
    async def requisites_start(message: Message, state: FSMContext):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer("Введите реквизиты:")
        await state.set_state(AdminRequisites.waiting_text)

    @dp.message(AdminRequisites.waiting_text)
    async def requisites_process(message: Message, state: FSMContext):
        set_setting('pramik_requisites', message.text)
        await message.answer("✅ Сохранены")
        await state.clear()

    @dp.message(F.text == "📢 Рассылка")
    async def broadcast_start(message: Message, state: FSMContext):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer("Введите текст рассылки:")
        await state.set_state(BroadcastFSM.waiting_message)

    @dp.message(BroadcastFSM.waiting_message)
    async def broadcast_confirm(message: Message, state: FSMContext):
        await state.update_data(message_text=message.text)
        await message.answer(
            "Подтвердите:\n" + message.text[:200] + "...",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast:confirm")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")]
            ])
        )
        await state.set_state(BroadcastFSM.waiting_confirm)

    @dp.callback_query(BroadcastFSM.waiting_confirm, F.data == "broadcast:confirm")
    async def broadcast_send(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            return
        data = await state.get_data()
        text = data.get('message_text')
        users = get_approved_user_ids()
        sent = 0
        for uid in users:
            try:
                await bot.send_message(uid, text, parse_mode="HTML")
                sent += 1
                await asyncio.sleep(0.05)
            except:
                pass
        await callback.message.edit_text(f"✅ Отправлено: {sent}")
        await state.clear()

    @dp.callback_query(BroadcastFSM.waiting_confirm, F.data == "broadcast:cancel")
    async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_text("❌ Отменено")
        await state.clear()

    @dp.message(F.text == "⬅️ Назад")
    async def back_cmd(message: Message):
        user_id = message.from_user.id
        user = get_user(user_id)

        # Возвращаем пользователю основное меню (ReplyKeyboard)
        kb = main_menu_kb(is_admin=(user_id in ADMIN_IDS))

        if user and user.get("status") == "approved":
            # Обновляем профиль (инлайн‑панель) и возвращаем меню кнопок
            await send_profile(bot, message.chat.id, user_id)
            await message.answer("🏠 Главное меню", reply_markup=kb)
        else:
            await message.answer("🏠 Главное меню", reply_markup=kb)

    @dp.message(F.text == "👤 Профиль")
    async def profile_handler(message: Message):
        await send_profile(bot, message.chat.id, message.from_user.id)

    
    # ==========================
    # КОМАНДЫ (для чата и лички)
    # ==========================

    HELP_TEXT = (
        "🆘 <b>Помощь</b>\n\n"
        "Доступные команды:\n"
        "/start — открыть ворк-панель\n"
        "/me — моя статистика и место в топе\n"
        "/kurator — список наставников\n"
        "/top — топ воркеров по профитам\n"
        "/top_week — топ за последние 7 дней\n"
        "/top_month — топ за текущий месяц\n"
        "/card — реквизиты (Прямик)\n"
        "/kassa — общая касса проекта\n"
        "/goal — цель по профитам (пример: /goal 10)\n"
        "/streak — серия дней с профитом\n"
        "/help — список команд"
    )

    def _require_approved(user_id: int) -> bool:
        u = get_user(user_id)
        return bool(u and (u.get("status") == "approved"))

    def _month_start_ts(tz: ZoneInfo) -> int:
        now = datetime.now(tz)
        ms = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(ms.timestamp())

    def _top_since(ts: int | None, limit: int = 20) -> list[tuple[int, float]]:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if ts is None:
            cur.execute(
                "SELECT user_id, COALESCE(SUM(total_amount),0) as s "
                "FROM profits GROUP BY user_id ORDER BY s DESC LIMIT ?",
                (limit,),
            )
        else:
            cur.execute(
                "SELECT user_id, COALESCE(SUM(total_amount),0) as s "
                "FROM profits WHERE created_at >= ? GROUP BY user_id ORDER BY s DESC LIMIT ?",
                (ts, limit),
            )
        rows = cur.fetchall()
        conn.close()
        return [(int(r[0]), float(r[1] or 0)) for r in rows]

    async def _send_top(message: Message, title: str, rows: list[tuple[int, float]]):
        if not rows:
            await message.answer(f"🏁 <b>{title}</b>\n\nПока нет данных.", parse_mode="HTML")
            return
        lines = [f"🏁 <b>{title}</b>\n"]
        for i, (uid, amount) in enumerate(rows, start=1):
            u = get_user(uid)
            name = (u.get("username") if u else None) or f"ID {uid}"
            tag = f"@{name}" if name and not str(name).startswith("ID ") else name
            lines.append(f"{i}. {tag} — <b>{amount:,.0f}</b> ₽")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(HELP_TEXT, parse_mode="HTML")

    @dp.message(Command("me"))
    async def cmd_me(message: Message):
        if not _require_approved(message.from_user.id):
            await message.answer("Профиль будет доступен после одобрения вашей заявки.")
            return
        await send_profile(bot, message.chat.id, message.from_user.id)

    @dp.message(Command("kurator"))
    async def cmd_kurator(message: Message):
        mentors = get_all_mentors()
        if not mentors:
            text = "🧑‍🏫 <b>Наставники</b>\n\nПока нет назначенных."
        else:
            lines = ["🧑‍🏫 <b>Наши наставники</b>\n"]
            for m_ in mentors:
                name = m_.get("username") or f"ID {m_['user_id']}"
                lines.append(f"• @{name}" if m_.get("username") else f"• {name}")
            text = "\n".join(lines)
        await message.answer(text, parse_mode="HTML")

    @dp.message(Command("top"))
    async def cmd_top(message: Message):
        await _send_top(message, "ТОП воркеров (всё время)", _top_since(None, limit=20))

    @dp.message(Command("top_week"))
    async def cmd_top_week(message: Message):
        ts = int(time.time()) - 7 * 24 * 3600
        await _send_top(message, "ТОП воркеров (7 дней)", _top_since(ts, limit=20))

    @dp.message(Command("top_month"))
    async def cmd_top_month(message: Message):
        tz = ZoneInfo(TIMEZONE) if TIMEZONE else ZoneInfo("UTC")
        ts = _month_start_ts(tz)
        await _send_top(message, "ТОП воркеров (текущий месяц)", _top_since(ts, limit=20))

    @dp.message(Command("card"))
    async def cmd_card(message: Message):
        req = get_setting("pramik_requisites", "Не настроены")
        await message.answer(f"📟 <b>Прямик</b>\n\n<pre>{req}</pre>", parse_mode="HTML")

    @dp.message(Command("kassa"))
    async def cmd_kassa(message: Message):
        k = get_kassa_stats()
        await message.answer(
            "💼 <b>Касса проекта</b>\n\n"
            f"📆 День: <b>{k['day']:,.0f}</b> ₽\n"
            f"🗓 Неделя: <b>{k['week']:,.0f}</b> ₽\n"
            f"🗓 Месяц: <b>{k['month']:,.0f}</b> ₽\n"
            f"💰 Всего: <b>{k['all']:,.0f}</b> ₽",
            parse_mode="HTML",
        )

    @dp.message(Command("goal"))
    async def cmd_goal(message: Message, state: FSMContext):
        if not _require_approved(message.from_user.id):
            await message.answer("Цель доступна после одобрения заявки.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2:
            try:
                goal = int(parts[1].strip())
                if 1 <= goal <= 10000:
                    set_user_goal(message.from_user.id, goal)
                    await message.answer(f"✅ Цель: {goal} профитов")
                else:
                    await message.answer("❌ Введите число от 1 до 10000")
            except ValueError:
                await message.answer("❌ Пример: /goal 10")
        else:
            await message.answer("❌ Пример: /goal 10")

    @dp.message(Command("streak"))
    async def cmd_streak(message: Message):
        if not _require_approved(message.from_user.id):
            await message.answer("Streak доступен после одобрения заявки.")
            return
        user = get_user(message.from_user.id)
        text = (
            f"🔥 <b>Ваша серия</b>\n\n"
            f"Текущая: <b>{user.get('current_streak', 0)} дней</b>\n"
            f"Рекорд: <b>{user.get('max_streak', 0)} дней</b>\n"
            f"Последний: {format_last_profit_date(user.get('last_profit_date'))}"
        )
        await message.answer(text, parse_mode="HTML")
# ==========================
    # ГЛАВНОЕ МЕНЮ (ReplyKeyboard)
    # ==========================

    @dp.message(F.text == "📊 NFT")
    async def menu_nft(message: Message):
        await send_menu_link(bot, message.chat.id, "nft", "📊 NFT")

    @dp.message(F.text == "📈 TRADE")
    async def menu_trade(message: Message):
        await send_menu_link(bot, message.chat.id, "trade", "📈 TRADE")

    @dp.message(F.text == "📣 ESCORT")
    async def menu_escort(message: Message):
        await send_menu_link(bot, message.chat.id, "escort", "📣 ESCORT")

    @dp.message(F.text == "₿ BTC Search")
    async def menu_btc(message: Message):
        await send_menu_link(bot, message.chat.id, "btc", "₿ BTC Search")

    @dp.message(F.text == "🧪 NARKO")
    async def menu_narko(message: Message):
        await send_menu_link(bot, message.chat.id, "narko", "🧪 NARKO")

    @dp.message(F.text == "🌐 Сайт Трейд")
    async def menu_site_trade(message: Message):
        await send_menu_link(bot, message.chat.id, "site_trade", "🌐 Сайт Трейд")

    @dp.message(F.text == "🌐 Сайт NFT")
    async def menu_site_nft(message: Message):
        await send_menu_link(bot, message.chat.id, "site_nft", "🌐 Сайт NFT")

    @dp.callback_query(F.data == "ip:profile")
    async def profile_callback(callback: CallbackQuery):
        await send_profile(bot, callback.message.chat.id, callback.from_user.id, callback.message)
        await callback.answer()

    @dp.callback_query(F.data == "panel:top")
    async def panel_top_callback(callback: CallbackQuery):
        await send_profile(bot, callback.message.chat.id, callback.from_user.id, callback.message)
        await callback.answer()

    @dp.callback_query(F.data == "ip:admin")
    async def admin_callback(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав", show_alert=True)
            return

        # Убираем возможную ReplyKeyboard (она "залипает" в чате)
        try:
            await callback.message.answer(" ", reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass

        # Отправляем новое сообщение вместо редактирования
        stats = get_global_stats()
        kassa = get_kassa_stats()
        text = (
            f"🛠 <b>Админ-панель</b>\n\n"
            f"👥 Всего: {stats['total_users']} | Одобрено: {stats['total_approved']}\n"
            f"💰 День: {kassa['day']:,.0f} ₽ | Неделя: {kassa['week']:,.0f} ₽\n\n"
            f"Выберите действие:"
        )

        try:
            # Пытаемся отправить новое сообщение
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=admin_dashboard_inline_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка отправки: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "adm:back")
    async def admin_back_callback(callback: CallbackQuery):
        await send_profile(bot, callback.message.chat.id, callback.from_user.id, callback.message)
        await callback.answer()

    @dp.callback_query(F.data == "panel:goal")
    async def panel_goal(callback: CallbackQuery, state: FSMContext):
        # Отправляем новое сообщение вместо редактирования
        await bot.send_message(
            callback.message.chat.id,
            "🎯 <b>Установка цели</b>\n\nВведите количество профитов:"
        )
        await state.set_state(AdminSetGoalFSM.waiting_goal)
        await callback.answer()

    @dp.message(AdminSetGoalFSM.waiting_goal)
    async def set_goal_process(message: Message, state: FSMContext):
        try:
            goal = int(message.text)
            if 1 <= goal <= 10000:
                set_user_goal(message.from_user.id, goal)
                await message.answer(f"✅ Цель: {goal} профитов")
            else:
                raise ValueError()
        except ValueError:
            await message.answer("❌ Введите число от 1 до 10000")
        await state.clear()

    @dp.callback_query(F.data == "panel:streak")
    async def panel_streak(callback: CallbackQuery):
        user = get_user(callback.from_user.id)
        if not user:
            await callback.answer("Ошибка", show_alert=True)
            return

        text = (
            f"🔥 <b>Ваша серия</b>\n\n"
            f"Текущая: <b>{user.get('current_streak', 0)} дней</b>\n"
            f"Рекорд: <b>{user.get('max_streak', 0)} дней</b>\n"
            f"Последний: {format_last_profit_date(user.get('last_profit_date'))}"
        )

        # Отправляем новое сообщение
        try:
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "ip:community")
    async def community_callback(callback: CallbackQuery):
        chat = get_setting('chat_link', '#')
        payouts = get_setting('payouts_link', '#')
        manuals = get_setting('manuals_link', '#')
        info = get_setting('info_link', '#')

        text = (
            f"🧑‍💻 <b>Комьюнити</b>\n\n"
            f"🔗 <a href='{chat}'>Чат проекта</a>\n"
            f"💸 <a href='{payouts}'>Канал выплат</a>\n"
            f"📚 <a href='{manuals}'>Мануалы</a>\n"
            f"📢 <a href='{info}'>Инфо канал</a>"
        )

        # Отправляем новое сообщение вместо редактирования
        try:
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "ip:pramik")
    async def pramik_callback(callback: CallbackQuery):
        req = get_setting('pramik_requisites', 'Не настроены')
        text = f"📟 <b>Прямик</b>\n\n<pre>{req}</pre>\n\nОтправьте скрин после перевода."

        try:
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "ip:mentors")
    async def mentors_callback(callback: CallbackQuery):
        mentors = get_all_mentors()
        if not mentors:
            text = "🧑‍🏫 <b>Наставники</b>\n\nПока нет назначенных."
        else:
            lines = ["🧑‍🏫 <b>Наши наставники</b>\n"]
            for m in mentors:
                name = m.get('username') or f"ID {m['user_id']}"
                lines.append(f"• @{name}")
            text = "\n".join(lines)

        try:
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "ip:settings")
    async def settings_callback(callback: CallbackQuery):
        try:
            await bot.send_message(
                callback.message.chat.id,
                "⚙️ <b>Настройки</b>\n\n"
                "• 🎯 Цель - установить цель по профитам\n"
                "• 🔥 Streak - просмотреть серию",
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "ip:referral")
    async def referral_callback(callback: CallbackQuery):
        user_id = callback.from_user.id
        me = await bot.get_me()
        ref_link = f"https://t.me/{me.username}?start=ref{user_id}"

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
        count = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(referrer_amount), 0) FROM profits WHERE referrer_id = ?", (user_id,))
        earned = cur.fetchone()[0] or 0
        conn.close()

        text = (
            f"🧳 <b>Реферальная программа</b>\n\n"
            f"🔗 <code>{ref_link}</code>\n\n"
            f"👥 Приглашено: <b>{count}</b>\n"
            f"💰 Заработано: <b>{earned:,.2f}</b> ₽\n\n"
            f"Вы получаете 5% с каждого профита реферала!"
        )

        try:
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "panel:myworkers")
    async def myworkers_callback(callback: CallbackQuery):
        user = get_user(callback.from_user.id)
        if not user or user.get('role') != 'mentor':
            await callback.answer("Только для наставников", show_alert=True)
            return

        workers = get_workers_for_mentor(callback.from_user.id)
        if not workers:
            text = "👥 <b>Ваши воркеры</b>\n\nУ вас пока нет закрепленных воркеров."
        else:
            lines = [f"👥 <b>Ваши воркеры ({len(workers)})</b>\n"]
            for w in workers:
                name = w.get('username') or f"ID {w['user_id']}"
                lines.append(f"• @{name} | {w['profits_count']} профитов")
            text = "\n".join(lines)

        try:
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    @dp.callback_query(F.data == "panel:inactive")
    async def inactive_callback(callback: CallbackQuery):
        user = get_user(callback.from_user.id)
        if not user or user.get('role') != 'mentor':
            await callback.answer("Только для наставников", show_alert=True)
            return

        inactive = get_inactive_workers_for_mentor(callback.from_user.id, 3)
        if not inactive:
            text = "⏳ <b>Неактивные</b>\n\nВсе ваши воркеры активны! 🔥"
        else:
            lines = [f"⏳ <b>Неактивные > 3 дней ({len(inactive)})</b>\n"]
            for w in inactive:
                name = w.get('username') or f"ID {w['user_id']}"
                days = w.get('_inactive_days') or "?"
                lines.append(f"• @{name} | {days} дн.")
            text = "\n".join(lines)

        try:
            await bot.send_message(
                callback.message.chat.id,
                text,
                reply_markup=back_to_profile_kb(),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка: {e}")

        await callback.answer()

    # ==========================
    # ОДОБРЕНИЕ/ОТКЛОНЕНИЕ
    # ==========================

    @dp.callback_query(F.data.startswith("approve:"))
    async def approve_callback(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав", show_alert=True)
            return
        try:
            user_id = int(callback.data.split(":")[1])
            approve_user(user_id)
            await bot.send_message(user_id, "🎉 Вы одобрены! Добро пожаловать.", reply_markup=main_menu_kb())
            await callback.message.edit_text("✅ Пользователь одобрен")
        except Exception as e:
            await callback.answer(f"Ошибка: {e}", show_alert=True)

    @dp.callback_query(F.data.startswith("reject:"))
    async def reject_callback(callback: CallbackQuery):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав", show_alert=True)
            return
        try:
            user_id = int(callback.data.split(":")[1])
            reject_user(user_id)
            await bot.send_message(user_id, "❌ К сожалению, заявка отклонена.")
            await callback.message.edit_text("❌ Заявка отклонена")
        except Exception as e:
            await callback.answer(f"Ошибка: {e}", show_alert=True)

    @dp.callback_query(F.data.startswith("adm:"))
    async def admin_dashboard_callback(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав")
            return

        action = callback.data.replace("adm:", "")

        if action == "panel":
            stats = get_users_stats()
            kassa = get_kassa_stats()
            text = (
                f"🛠 <b>Админ-панель</b>\n\n"
                f"👥 Всего: {stats['total_users']} | Одобрено: {stats['total_approved']}\n"
                f"💰 День: {kassa['day']:,.0f} ₽ | Неделя: {kassa['week']:,.0f} ₽\n\n"
                f"Выберите действие:"
            )
            try:
                await callback.message.edit_text(text, reply_markup=admin_dashboard_inline_kb(), parse_mode="HTML")
            except Exception:
                await bot.send_message(callback.message.chat.id, text, reply_markup=admin_dashboard_inline_kb(), parse_mode="HTML")
            await callback.answer()
            return

        if action == "profit":
            await bot.send_message(callback.message.chat.id, "Введите ID воркера:")
            await state.set_state(ProfitIssue.worker_id)
        elif action == "user":
            await bot.send_message(callback.message.chat.id, "Введите ID для поиска:")
            await state.set_state(WorkerStatsFSM.waiting_user_id)
        elif action == "stats":
            stats = get_global_stats()
            kassa = get_kassa_stats()
            text = (
                f"📊 <b>Статистика</b>\n\n"
                f"Всего: {stats['total_users']} | Одобрено: {stats['total_approved']}\n"
                f"💰 День: {kassa['day']:,.0f} ₽ | Неделя: {kassa['week']:,.0f} ₽"
            )
            await bot.send_message(callback.message.chat.id, text, parse_mode="HTML",
                                   reply_markup=admin_dashboard_inline_kb())
        elif action == "logs":
            logs = get_admin_logs(5)
            text = "🧾 <b>Последние действия:</b>\n\n" + "\n".join(
                [f"{format_ts(l['created_at'])}: {l['action']}" for l in logs])
            await bot.send_message(callback.message.chat.id, text, parse_mode="HTML",
                                   reply_markup=admin_dashboard_inline_kb())
        elif action == "apps":
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users WHERE status = 'pending'")
            count = cur.fetchone()[0]
            conn.close()
            await bot.send_message(callback.message.chat.id, f"📨 Заявок: {count}\n\nИспользуйте кнопку '📨 Заявки'",
                                   reply_markup=admin_dashboard_inline_kb())
        elif action == "settings":
            # Открываем настройки (полностью inline)
            try:
                await callback.message.edit_text("⚙️ <b>Настройки</b>\n\nВыберите раздел:", reply_markup=admin_settings_inline_kb(), parse_mode="HTML")
            except Exception:
                await bot.send_message(callback.message.chat.id, "⚙️ <b>Настройки</b>\n\nВыберите раздел:", reply_markup=admin_settings_inline_kb(), parse_mode="HTML")

        await callback.answer()

    # ==========================
    # АДМИН: настройка кнопок главного меню
    # ==========================


    # ==========================
    # АДМИН: НАСТРОЙКИ (INLINE)
    # ==========================

    @dp.callback_query(F.data == "admset:req")
    async def admin_settings_requisites(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав", show_alert=True)
            return
        # Переходим в сценарий реквизитов
        await callback.message.answer("💳 <b>Реквизиты Прамик</b>\n\nОтправьте новые реквизиты одним сообщением.",
                                      parse_mode="HTML",
                                      reply_markup=ReplyKeyboardRemove())
        await state.set_state(RequisitesFSM.waiting_text)
        await callback.answer()

    @dp.callback_query(F.data == "admset:menu")
    async def admin_settings_menu_buttons(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав", show_alert=True)
            return
        await callback.message.answer(
            "🧱 <b>Кнопки меню</b>\n\nВыбери кнопку, которую нужно настроить (URL и/или текст).",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        await callback.message.answer("Выбор кнопки:", reply_markup=menu_buttons_settings_kb())
        await state.set_state(MenuButtonsPickFSM.waiting_pick)
        await callback.answer()

    @dp.callback_query(F.data == "admset:mail")
    async def admin_settings_mailing(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав", show_alert=True)
            return
        await callback.message.answer("📢 <b>Рассылка</b>\n\nОтправьте текст рассылки (или /cancel).",
                                      parse_mode="HTML",
                                      reply_markup=ReplyKeyboardRemove())
        await state.set_state(MailingFSM.waiting_text)
        await callback.answer()


    @dp.message(F.text == "🧱 Кнопки меню")
    async def menu_buttons_admin_start(message: Message, state: FSMContext):
        if message.from_user.id not in ADMIN_IDS:
            return
        await message.answer(
            "🧱 <b>Кнопки меню</b>\n\n"
            "Выбери кнопку, которую нужно настроить (URL и/или текст).",
            reply_markup=menu_buttons_settings_kb(),
        )
        await state.set_state(MenuButtonsPickFSM.waiting_pick)

    @dp.callback_query(MenuButtonsPickFSM.waiting_pick, F.data.startswith("menuset:"))
    async def menuset_pick(callback: CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав", show_alert=True)
            return

        key = callback.data.split(":", 1)[1]
        if key == "back":
            await state.clear()
            await callback.message.edit_text("↩️ Назад", reply_markup=None)
            await callback.answer()
            return

        await state.update_data(menu_key=key)
        current_url = (get_setting(f"menu_{key}_url", "") or "").strip()
        current_text = (get_setting(f"menu_{key}_text", "") or "").strip()

        await callback.message.edit_text(
            "Отправь одним сообщением, что сохранить:\n"
            "• URL (https://...)\n"
            "• или просто текст (если нужно без ссылки)\n\n"
            "Если хочешь сохранить и URL и текст — отправь в формате:\n"
            "<code>URL | ТЕКСТ</code>\n\n"
            f"Текущее:\nURL: <code>{current_url or '—'}</code>\n"
            f"Текст: <code>{(current_text[:200] + '…') if len(current_text) > 200 else (current_text or '—')}</code>",
            parse_mode="HTML",
        )
        await state.set_state(MenuButtonsFSM.waiting_text)
        await callback.answer()

    @dp.message(MenuButtonsFSM.waiting_text)
    async def menuset_save(message: Message, state: FSMContext):
        if message.from_user.id not in ADMIN_IDS:
            return
        data = await state.get_data()
        key = data.get("menu_key")
        if not key:
            await state.clear()
            return

        raw = (message.text or "").strip()
        url, text = "", ""
        if "|" in raw:
            left, right = raw.split("|", 1)
            url = left.strip()
            text = right.strip()
        elif raw.startswith("http://") or raw.startswith("https://"):
            url = raw
        else:
            text = raw

        set_setting(f"menu_{key}_url", url)
        set_setting(f"menu_{key}_text", text)

        await message.answer(
            "✅ Сохранено.\n\n"
            f"URL: <code>{url or '—'}</code>\n"
            f"Текст: <code>{(text[:200] + '…') if len(text) > 200 else (text or '—')}</code>",
            parse_mode="HTML",
        )
        await state.clear()

    print("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
