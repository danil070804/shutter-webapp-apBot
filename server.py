import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# IMPORTANT:
# - BOT_TOKEN must be set in env (config.py требует это)
# - DB_PATH должен указывать на один и тот же sqlite файл для бота и веба
from config import DB_PATH

import bot as bot_module  # bot.py (НЕ запускается при импорте)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                user_id, username, status, profits_count, profits_sum,
                goal_profits, current_streak, max_streak, last_profit_date,
                joined_at, role, mentor_id, referrer_id
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_recent_profits(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, total_amount, worker_percent, worker_amount, direction,
                   mentor_id, mentor_amount, referrer_id, referrer_amount, created_at, admin_id
            FROM profits
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _get_top(limit: int = 20) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, username, profits_count, profits_sum, role
            FROM users
            WHERE status = 'approved'
            ORDER BY profits_count DESC, profits_sum DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # гарантируем структуру БД
    bot_module.init_db()

    # запускаем polling бота в фоне
    bot_task = asyncio.create_task(bot_module.main())

    try:
        yield
    finally:
        # мягкая остановка
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Shutter Island Bot + Web", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # можно ужесточить до вашего домена
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- API ---------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/user/{user_id}")
def api_user(user_id: int):
    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/api/user/{user_id}/profits")
def api_user_profits(user_id: int, limit: int = 20):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user_id, "profits": _get_recent_profits(user_id, limit=limit)}


@app.get("/api/top")
def api_top(limit: int = 20):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    return {"items": _get_top(limit=limit)}


# --------- Static web ---------
# Главный Mini App
if os.path.isdir("webapp"):
    app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")

# Отдельная админка, если нужна
if os.path.isdir("admin_dashboard"):
    app.mount("/admin", StaticFiles(directory="admin_dashboard", html=True), name="admin")
