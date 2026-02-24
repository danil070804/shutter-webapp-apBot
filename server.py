import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# IMPORTANT:
# - BOT_TOKEN must be set in env (config.py требует это)
# - DB_PATH должен указывать на один и тот же sqlite файл для бота и веба
import bot as bot_module  # bot.py (НЕ запускается при импорте)


from db import engine, users, profits

def _get_user(user_id: int) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select

    with engine.connect() as conn:
        row = conn.execute(
            select(
                users.c.user_id, users.c.username, users.c.status, users.c.profits_count, users.c.profits_sum,
                users.c.goal_profits, users.c.current_streak, users.c.max_streak, users.c.last_profit_date,
                users.c.joined_at, users.c.role, users.c.mentor_id, users.c.referrer_id
            ).where(users.c.user_id == user_id)
        ).fetchone()

    return dict(row._mapping) if row else None


def _get_recent_profits(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    from sqlalchemy import select, desc

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                profits.c.id, profits.c.total_amount, profits.c.worker_percent, profits.c.worker_amount,
                profits.c.direction, profits.c.mentor_id, profits.c.mentor_amount,
                profits.c.referrer_id, profits.c.referrer_amount, profits.c.created_at, profits.c.admin_id
            )
            .where(profits.c.user_id == user_id)
            .order_by(desc(profits.c.created_at))
            .limit(int(limit))
        ).fetchall()

    return [dict(r._mapping) for r in rows]


def _get_top(limit: int = 20) -> List[Dict[str, Any]]:
    from sqlalchemy import select, desc

    with engine.connect() as conn:
        rows = conn.execute(
            select(users.c.user_id, users.c.username, users.c.profits_count, users.c.profits_sum, users.c.role)
            .where(users.c.status == "approved")
            .order_by(desc(users.c.profits_count), desc(users.c.profits_sum))
            .limit(int(limit))
        ).fetchall()

    return [dict(r._mapping) for r in rows]


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
