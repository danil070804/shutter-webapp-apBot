FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Запуск бота и web одновременно
CMD ["bash", "-lc", "python bot.py & uvicorn server:app --host 0.0.0.0 --port $PORT"]
