from app.config import Settings
from app.domain import UserRole


def is_telegram_user_allowed(user_id: int | None, settings: Settings) -> bool:
    if user_id is None:
        return False

    allowed_user_ids = settings.allowed_telegram_user_ids
    if not allowed_user_ids:
        return False

    return user_id in allowed_user_ids


def get_telegram_user_role(user_id: int, settings: Settings) -> UserRole:
    if user_id in settings.admin_telegram_user_ids:
        return UserRole.ADMIN
    if user_id in settings.manager_telegram_user_ids:
        return UserRole.MANAGER
    if user_id in settings.observer_telegram_user_ids:
        return UserRole.OBSERVER
    return UserRole.EMPLOYEE
