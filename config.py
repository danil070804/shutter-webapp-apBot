import os

def _env_str(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()

def _env_int(name: str, default: int | None = None) -> int | None:
    v = _env_str(name, None)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        raise RuntimeError(f"Env var {name} must be an integer, got: {v!r}")

def _env_int_list(name: str, default: tuple[int, ...] = ()) -> tuple[int, ...]:
    v = _env_str(name, None)
    if v is None:
        return default
    items = []
    for part in v.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            items.append(int(part))
        except ValueError:
            raise RuntimeError(f"Env var {name} must be comma-separated integers, got bad item: {part!r}")
    return tuple(items)

# ====== REQUIRED ======
TOKEN = _env_str("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")

# ====== OPTIONAL / RECOMMENDED ======
# список ID админов (через запятую)
ADMIN_IDS = _env_int_list("ADMIN_IDS", default=())

# ID админ-чата, куда будут падать заявки
ADMIN_CHAT_ID = _env_int("ADMIN_CHAT_ID", default=None)

# ID канала выплат
PAYOUTS_CHANNEL_ID = _env_int("PAYOUTS_CHANNEL_ID", default=None)

# ID обычного чата проекта (куда дублируются профиты)
PROJECT_CHAT_ID = _env_int("PROJECT_CHAT_ID", default=None)

# путь к картинке шапки профиля
PROFILE_IMAGE_PATH = _env_str("PROFILE_IMAGE_PATH", default="profile_header.jpg")

# путь к базе данных (sqlite)
DB_PATH = _env_str("DB_PATH", default="bot.db")

# Часовой пояс проекта (для streak/топов по времени)
TIMEZONE = _env_str("TIMEZONE", default="Europe/Kyiv")

# Ранги воркеров по количеству профитов
RANK_LEVELS = [
    {"min_profits": 0, "emoji": "🟢", "name": "New"},
    {"min_profits": 3, "emoji": "🔵", "name": "Worker"},
    {"min_profits": 10, "emoji": "🟣", "name": "Senior"},
    {"min_profits": 25, "emoji": "🟡", "name": "Elite"},
]

# Ежедневный дайджест для наставников
DIGEST_ENABLED = _env_str("DIGEST_ENABLED", "true").lower() in ("1", "true", "yes", "y", "on")
DIGEST_TIME = _env_str("DIGEST_TIME", "20:00")  # HH:MM
DIGEST_INACTIVE_DAYS = int(_env_str("DIGEST_INACTIVE_DAYS", "3"))
DIGEST_SEND_TO_ADMINS = _env_str("DIGEST_SEND_TO_ADMINS", "false").lower() in ("1", "true", "yes", "y", "on")

# Авто-цель: бот выставляет цель по профитам при одобрении заявки
AUTO_GOAL_ENABLED = _env_str("AUTO_GOAL_ENABLED", "true").lower() in ("1", "true", "yes", "y", "on")
DEFAULT_GOAL_PROFITS = int(_env_str("DEFAULT_GOAL_PROFITS", "10"))

# WEBAPP (Mini App) URL
WEBAPP_URL = _env_str("WEBAPP_URL", "")
