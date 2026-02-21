import json
import os
import sqlite3
import time
import urllib.parse
import hmac
import hashlib
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import TOKEN as BOT_TOKEN, DB_PATH


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


# ---------------- Telegram WebApp initData verify ----------------
def _parse_init_data(init_data: str) -> Dict[str, str]:
    # init_data is querystring: key=val&key=val
    pairs = {}
    for part in init_data.split("&"):
        if not part:
            continue
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        pairs[k] = v
    return pairs


def _verify_init_data(init_data: str, max_age_sec: int = 60 * 60 * 24) -> Dict[str, Any]:
    """Verify Telegram WebApp initData.
    Returns parsed 'user' dict if valid, else raises HTTPException.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")

    data = _parse_init_data(init_data)

    received_hash = data.get("hash")
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash")

    # build data_check_string from sorted key=value (exclude hash)
    check_items = []
    for k in sorted(data.keys()):
        if k == "hash":
            continue
        check_items.append(f"{k}={data[k]}")
    data_check_string = "\n".join(check_items)

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Bad initData signature")

    # auth_date age check
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if auth_date <= 0:
        raise HTTPException(status_code=401, detail="Bad auth_date")
    if int(time.time()) - auth_date > max_age_sec:
        raise HTTPException(status_code=401, detail="initData expired")

    # user is urlencoded JSON
    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="Missing user")
    try:
        user_json = json.loads(urllib.parse.unquote(user_raw))
    except Exception:
        raise HTTPException(status_code=401, detail="Bad user json")

    return user_json


app = FastAPI(title="Shutter Island Web API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/me")
async def api_me(request: Request):
    """Return profile for current Telegram user (WebApp)."""
    # initData can be sent via header or json body
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        init_data = body.get("initData")

    user = _verify_init_data(init_data)
    user_id = int(user["id"])
    db_user = _get_user(user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"telegram": {"id": user_id, "username": user.get("username")}, "profile": db_user}


@app.post("/api/me/profits")
async def api_me_profits(request: Request, limit: int = 20):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        init_data = body.get("initData")
    user = _verify_init_data(init_data)
    user_id = int(user["id"])
    db_user = _get_user(user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user_id, "profits": _get_recent_profits(user_id, limit=limit)}


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
if os.path.isdir("webapp"):
    app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")
