FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system bot && adduser --system --ingroup bot bot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=bot:bot bot ./bot
COPY --chown=bot:bot scripts ./scripts
COPY --chown=bot:bot migrations ./migrations
COPY --chown=bot:bot alembic.ini ./alembic.ini

RUN mkdir -p /data && chown bot:bot /data

USER bot

# Keep the default SQLite database outside the application directory so it can
# be persisted by a Docker volume.
ENV DATABASE_URL=sqlite+aiosqlite:////data/bot.db

VOLUME ["/data"]

CMD ["sh", "-c", "alembic upgrade head && exec python -m bot.main"]
