"""Explicit, strictly validated user-facing translations."""

# Add a locale here and to every catalogue below. Validation is deliberately
# strict: silently showing another language is worse than failing at startup.
LANGUAGE_NAMES = {
    "kk": "🇰🇿 Қазақша",
    "ru": "🇷🇺 Русский",
}
SUPPORTED_LANGUAGES = frozenset(LANGUAGE_NAMES)
DEFAULT_LANGUAGE = "kk"


TEXTS = {
    "command_start": {"kk": "Тіркелу", "ru": "Регистрация"},
    "command_language": {"kk": "Тілді өзгерту", "ru": "Изменить язык"},
    "command_dev": {
        "kk": "Тұрақты тест персонасын таңдау",
        "ru": "Выбрать постоянную тестовую персону",
    },
    "command_reset": {
        "kk": "Профильді жойып, тіркелуді қайта тексеру",
        "ru": "Удалить профиль и заново проверить регистрацию",
    },
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
    "waiting_tenant": {"kk": "Меншік иесінің растауын күтіңіз.", "ru": "Ожидайте подтверждения собственника."},
    "main_prompt": {"kk": "Төмендегі мәзірден әрекетті таңдаңыз.", "ru": "Выберите действие в меню ниже."},
    "role": {"kk": "Рөл", "ru": "Роль"},
    "role_resident": {"kk": "Тұрғын", "ru": "Житель"},
    "role_worker": {"kk": "Орындаушы", "ru": "Исполнитель"},
    "choose_resident_subrole": {"kk": "Тұрғын түрін таңдаңыз:", "ru": "Выберите тип жителя:"},
    "subrole_owner": {"kk": "🏠 Меншік иесі", "ru": "🏠 Собственник"},
    "subrole_tenant": {"kk": "🔑 Жалға алушы", "ru": "🔑 Арендатор"},
    "role_dispatcher": {"kk": "Диспетчер", "ru": "Диспетчер"},
    "role_administrator": {"kk": "Төраға", "ru": "Председатель"},
    "pending_approval": {"kk": "шешім күтуде", "ru": "требуют решения"},
    "main_menu": {"kk": "Басты мәзір", "ru": "Главное меню"},
    "dev_disabled": {"kk": "⛔ DEV_MODE өшірулі.", "ru": "⛔ DEV_MODE отключён."},
    "dev_start_first": {"kk": "Алдымен /start пәрменін жіберіңіз.", "ru": "Сначала отправьте /start."},
    "dev_own_profile": {"kk": "Өз профилім", "ru": "Мой профиль"},
    "dev_resident_owner": {"kk": "🏠 Тұрғын · меншік иесі", "ru": "🏠 Житель · собственник"},
    "dev_resident_tenant": {"kk": "🔑 Тұрғын · жалға алушы", "ru": "🔑 Житель · арендатор"},
    "dev_worker_electrician": {"kk": "⚡ Электрик", "ru": "⚡ Электрик"},
    "dev_worker_plumber": {"kk": "🔧 Сантехник", "ru": "🔧 Сантехник"},
    "dev_worker_security": {"kk": "🛡️ Күзет", "ru": "🛡️ Охрана"},
    "dev_worker_cleaning": {"kk": "🧹 Клининг", "ru": "🧹 Клининг"},
    "dev_worker_kazakhdomofon": {"kk": "📹 Қазақдомофон", "ru": "📹 Казахдомофон"},
    "dev_dispatcher": {"kk": "🎛️ Диспетчер", "ru": "🎛️ Диспетчер"},
    "dev_administrator": {"kk": "🛡️ Төраға", "ru": "🛡️ Председатель"},
    "dev_clear_persona": {
        "kk": "🧹 Осы персонаның деректерін тазарту",
        "ru": "🧹 Очистить данные этой персоны",
    },
    "dev_menu": {
        "kk": "🔧 <b>DEV персоналар</b>\nБелсенді: <b>{label}</b>\n\nТестілеу үшін персонаны таңдаңыз. Әр персона тұрақты және өзінің өтінімдер тарихын сақтайды.",
        "ru": "🔧 <b>DEV-персоны</b>\nАктивная: <b>{label}</b>\n\nВыберите персону для тестирования. Каждая персона постоянна и сохраняет свою историю заявок.",
    },
    "dev_unknown_persona": {"kk": "Белгісіз персона", "ru": "Неизвестная персона"},
    "dev_active_persona": {
        "kk": "✅ Белсенді персона: <b>{label}</b>",
        "ru": "✅ Активная персона: <b>{label}</b>",
    },
    "dev_persona_switched": {"kk": "Персона ауыстырылды", "ru": "Персона переключена"},
    "dev_no_active_persona": {"kk": "Белсенді DEV персона жоқ", "ru": "Нет активной DEV-персоны"},
    "dev_persona_cleared": {"kk": "Персона деректері тазартылды", "ru": "Данные персоны очищены"},
    "dev_profile_missing": {
        "kk": "Профиль жоқ — /start пәрменін жіберіңіз.",
        "ru": "Профиль не найден — отправьте /start.",
    },
    "dev_profile_deleted": {
        "kk": "🗑️ Профиль жойылды. Қайта тіркелу үшін /start пәрменін жіберіңіз.",
        "ru": "🗑️ Профиль удалён. Отправьте /start, чтобы зарегистрироваться снова.",
    },
    "dev_profile_reset": {
        "kk": "⚠️ Профиль бастапқы күйге қайтарылды. Қайта тіркелу үшін /start пәрменін жіберіңіз.",
        "ru": "⚠️ Профиль сброшен. Отправьте /start, чтобы зарегистрироваться снова.",
    },
    "cancel": {"kk": "❌ Болдырмау", "ru": "❌ Отмена"},
    "cancelled": {"kk": "❌ Болдырылмады.", "ru": "❌ Отменено."},
    "create_request": {"kk": "📝 Өтінім жасау", "ru": "📝 Создать заявку"},
    "my_requests": {"kk": "📋 Менің өтінімдерім", "ru": "📋 Мои заявки"},
    "shift_on": {"kk": "▶️ Ауысымға шығу", "ru": "▶️ На смену"},
    "shift_off": {"kk": "⏸️ Ауысымнан шығу", "ru": "⏸️ Уйти со смены"},
    "available_requests": {"kk": "📋 Қолжетімді өтінімдер", "ru": "📋 Доступные заявки"},
    "worker_my_requests": {"kk": "🔧 Менің өтінімдерім", "ru": "🔧 Мои заявки"},
    "summary": {"kk": "📊 Есептер", "ru": "📊 Отчёты"},
    "all_requests": {"kk": "📋 Барлық өтінімдер", "ru": "📋 Все заявки"},
    "pending_workers": {"kk": "⏳ Шешім күтуде", "ru": "⏳ Требуют решения"},
    "pending_items_empty": {
        "kk": "✅ Шешім күтетін ештеңе жоқ.",
        "ru": "✅ Нет вопросов, требующих решения.",
    },
    "pending_items_heading": {
        "kk": "⏳ <b>Шешім күтуде</b> — {page}/{pages}-бет (барлығы {total})\n"
              "Шешім қабылдау үшін карточканы ашыңыз\n",
        "ru": "⏳ <b>Требуют решения</b> — стр. {page}/{pages} (всего {total})\n"
              "Откройте карточку, чтобы принять решение\n",
    },
    "pending_registration_detail": {
        "kk": "⏳ Жаңа тіркелу: {role}",
        "ru": "⏳ Новая регистрация: {role}",
    },
    "pending_registration_item": {
        "kk": "👤 <b>Тіркелу #{id}</b> • {role} • {created}\n{name} • {apartment}-пәтер\n",
        "ru": "👤 <b>Регистрация #{id}</b> • {role} • {created}\n{name} • кв. {apartment}\n",
    },
    "pending_request_item": {
        "kk": "📹 <b>#{id} өтінімді келісу</b> • {created}\n{resident} • {apartment}-пәтер\n<i>{description}</i>\n",
        "ru": "📹 <b>Согласование заявки #{id}</b> • {created}\n{resident} • кв. {apartment}\n<i>{description}</i>\n",
    },
    "add_worker": {"kk": "➕ Орындаушы қосу", "ru": "➕ Добавить исполнителя"},
    "announcements_button": {"kk": "📢 Хабарландырулар", "ru": "📢 Объявления"},
    "participants": {"kk": "👥 Қатысушылар", "ru": "👥 Участники"},
    "manage_tenant": {"kk": "🔑 Жалға алушы", "ru": "🔑 Арендатор"},
    "resident_placeholder": {"kk": "Әрекетті таңдаңыз", "ru": "Выберите действие"},
    "worker_placeholder": {"kk": "Орындаушы мәзірі", "ru": "Меню исполнителя"},
    "dispatcher_placeholder": {"kk": "Диспетчер тақтасы", "ru": "Панель диспетчера"},
    "registration_resident": {"kk": "🏠 Мен тұрғынмын", "ru": "🏠 Я житель"},
    "registration_worker": {"kk": "🔧 Мен орындаушымын", "ru": "🔧 Я исполнитель"},
    "announcement_broadcast": {"kk": "📢 <b>Хабарландыру</b>\n\n{text}", "ru": "📢 <b>Объявление</b>\n\n{text}"},
    "new_request_notification": {
        "kk": "🆕 <b>Жаңа өтінім #{id}</b>\nСанат: {category}\nМекенжай: {address} | {resident}\nСипаттама: {description}\n\nҚабылдау үшін «{available_requests}» басыңыз.",
        "ru": "🆕 <b>Новая заявка #{id}</b>\nКатегория: {category}\nАдрес: {address} | {resident}\nОписание: {description}\n\nНажмите «{available_requests}» чтобы принять.",
    },
    "no_available_workers": {
        "kk": "⚠️ Жаңа #{id} өтініміне ауысымдағы қолжетімді орындаушылар жоқ.",
        "ru": "⚠️ Для новой заявки #{id} нет доступных исполнителей на смене.",
    },
    "request_accepted_notification": {
        "kk": "✅ Сіздің #{id} өтініміңізді {worker} орындаушысы қабылдады",
        "ru": "✅ Ваша заявка #{id} принята исполнителем {worker}",
    },
    "unknown_category": {"kk": "Белгісіз санат", "ru": "Неизвестная категория"},
    "start_first": {"kk": "Алдымен /start пәрменін басыңыз", "ru": "Сначала /start"},
    "registration_error": {"kk": "Қате. /start пәрменінен қайта бастаңыз", "ru": "Ошибка, попробуйте /start заново"},
    "registration_done": {"kk": "Рақмет, {name}! Деректер қабылданды ({apartment}-пәтер).\nДиспетчердің растауын күтіңіз — сізге хабарлама келеді.", "ru": "Спасибо, {name}! Данные приняты (кв. {apartment}).\nОжидайте подтверждения диспетчера — вам придёт уведомление."},
    "tenant_registration_done": {"kk": "Рақмет, {name}! Деректер қабылданды ({apartment}-пәтер).\nМеншік иесінің растауын күтіңіз — сізге хабарлама келеді.", "ru": "Спасибо, {name}! Данные приняты (кв. {apartment}).\nОжидайте подтверждения собственника — вам придёт уведомление."},
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
    "insufficient_rights": {"kk": "Құқық жеткіліксіз.", "ru": "Недостаточно прав."},
    "no_approved_workers": {"kk": "Расталған орындаушылар жоқ.", "ru": "Нет одобренных исполнителей."},
    "worker_not_found": {"kk": "Орындаушы табылмады", "ru": "Исполнитель не найден"},
    "worker_schedules": {"kk": "Орындаушылар кестесі", "ru": "Графики исполнителей"},
    "organization_timezone": {"kk": "Уақыт белдеуі", "ru": "Часовой пояс"},
    "choose_worker": {"kk": "Орындаушыны таңдаңыз:", "ru": "Выберите исполнителя:"},
    "schedule_add_hours": {"kk": "➕ Жұмыс уақытын қосу", "ru": "➕ Добавить часы"},
    "schedule_absence": {"kk": "🚫 Болмау", "ru": "🚫 Отсутствие"},
    "schedule_extra_shift": {"kk": "✅ Қосымша ауысым", "ru": "✅ Доп. смена"},
    "schedule_clear": {"kk": "🗑 Кестені тазарту", "ru": "🗑 Очистить график"},
    "actually_on_shift": {"kk": "Нақты ауысымда", "ru": "Фактически на смене"},
    "yes": {"kk": "иә", "ru": "да"},
    "no": {"kk": "жоқ", "ru": "нет"},
    "recurring_hours": {"kk": "Тұрақты жұмыс уақыты:", "ru": "Регулярные часы:"},
    "schedule_not_set": {
        "kk": "• орнатылмаған — тек ауысымға шығу белгісі қолданылады",
        "ru": "• не заданы — действует только отметка выхода на смену",
    },
    "recent_schedule_exceptions": {"kk": "Соңғы ерекшеліктер:", "ru": "Последние исключения:"},
    "schedule_available": {"kk": "✅ қолжетімді", "ru": "✅ доступен"},
    "schedule_unavailable": {"kk": "🚫 жоқ", "ru": "🚫 отсутствует"},
    "schedule_hours_prompt": {
        "kk": "Күндер мен жұмыс уақытын енгізіңіз, мысалы:\n<code>1-5 09:00-18:00</code>\nнемесе <code>1,3,5 20:00-08:00</code>. 1 — дүйсенбі.",
        "ru": "Введите дни и часы, например:\n<code>1-5 09:00-18:00</code>\nили <code>1,3,5 20:00-08:00</code>. 1 — понедельник.",
    },
    "schedule_hours_added": {"kk": "✅ Жұмыс уақыты қосылды.", "ru": "✅ Рабочие часы добавлены."},
    "schedule_exception_prompt": {
        "kk": "Ұйымның уақыт белдеуіндегі аралықты енгізіңіз:\n<code>25.03.2026 09:00-18:00 себеп</code>\nТүнгі ауысым келесі күні автоматты түрде аяқталады.",
        "ru": "Введите интервал в часовом поясе организации:\n<code>25.03.2026 09:00-18:00 причина</code>\nНочная смена автоматически завершится на следующий день.",
    },
    "schedule_exception_added": {"kk": "✅ Кесте ерекшелігі қосылды.", "ru": "✅ Исключение графика добавлено."},
    "schedule_cleared": {"kk": "Тұрақты кесте тазартылды", "ru": "Регулярный график очищен"},
    "schedule_hours_format": {
        "kk": "Пішім: 1-5 09:00-18:00 (1 — дүйсенбі, 7 — жексенбі)",
        "ru": "Формат: 1-5 09:00-18:00 (1 — понедельник, 7 — воскресенье)",
    },
    "schedule_exception_format": {
        "kk": "Пішім: 25.03.2026 09:00-18:00 себеп",
        "ru": "Формат: 25.03.2026 09:00-18:00 причина",
    },
    "schedule_end_after_start": {
        "kk": "Аяқталу уақыты басталу уақытынан кейін болуы керек",
        "ru": "Время окончания должно быть позже времени начала",
    },
    "start_shift_first": {
        "kk": "Алдымен ауысымға шығуды белгілеңіз: ▶️ Ауысымға шығу",
        "ru": "Сначала выйдите на смену: ▶️ На смену",
    },
    "not_scheduled_now": {
        "kk": "Қазір сіз ауысымға жоспарланбағансыз. Диспетчерге хабарласыңыз.",
        "ru": "Сейчас вы не запланированы на смену. Обратитесь к диспетчеру.",
    },
    "not_scheduled_claim": {
        "kk": "Қазір сіз ауысымға жоспарланбағансыз",
        "ru": "Сейчас вы не запланированы на смену",
    },
}

CATEGORY_LABELS = {
    "kk": {
        "electrician": "⚡ Электрик", "plumber": "🔧 Сантехник",
        "security": "🛡️ Күзет", "cleaning": "🧹 Клининг",
        "kazakhdomofon": "📹 Қазақдомофон",
    },
    "ru": {
        "electrician": "⚡ Электрик", "plumber": "🔧 Сантехник",
        "security": "🛡️ Охрана", "cleaning": "🧹 Клининг",
        "kazakhdomofon": "📹 Казахдомофон",
    },
}
STATUS_LABELS = {
    "kk": {"new": "🆕 Жаңа", "accepted": "🔧 Орындалуда", "closed": "✅ Аяқталды"},
    "ru": {"new": "🆕 Новая", "accepted": "🔧 В работе", "closed": "✅ Завершена"},
}
URGENCY_LABELS = {
    "kk": {"low": "Төмен", "normal": "Қалыпты", "high": "Жоғары"},
    "ru": {"low": "Низкий", "normal": "Обычный", "high": "Высокий"},
}


def _validate_catalogs() -> None:
    for key, translations in TEXTS.items():
        languages = set(translations)
        if languages != SUPPORTED_LANGUAGES:
            raise RuntimeError(
                f"Invalid translation {key!r}: expected {sorted(SUPPORTED_LANGUAGES)}, "
                f"got {sorted(languages)}"
            )

    for name, labels in (
        ("category", CATEGORY_LABELS),
        ("status", STATUS_LABELS),
        ("urgency", URGENCY_LABELS),
    ):
        if set(labels) != SUPPORTED_LANGUAGES:
            raise RuntimeError(f"Invalid language set in {name} labels")
        expected_values = set(labels[DEFAULT_LANGUAGE])
        for language, localized in labels.items():
            if set(localized) != expected_values:
                raise RuntimeError(f"Incomplete {name} labels for {language!r}")


_validate_catalogs()


def normalize_language(language: str | None) -> str:
    if not language:
        return DEFAULT_LANGUAGE
    normalized = language.strip().lower().replace("_", "-").split("-", 1)[0]
    return normalized if normalized in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def language_choices():
    return LANGUAGE_NAMES.items()


def t(key: str, language: str | None, **values) -> str:
    normalized = normalize_language(language)
    try:
        template = TEXTS[key][normalized]
    except KeyError as exc:
        raise KeyError(f"Missing translation {key!r} for language {normalized!r}") from exc
    return template.format(**values)


def text_variants(key: str) -> frozenset[str]:
    """Return all localized values for a Telegram text filter."""
    return frozenset(TEXTS[key].values())


def render(key: str, language: str | None, values: dict | None = None) -> str:
    """Render a message key with a recipient locale."""
    return t(key, language, **(values or {}))


def category_label(category: str | None, language: str | None) -> str:
    return CATEGORY_LABELS[normalize_language(language)].get(category, category or "")


def status_label(status: str | None, language: str | None) -> str:
    return STATUS_LABELS[normalize_language(language)].get(status, status or "")


def urgency_label(urgency: str | None, language: str | None) -> str:
    return URGENCY_LABELS[normalize_language(language)].get(urgency, urgency or "")


def role_label(role: str | None, language: str | None) -> str:
    """Return a public role name without exposing an internal role code."""
    key = f"role_{role}" if role else ""
    if key in TEXTS:
        return t(key, language)
    return "Белгісіз рөл" if normalize_language(language) == "kk" else "Неизвестная роль"
