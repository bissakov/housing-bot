"""Small, explicit Kazakh/Russian message catalogue."""

SUPPORTED_LANGUAGES = {"kk", "ru"}
DEFAULT_LANGUAGE = "kk"


TEXTS = {
    "choose_language": {"kk": "👋 Қош келдіңіз! Добро пожаловать!\n\n🌐 Тілді таңдаңыз / Выберите язык:", "ru": "🌐 Выберите язык:"},
    "language_changed": {"kk": "✅ Тіл қазақ тіліне ауыстырылды.", "ru": "✅ Язык изменён на русский."},
    "welcome_role": {
        "kk": "👋 <b>Домовой ботына қош келдіңіз</b>\n\nҚалай тіркелгіңіз келеді?",
        "ru": "👋 <b>Добро пожаловать в Домовой</b>\n\nКем вы хотите зарегистрироваться?",
    },
    "welcome_name": {
        "kk": "👋 <b>Домовой ботына қош келдіңіз</b>\n\nМұнда үйдегі мәселелер туралы хабарлап, олардың шешілуін бақылай аласыз.\n\n1/2 қадам — аты-жөніңізді енгізіңіз:",
        "ru": "👋 <b>Добро пожаловать в Домовой</b>\n\nЗдесь можно сообщать о проблемах в доме и следить за их решением.\n\nШаг 1 из 2 — введите ваше ФИО:",
    },
    "enter_name": {"kk": "Аты-жөніңізді енгізіңіз:", "ru": "Введите ФИО:"},
    "step_name": {"kk": "1/2 қадам — аты-жөніңізді енгізіңіз:", "ru": "Шаг 1 из 2 — введите ваше ФИО:"},
    "invalid_name": {"kk": "Дұрыс аты-жөнді енгізіңіз (кемінде 3 таңба):", "ru": "Введите корректное ФИО (минимум 3 символа):"},
    "step_apartment": {"kk": "2/2 қадам — пәтер нөмірін енгізіңіз:", "ru": "Шаг 2 из 2 — введите номер квартиры:"},
    "step_worker_category": {"kk": "2/2 қадам — мамандығыңызды таңдаңыз:", "ru": "Шаг 2 из 2 — выберите вашу дисциплину:"},
    "waiting_worker": {"kk": "Орындаушы рөліне өтініміңіз диспетчердің растауын күтуде.", "ru": "Ваша заявка на роль исполнителя ожидает подтверждения диспетчера."},
    "waiting_resident": {"kk": "Тіркелуіңіз диспетчердің растауын күтуде. Күте тұрыңыз.", "ru": "Ваша регистрация ожидает подтверждения диспетчера. Пожалуйста, ожидайте."},
    "main_prompt": {"kk": "Төмендегі мәзірден әрекетті таңдаңыз.", "ru": "Выберите действие в меню ниже."},
    "role": {"kk": "Рөл", "ru": "Роль"},
    "role_resident": {"kk": "Тұрғын", "ru": "Житель"},
    "role_worker": {"kk": "Орындаушы", "ru": "Исполнитель"},
    "role_dispatcher": {"kk": "Диспетчер", "ru": "Диспетчер"},
    "role_administrator": {"kk": "Әкімші", "ru": "Администратор"},
    "pending_approval": {"kk": "растауды күтуде", "ru": "ожидают подтверждения"},
    "main_menu": {"kk": "Басты мәзір", "ru": "Главное меню"},
    "cancel": {"kk": "❌ Болдырмау", "ru": "❌ Отмена"},
    "cancelled": {"kk": "❌ Болдырылмады.", "ru": "❌ Отменено."},
    "unknown_category": {"kk": "Белгісіз санат", "ru": "Неизвестная категория"},
    "start_first": {"kk": "Алдымен /start пәрменін басыңыз", "ru": "Сначала /start"},
    "registration_error": {"kk": "Қате. /start пәрменінен қайта бастаңыз", "ru": "Ошибка, попробуйте /start заново"},
    "registration_done": {"kk": "Рақмет, {name}! Деректер қабылданды ({apartment}-пәтер).\nДиспетчердің растауын күтіңіз — сізге хабарлама келеді.", "ru": "Спасибо, {name}! Данные приняты (кв. {apartment}).\nОжидайте подтверждения диспетчера — вам придёт уведомление."},
    "finish_registration": {"kk": "Алдымен /start арқылы тіркелуді аяқтаңыз", "ru": "Сначала завершите регистрацию через /start"},
    "new_request": {"kk": "Жаңа өтінім", "ru": "Новая заявка"},
    "choose_request_category": {"kk": "1/2 қадам — санатты таңдаңыз:", "ru": "Шаг 1 из 2 — выберите категорию:"},
    "describe_problem": {"kk": "2/2 қадам — мәселені сипаттаңыз", "ru": "Шаг 2 из 2 — опишите проблему"},
    "category": {"kk": "Санат", "ru": "Категория"},
    "description_hint": {"kk": "Не болғанын және нақты қай жерде екенін жазыңыз. Мысалы: «Ас үйде раковинаның астындағы құбыр ағып жатыр».", "ru": "Укажите, что произошло и где именно. Например: «На кухне под мойкой течёт труба»."},
    "no_announcements": {"kk": "Әзірге хабарландыру жоқ.", "ru": "Пока нет объявлений."},
    "announcements": {"kk": "Хабарландырулар", "ru": "Объявления"},
    "announcement": {"kk": "Хабарландыру", "ru": "Объявление"},
    "not_found_announcement": {"kk": "Хабарландыру табылмады.", "ru": "Объявление не найдено."},
    "back": {"kk": "◀️ Артқа", "ru": "◀️ Назад"},
    "back_to_list": {"kk": "◀️ Тізімге", "ru": "◀️ К списку"},
}

CATEGORY_LABELS = {
    "kk": {"electrician": "⚡ Электрик", "plumber": "🔧 Сантехник", "security": "🛡️ Күзет"},
    "ru": {"electrician": "⚡ Электрик", "plumber": "🔧 Сантехник", "security": "🛡️ Охрана"},
}
STATUS_LABELS = {
    "kk": {"new": "🆕 Жаңа", "accepted": "🔧 Орындалуда", "closed": "✅ Жабық"},
    "ru": {"new": "🆕 Новая", "accepted": "🔧 В работе", "closed": "✅ Закрыта"},
}
URGENCY_LABELS = {
    "kk": {"low": "Төмен", "normal": "Қалыпты", "high": "Жоғары"},
    "ru": {"low": "Низкий", "normal": "Обычный", "high": "Высокий"},
}


def normalize_language(language: str | None) -> str:
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def t(key: str, language: str | None, **values) -> str:
    return TEXTS[key][normalize_language(language)].format(**values)


def category_label(category: str | None, language: str | None) -> str:
    return CATEGORY_LABELS[normalize_language(language)].get(category, category or "")


def status_label(status: str | None, language: str | None) -> str:
    return STATUS_LABELS[normalize_language(language)].get(status, status or "")


def urgency_label(urgency: str | None, language: str | None) -> str:
    return URGENCY_LABELS[normalize_language(language)].get(urgency, urgency or "")
