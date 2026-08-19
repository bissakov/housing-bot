"""Populate the configured database with realistic data for manual bot testing.

Usage:
    python scripts/seed_demo.py
    python scripts/seed_demo.py --requests 120 --reset

The script is idempotent by default: it only adds enough requests to reach the
requested amount. ``--reset`` removes data created by this script first.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.database import async_session, init_db
from bot.models import Announcement, Request, RequestEvent, User

SEED_TG_START = 8_800_000_000

RESIDENTS = [
    ("Анна Петрова", "12"), ("Михаил Соколов", "34"),
    ("Елена Волкова", "56"), ("Иван Кузнецов", "7"),
    ("Ольга Морозова", "81"), ("Дмитрий Попов", "23"),
    ("Наталья Лебедева", "45"), ("Алексей Новиков", "68"),
    ("Мария Фёдорова", "19"), ("Сергей Козлов", "102"),
    ("Татьяна Орлова", "4"), ("Артём Макаров", "73"),
    ("Ирина Захарова", "28"), ("Павел Виноградов", "91"),
    ("Светлана Белова", "15"), ("Виктор Титов", "63"),
]

WORKERS = [
    ("Андрей Светлов", "electrician", True),
    ("Роман Вольтов", "electrician", False),
    ("Борис Трубников", "plumber", True),
    ("Максим Водянов", "plumber", True),
    ("Олег Дозоров", "security", True),
    ("Кирилл Стражев", "security", False),
]

DESCRIPTIONS = {
    "electrician": [
        "В коридоре на этаже мигает лампа и периодически гаснет.",
        "Не работает розетка на кухне, появился запах гари.",
        "В подъезде не горит свет между третьим и четвёртым этажом.",
        "Выбивает автомат при включении стиральной машины.",
        "Не работает домофон и кнопка открытия двери.",
        "Оголился провод возле электрощитка на лестничной площадке.",
        "Лифт не реагирует на кнопку вызова, табло погасло.",
        "После скачка напряжения не включается освещение в ванной.",
    ],
    "plumber": [
        "Под кухонной мойкой течёт труба, вода собирается на полу.",
        "Слабый напор горячей воды во всей квартире.",
        "В подвале слышен сильный шум воды и появилась лужа.",
        "Не прогревается батарея в спальне, стояк при этом горячий.",
        "Засорился слив в ванной, вода уходит очень медленно.",
        "Капает вентиль на общем стояке в санузле.",
        "Из полотенцесушителя подтекает вода в месте соединения.",
        "В подъезде на первом этаже мокрый потолок после дождя.",
    ],
    "security": [
        "В подъезде уже час находится посторонний человек.",
        "Входная дверь не закрывается, сломан магнитный замок.",
        "Во дворе ночью громко играет музыка из автомобиля.",
        "Камера у второго подъезда повёрнута в сторону стены.",
        "На лестничной площадке оставили подозрительную коробку.",
        "Кто-то расклеивает объявления и портит стены в лифте.",
        "На парковке чужой автомобиль перекрыл пожарный проезд.",
        "Неизвестные пытаются попасть в техническое помещение.",
    ],
}

ANNOUNCEMENTS = [
    "Завтра с 10:00 до 14:00 будет проводиться проверка системы отопления.",
    "В субботу в 11:00 состоится субботник во дворе. Инвентарь выдадим у первого подъезда.",
    "С 20 по 22 число возможны кратковременные перебои горячего водоснабжения.",
    "Просим до конца недели убрать велосипеды и коляски из эвакуационных проходов.",
    "Общее собрание собственников пройдёт во вторник в 19:00 в холле первого подъезда.",
    "Во дворе установлены новые контейнеры для раздельного сбора отходов.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create demo data for the Telegram bot")
    parser.add_argument("--requests", type=int, default=75, help="target demo request count")
    parser.add_argument("--seed", type=int, default=20260324, help="random seed")
    parser.add_argument("--reset", action="store_true", help="delete prior demo data first")
    return parser.parse_args()


async def seed(request_count: int, random_seed: int, reset: bool) -> None:
    random.seed(random_seed)
    await init_db()

    async with async_session() as session:
        demo_users_result = await session.execute(
            select(User).where(User.telegram_id >= SEED_TG_START)
        )
        demo_users = list(demo_users_result.scalars())

        if reset and demo_users:
            demo_ids = [user.id for user in demo_users]
            demo_request_ids_result = await session.execute(
                select(Request.id).where(Request.resident_id.in_(demo_ids))
            )
            demo_request_ids = list(demo_request_ids_result.scalars())
            if demo_request_ids:
                await session.execute(
                    delete(RequestEvent).where(RequestEvent.request_id.in_(demo_request_ids))
                )
            await session.execute(delete(Request).where(Request.resident_id.in_(demo_ids)))
            await session.execute(delete(Announcement).where(Announcement.author_id.in_(demo_ids)))
            await session.execute(delete(User).where(User.id.in_(demo_ids)))
            await session.commit()
            demo_users = []

        users_by_name = {user.full_name: user for user in demo_users}
        next_tg = SEED_TG_START

        async def get_or_create_user(name: str, **kwargs) -> User:
            nonlocal next_tg
            if name in users_by_name:
                return users_by_name[name]
            used_ids = {user.telegram_id for user in users_by_name.values()}
            while next_tg in used_ids:
                next_tg += 1
            user = User(telegram_id=next_tg, full_name=name, **kwargs)
            next_tg += 1
            session.add(user)
            await session.flush()
            users_by_name[name] = user
            return user

        dispatcher = await get_or_create_user(
            "Демо Диспетчер", role="dispatcher", is_approved=True
        )
        residents = [
            await get_or_create_user(
                name, apartment=apartment, role="resident", is_approved=True
            )
            for name, apartment in RESIDENTS
        ]
        workers = [
            await get_or_create_user(
                name,
                role="worker",
                worker_category=category,
                is_approved=True,
                is_on_shift=on_shift,
            )
            for name, category, on_shift in WORKERS
        ]

        # Always use the generated dispatcher so reset can identify these rows.
        author = dispatcher

        resident_ids = [resident.id for resident in residents]
        existing_count_result = await session.execute(
            select(func.count()).select_from(Request).where(Request.resident_id.in_(resident_ids))
        )
        existing_count = existing_count_result.scalar() or 0
        to_create = max(0, request_count - existing_count)
        now = datetime.now(timezone.utc)
        category_workers = {
            category: [worker for worker in workers if worker.worker_category == category]
            for category in DESCRIPTIONS
        }

        created_requests: list[Request] = []
        for index in range(to_create):
            category = random.choice(list(DESCRIPTIONS))
            resident = random.choice(residents)
            age = timedelta(hours=random.randint(1, 24 * 55), minutes=random.randint(0, 59))
            created_at = now - age
            roll = random.random()
            status = "new" if roll < 0.38 else "accepted" if roll < 0.68 else "closed"
            worker = random.choice(category_workers[category]) if status != "new" else None
            accepted_at = None
            closed_at = None
            if status != "new":
                accepted_at = created_at + timedelta(minutes=random.randint(5, 360))
            if status == "closed":
                closed_at = accepted_at + timedelta(minutes=random.randint(20, 60 * 48))
                if closed_at > now:
                    closed_at = now - timedelta(minutes=random.randint(1, 120))

            urgency = random.choices(["low", "normal", "high"], weights=[18, 62, 20], k=1)[0]
            description = random.choice(DESCRIPTIONS[category])
            if index % 7 == 0:
                description += " Просьба связаться перед приходом."
            request = Request(
                resident_id=resident.id,
                category=category,
                description=description,
                raw_description=description,
                status=status,
                worker_id=worker.id if worker else None,
                created_at=created_at,
                accepted_at=accepted_at,
                closed_at=closed_at,
                urgency=urgency,
                is_escalated=status != "closed" and age > timedelta(hours=24),
            )
            session.add(request)
            created_requests.append(request)

        await session.flush()
        for request in created_requests:
            session.add(RequestEvent(
                request_id=request.id,
                actor_id=request.resident_id,
                action="created",
                details=f"category={request.category}; demo=true",
                created_at=request.created_at,
            ))
            if request.accepted_at:
                session.add(RequestEvent(
                    request_id=request.id,
                    actor_id=request.worker_id,
                    action="claimed",
                    details="demo=true",
                    created_at=request.accepted_at,
                ))
            if request.closed_at:
                session.add(RequestEvent(
                    request_id=request.id,
                    actor_id=request.worker_id,
                    action="closed",
                    details="demo=true",
                    created_at=request.closed_at,
                ))

        announcements_result = await session.execute(
            select(func.count()).select_from(Announcement).where(Announcement.author_id == author.id)
        )
        announcement_count = announcements_result.scalar() or 0
        for index, text in enumerate(ANNOUNCEMENTS[announcement_count:]):
            session.add(Announcement(
                author_id=author.id,
                text=text,
                created_at=now - timedelta(days=index * 3 + 1),
            ))

        await session.commit()

        status_result = await session.execute(
            select(Request.status, func.count(Request.id))
            .where(Request.resident_id.in_(resident_ids))
            .group_by(Request.status)
        )
        statuses = dict(status_result.all())
        print("Demo data is ready:")
        print(f"  residents: {len(residents)}")
        print(f"  workers: {len(workers)}")
        print(f"  requests: {sum(statuses.values())} ({statuses})")
        print(f"  announcements: {len(ANNOUNCEMENTS)}")
        print(f"  demo Telegram IDs start at: {SEED_TG_START}")


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(seed(args.requests, args.seed, args.reset))
