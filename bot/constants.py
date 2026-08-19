"""Shared domain values used by the database, services, and Telegram UI."""

REQUEST_CATEGORIES = frozenset({"electrician", "plumber", "security"})
REQUEST_STATUSES = frozenset({"new", "accepted", "closed"})
USER_ROLES = frozenset({"resident", "worker", "dispatcher"})
URGENCY_LEVELS = frozenset({"low", "normal", "high"})

CATEGORY_LABELS = {
    "electrician": "🔌 Электрик",
    "plumber": "🚿 Сантехник",
    "security": "🛡️ Охрана",
}

STATUS_LABELS = {
    "new": "🆕 Новая",
    "accepted": "🔧 В работе",
    "closed": "✅ Закрыта",
}

URGENCY_LABELS = {
    "low": "🟢 Низкий",
    "normal": "🟡 Обычный",
    "high": "🔴 Высокий",
}
