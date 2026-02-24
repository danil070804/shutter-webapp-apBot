import asyncio
import sqlite3
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from config import DB_PATH, TIMEZONE, ADMIN_IDS

class NotificationType(Enum):
    REFERRAL_PROFIT = "referral_profit"
    MENTOR_PROFIT = "mentor_profit"
    STREAK_WARNING = "streak_warning"
    STREAK_BROKEN = "streak_broken"
    RANK_UP = "rank_up"
    GOAL_ACHIEVED = "goal_achieved"
    DAILY_DIGEST = "daily_digest"
    INACTIVE_WARNING = "inactive_warning"
    MASS_MESSAGE = "mass_message"
    SYSTEM_ALERT = "system_alert"
    PROFIT_MILESTONE = "profit_milestone"

@dataclass
class NotificationTemplate:
    type: NotificationType
    title: str
    body: str
    emoji: str
    action_button: Optional[str] = None
    action_url: Optional[str] = None
    priority: int = 1

class SmartNotifier:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=ZoneInfo(TIMEZONE))
        self.templates = self._load_templates()
        self.user_activity_cache = {}

    def _load_templates(self) -> Dict[NotificationType, NotificationTemplate]:
        return {
            NotificationType.REFERRAL_PROFIT: NotificationTemplate(
                type=NotificationType.REFERRAL_PROFIT,
                title="Реферальный доход!",
                body="Твой реферал сделал профит на {amount}₽. Твой бонус: {bonus}₽",
                emoji="💰",
                action_button="Мои рефералы",
                priority=4
            ),
            NotificationType.RANK_UP: NotificationTemplate(
                type=NotificationType.RANK_UP,
                title="Новый ранг!",
                body="Поздравляем! Ты достиг ранга {new_rank}. Следующий уровень: {next_rank}",
                emoji="🎖",
                action_button="Профиль",
                priority=5
            ),
            NotificationType.STREAK_WARNING: NotificationTemplate(
                type=NotificationType.STREAK_WARNING,
                title="Серия горит!",
                body="У тебя серия {streak} дней! Сделай профит в течение {hours_left}ч, чтобы сохранить её.",
                emoji="⚠️",
                action_button="Сделать профит",
                priority=5
            ),
            NotificationType.GOAL_ACHIEVED: NotificationTemplate(
                type=NotificationType.GOAL_ACHIEVED,
                title="Цель достигнута!",
                body="Ты выполнил цель на {goal} профитов! Бонус начислен на баланс.",
                emoji="🎯",
                action_button="Получить награду",
                priority=4
            ),
            NotificationType.PROFIT_MILESTONE: NotificationTemplate(
                type=NotificationType.PROFIT_MILESTONE,
                title="Юбилейный профит!",
                body="Это твой {milestone}-й профит! Общая сумма: {total_sum}₽",
                emoji="🏆",
                priority=3
            )
        }

    async def start(self):
        # Только рабочие методы
        self.scheduler.add_job(self._check_streaks, CronTrigger(hour=20, minute=0))
        self.scheduler.add_job(self._realtime_online_check, 'interval', minutes=5)
        self.scheduler.start()
        print("SmartNotifier запущен")

    async def send_smart(self, user_id: int, notif_type: NotificationType, **kwargs):
        template = self.templates.get(notif_type)
        if not template:
            return

        if await self._is_user_sleeping(user_id):
            return

        text = f"{template.emoji} <b>{template.title}</b>\n\n{template.body.format(**kwargs)}"

        kb = None
        if template.action_button:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=template.action_button, callback_data=f"notif_action:{notif_type.value}")],
                [InlineKeyboardButton(text="Закрыть", callback_data="delete_message")]
            ])

        try:
            if template.priority >= 4:
                await self.bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")
            else:
                await self.bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML", disable_notification=True)
        except Exception as e:
            print(f"Ошибка отправки уведомления {user_id}: {e}")

    async def notify_profit_created(self, user_id: int, amount: float, total_amount: float,
                                   direction: str, streak_data: dict):
        milestones = [10, 25, 50, 100, 250, 500, 1000]
        profits_count = streak_data.get('profits_count', 0)

        if profits_count in milestones:
            await self.send_smart(user_id, NotificationType.PROFIT_MILESTONE,
                                milestone=profits_count, total_sum=total_amount)

        new_rank = self._check_rank_up(user_id, profits_count)
        if new_rank:
            await self.send_smart(user_id, NotificationType.RANK_UP,
                                new_rank=new_rank['name'], next_rank=new_rank.get('next', 'Max'))

        await self._notify_referrer(user_id, amount, total_amount)

    async def _notify_referrer(self, worker_id: int, amount: float, total_amount: float):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT referrer_id FROM users WHERE user_id = ?", (worker_id,))
        row = cur.fetchone()
        conn.close()

        if not row or not row[0]:
            return

        referrer_id = row[0]
        bonus = round(total_amount * 0.05, 2)

        caption = (
            f"💎 <b>Реферальный бриллиант!</b>\n\n"
            f"Твой партнер только что сделал крупный профит:\n"
            f"💵 Сумма: <code>{total_amount:,.0f}</code> ₽\n"
            f"🎁 Твой доход: <code>+{bonus:,.0f}</code> ₽ (5%)\n\n"
            f"📊 Статистика рефералов доступна в профиле"
        )

        try:
            await self.bot.send_message(referrer_id, caption, parse_mode="HTML")
        except:
            pass

    async def _check_streaks(self):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = datetime.now(ZoneInfo(TIMEZONE))
        yesterday = (now - timedelta(days=1)).date()

        cur.execute("""
            SELECT user_id, current_streak, username 
            FROM users 
            WHERE current_streak > 2 
            AND last_profit_date = ?
            AND status = 'approved'
        """, (yesterday.isoformat(),))

        users = cur.fetchall()
        conn.close()

        for user_id, streak, username in users:
            hours_left = 24 - now.hour
            await self.send_smart(user_id, NotificationType.STREAK_WARNING,
                                streak=streak, hours_left=hours_left)

    async def _realtime_online_check(self):
        if random.random() > 0.7:
            await self._trigger_flash_event()

    async def _trigger_flash_event(self):
        event_types = [
            ("⚡ FLASH BONUS", "Первые 3 профита в ближайший час получат +10%!"),
            ("🔥 HOT STREAK", "2 профита подряд = розыгрыш 1000$"),
        ]
        event = random.choice(event_types)

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id FROM users 
            WHERE last_profit_date >= date('now', '-3 days')
            AND status = 'approved'
        """)
        users = cur.fetchall()
        conn.close()

        for (uid,) in users[:50]:
            try:
                await self.bot.send_message(uid, f"{event[0]}\n\n{event[1]}\n\n⏳ 1 час!", parse_mode="HTML")
                await asyncio.sleep(0.1)
            except:
                continue

    async def _is_user_sleeping(self, user_id: int) -> bool:
        hour = datetime.now(ZoneInfo(TIMEZONE)).hour
        return 23 <= hour or hour <= 7

    def _check_rank_up(self, user_id: int, profits_count: int) -> Optional[dict]:
        ranks = [
            {"min": 0, "name": "New", "next": "Worker"},
            {"min": 10, "name": "Worker", "next": "Senior"},
            {"min": 25, "name": "Senior", "next": "Elite"},
            {"min": 50, "name": "Elite", "next": "Master"},
            {"min": 100, "name": "Master", "next": "Legend"}
        ]
        current = None
        for rank in ranks:
            if profits_count >= rank["min"]:
                current = rank
        return current

notifier = None

def init_notifier(bot: Bot):
    global notifier
    notifier = SmartNotifier(bot)
    return notifier
