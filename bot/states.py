from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    waiting_language = State()
    waiting_role = State()
    waiting_resident_subrole = State()
    waiting_name = State()
    waiting_apartment = State()
    waiting_worker_category = State()


class RequestStates(StatesGroup):
    waiting_category = State()
    waiting_service_area = State()
    waiting_media = State()
    waiting_description = State()
    waiting_confirm = State()
    waiting_duplicate_clarification = State()
    waiting_duplicate_decision = State()


class WorkerCompletionStates(StatesGroup):
    waiting_comment = State()


class AnnouncementStates(StatesGroup):
    waiting_text = State()


class RequestApprovalStates(StatesGroup):
    waiting_rejection_comment = State()


class AddWorkerStates(StatesGroup):
    waiting_telegram_id = State()
    waiting_category = State()


class ScheduleStates(StatesGroup):
    waiting_worker = State()
    waiting_hours = State()
    waiting_exception = State()
    waiting_exception_details = State()


class ReportDateStates(StatesGroup):
    waiting_start = State()
    waiting_end = State()
