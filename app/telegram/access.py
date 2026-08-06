from app.config import Settings


def is_telegram_user_allowed(user_id: int | None, settings: Settings) -> bool:
    if user_id is None:
        return False

    allowed_user_ids = settings.allowed_telegram_user_ids
    if not allowed_user_ids:
        return False

    return user_id in allowed_user_ids
