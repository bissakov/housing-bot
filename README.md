# Telegram Bot - ЖКХ / ТСЖ

Spec: `spec.md`

Highlights include atomic request transitions, append-only request audit
history, typed callback payloads, optional LLM assistance, and Redis-backed
FSM storage in Docker deployments.

## Setup

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

pip install -r requirements.txt

cp .env.example .env
# edit .env: BOT_TOKEN, ADMIN_IDS
```

## Env

```
BOT_TOKEN=123:ABC
ADMIN_IDS=123456,789012   # telegram IDs of administrators
DATABASE_URL=sqlite+aiosqlite:///./bot.db   # or postgresql+asyncpg://user:pass@localhost/botdb
ESCALATION_MINUTES=20
DISPLAY_TIMEZONE=Asia/Almaty
```

Timestamps are stored and processed in UTC, then converted to
`DISPLAY_TIMEZONE` when shown to users. The default, `Asia/Almaty`, displays
Kazakhstan's current UTC+5 civil time. Use an IANA timezone name rather than a
numeric offset.

## Run

```bash
python -m bot.main
```

## Demo data for UI testing

Populate the configured database with 16 residents, 6 workers, announcements,
request history, and 75 realistic requests in different states:

```bash
python scripts/seed_demo.py
```

Choose a larger dataset or rebuild only the generated demo records:

```bash
python scripts/seed_demo.py --requests 150 --reset
```

When the bot is running with Docker Compose, seed its persisted database inside
the container instead:

```bash
docker compose exec bot python scripts/seed_demo.py --requests 75
```

The command is safe to rerun without `--reset`: it tops the demo dataset up to
the requested number instead of duplicating it. Demo users use Telegram IDs
starting at `8800000000`, so they are easy to distinguish from real users.

## Development personas

Set `DEV_MODE=true`, restart the bot, and send `/dev` to select a stable test
persona. The available personas cover resident owner and tenant, every worker
category, dispatcher, and chairman. They use stable demo names and inherit the
developer's selected interface language.

A persona is created only on first use and then reused. Switching personas does
not rewrite the developer's profile or the role attached to historical
requests. Notifications addressed to the selected persona are delivered to the
developer controlling it. **My profile** returns to the ordinary profile, and
the cleanup button clears the selected persona's requests and schedule without
deleting the persona itself.

`/reset` remains separate: it exits persona mode and resets the developer's
ordinary profile so the registration flow can be tested from the beginning.
Run `alembic upgrade head` after enabling this feature on an existing database.

## Docker

The Compose setup runs the bot as an unprivileged user and persists its SQLite
database in the named volume `bot-data`.

```bash
cp .env.example .env
# Set BOT_TOKEN and the other required values in .env

docker compose up -d --build
docker compose logs -f bot
```

The container applies `alembic upgrade head` before starting. For a direct
deployment, migrate explicitly before polling:

```bash
alembic upgrade head
python -m bot.main
```

Dispatchers can open a request and select **🕓 История** to review creation,
claim, assignment/reassignment, closure, and deletion events. Audit events are
retained when the request itself is deleted.

Stop the bot without deleting its database:

```bash
docker compose down
```

To also delete the SQLite database volume:

```bash
docker compose down -v
```

By default Compose sets
`DATABASE_URL=sqlite+aiosqlite:////data/bot.db`. To use an external PostgreSQL
database instead, set an async SQLAlchemy URL in `.env`, for example:

```dotenv
DATABASE_URL=postgresql+asyncpg://user:password@database-host:5432/botdb
```

No ports are exposed because the bot receives Telegram updates using long
polling.

SQLite is intended for local development and single-instance deployments.
Use PostgreSQL in production for stronger concurrent request handling.
Docker Compose also runs Redis and configures it for persistent FSM state, so
in-progress registration and request forms survive bot restarts. Without a
`REDIS_URL`, direct local runs continue to use in-memory FSM storage.

## Roles flow

- **Resident**: `/start` -> ФИО + квартира -> 📝 Создать заявку -> выбор категории -> описание -> уведомления
- **Worker**: диспетчер добавляет по Telegram ID + категория -> `▶️ На смену` -> 📋 Доступные заявки -> Принять (атомарный claim) -> 🔧 Мои заявки -> Завершить с результатом «выполнено» или «не выполнено»
- **Dispatcher**: 📋 Все заявки (пагинация) -> Назначить/Переназначить -> ➕ Добавить исполнителя -> 📢 Создать объявление (broadcast)
- **Chairman**: the existing internal `administrator` role shown under the
  client-facing name «Председатель». It inherits all dispatcher access, can
  approve Kazakhdomofon work, revoke participant access, and perform authorized
  destructive actions. `ADMIN_IDS` users are bootstrapped into this role.

## Additional service requests

- Cleaning and Kazakhdomofon reuse the normal worker claim/completion workflow.
- Kazakhdomofon offers fixed Face ID, camera, and magnet request templates.
  Face ID requires one photo and the chairman must approve the request before
  it enters the Kazakhdomofon queue.
- Cleaning requires a photo or video with a location in the caption. Requests
  outside Monday-Friday 08:00-13:00 and Saturday 08:00-12:00 are deferred to
  the next opening; Sunday requests are released Monday at 08:00. No public
  holiday calendar is applied.
- Plumber and electrician requests record either apartment or common-property
  work. Apartment work displays the configured bilingual paid-service notice.
- Telegram media remains hosted by Telegram; the database stores reusable file
  identifiers rather than downloading the files.

## Worker schedules and current time

- A dispatcher or administrator opens **🗓 Графики исполнителей** and adds
  recurring hours such as `1-5 09:00-18:00` (Monday through Friday).
- The same screen records one-off absences and additional shifts. Input uses
  `DISPLAY_TIMEZONE`; concrete exceptions are stored in UTC.
- A worker must both be scheduled and mark an actual shift start to receive or
  claim normal requests. Workers without configured hours retain the previous
  check-in-only behavior, allowing schedules to be introduced gradually.
- Urgent requests fall back first to scheduled workers who have not checked in,
  then to all approved specialists in the category.
- Every LLM call receives the current local ISO timestamp and timezone, so
  relative dates never depend on a stale model clock.

## Atomic claim

`UPDATE requests SET status='accepted' WHERE id=:id AND status='new'` - prevents race.

## Escalation

APScheduler every 1 min checks `status='new'` older than 20 min -> notify dispatchers.

## LLM duplicate checks and dynamic priority

- Before saving, the bot compares a draft with at most 12 active requests in
  the same category. Completed requests never block creation.
- On a possible match, the LLM asks the resident one distinguishing question.
  A duplicate is blocked only after that answer and only at confidence
  `LLM_DUPLICATE_CONFIDENCE_THRESHOLD` or higher (default `0.92`). The resident
  can always override a false match and create a new request.
- An unavailable LLM or an ambiguous result is fail-open: the request is
  created so a genuinely new incident is not lost.
- Worker queues are dynamically sorted by `high`, `normal`, then `low`, with
  escalated and older requests first within a priority. Completing a higher
  priority task therefore promotes the next task without another LLM call.
