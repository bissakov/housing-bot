"""Shared domain values used by the database, services, and Telegram UI."""

REQUEST_CATEGORIES = frozenset({
    "electrician", "plumber", "security", "cleaning", "kazakhdomofon",
})
REQUEST_STATUSES = frozenset({"new", "accepted", "closed"})
USER_ROLES = frozenset({"resident", "worker", "dispatcher", "administrator"})
URGENCY_LEVELS = frozenset({"low", "normal", "high"})

CATEGORY_LABELS = {
    "electrician": "🔌 Электрик",
    "plumber": "🚿 Сантехник",
    "security": "🛡️ Охрана",
    "cleaning": "🧹 Клининг",
    "kazakhdomofon": "📹 Казахдомофон",
}

KAZAKHDOMOFON_TEMPLATES = {
    "face_id": "Добавление Face ID",
    "camera": "Добавление камер",
    "magnet": "Изготовление магнитов",
}

SERVICE_AREAS = frozenset({"apartment", "common"})
APPROVAL_STATUSES = frozenset({"pending", "approved", "rejected"})

STATUS_LABELS = {
    "new": "🆕 Новая",
    "accepted": "🔧 В работе",
    "closed": "✅ Завершена",
}

URGENCY_LABELS = {
    "low": "🟢 Низкий",
    "normal": "🟡 Обычный",
    "high": "🔴 Высокий",
}

# Demo rows seeded by scripts/seed_demo.py use synthetic Telegram ids starting here.
# They are not real chats, so notification fan-out must skip them.
SEED_TG_START = 8_800_000_000
