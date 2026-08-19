from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    waiting_role = State()
    waiting_name = State()
    waiting_apartment = State()
    waiting_worker_category = State()


class RequestStates(StatesGroup):
    waiting_category = State()
    waiting_description = State()


class AnnouncementStates(StatesGroup):
    waiting_text = State()


class AddWorkerStates(StatesGroup):
    waiting_telegram_id = State()
    waiting_category = State()
