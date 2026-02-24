# Deploy to Railway (Worker bot)

## 1) Prepare repo
Upload this folder to GitHub (or deploy from local zip).

## 2) Create project on Railway
- Railway → New Project → Deploy from GitHub repo
- Choose this repo

## 3) Set start command
Railway usually detects Python automatically.
If it asks for a start command, set:
- `python bot.py`

(Procfile is included: `worker: python bot.py`)

## 4) Add environment variables
In Railway → Project → Variables, add:

- `BOT_TOKEN` (required)
- `ADMIN_IDS` (your TG user IDs, comma-separated)
- `ADMIN_CHAT_ID` (optional but recommended, where applications go)
- `PAYOUTS_CHANNEL_ID` (optional)
- `PROJECT_CHAT_ID` (optional)
- `WEBAPP_URL` (optional)

## 5) SQLite persistence (IMPORTANT)
This bot uses SQLite by default (`bot.db`).
Railway filesystem can be ephemeral on redeploys.

Options:
A) Quick test (no persistence):
- do nothing; DB may reset on redeploy.

B) Persist DB:
- Add a Railway **Volume** to the service
- Mount it to `/app/data`
- Set variable: `DB_PATH=/app/data/bot.db`

## 6) Deploy
Click Deploy. Then open logs to ensure:
- ✅ Бот запущен!

## Notes
- If you use channels/groups, make sure the bot is added and has permissions.
- For admin featur
