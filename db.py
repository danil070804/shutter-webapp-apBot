import os
from typing import Optional

from sqlalchemy import (
    create_engine, MetaData, Table, Column,
    Integer, BigInteger, String, Text, Float
)

from config import DB_PATH

def _env_str(name: str) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    return v or None

# Railway обычно дает DATABASE_URL (иногда POSTGRES_URL)
DATABASE_URL = _env_str("DATABASE_URL") or _env_str("POSTGRES_URL")

def build_db_url() -> str:
    if DATABASE_URL:
        # SQLAlchemy умеет postgresql://... при наличии psycopg2
        return DATABASE_URL
    # локальная разработка / fallback
    return f"sqlite+pysqlite:///{DB_PATH}"

engine = create_engine(
    build_db_url(),
    pool_pre_ping=True,
    future=True,
)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("user_id", BigInteger, primary_key=True),
    Column("username", Text),
    Column("status", String(32), nullable=False, server_default="pending"),
    Column("q1", Text),
    Column("q2", Text),
    Column("q3", Text),
    Column("profits_count", Integer, nullable=False, server_default="0"),
    Column("profits_sum", Float, nullable=False, server_default="0"),
    Column("goal_profits", Integer, nullable=False, server_default="0"),
    Column("current_streak", Integer, nullable=False, server_default="0"),
    Column("max_streak", Integer, nullable=False, server_default="0"),
    Column("last_profit_date", Text),
    Column("joined_at", Integer),
    Column("role", String(32), nullable=False, server_default="worker"),
    Column("mentor_id", BigInteger),
    Column("referrer_id", BigInteger),
)

profits = Table(
    "profits",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, nullable=False),
    Column("admin_id", BigInteger, nullable=False),
    Column("total_amount", Float, nullable=False),
    Column("worker_percent", Float, nullable=False),
    Column("worker_amount", Float, nullable=False),
    Column("direction", Text),
    Column("mentor_id", BigInteger),
    Column("mentor_amount", Float, server_default="0"),
    Column("referrer_id", BigInteger),
    Column("referrer_amount", Float, nullable=False, server_default="0"),
    Column("created_at", Integer, nullable=False),
)

settings = Table(
    "settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text),
)

admin_logs = Table(
    "admin_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("admin_id", BigInteger, nullable=False),
    Column("action", Text, nullable=False),
    Column("target_user_id", BigInteger),
    Column("details", Text),
    Column("created_at", Integer, nullable=False),
)

def init_db() -> None:
    """Создает таблицы (sqlite или postgres) если их еще нет."""
    metadata.create_all(engine)
