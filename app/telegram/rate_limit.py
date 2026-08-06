import asyncio
import time


class UserRateLimiter:
    def __init__(self, cooldown_seconds: float) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._last_request_at: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def retry_after(self, user_id: int) -> float:
        if self.cooldown_seconds <= 0:
            return 0.0

        now = time.monotonic()
        async with self._lock:
            last_request_at = self._last_request_at.get(user_id)
            if last_request_at is None:
                self._last_request_at[user_id] = now
                return 0.0

            elapsed = now - last_request_at
            remaining = self.cooldown_seconds - elapsed
            if remaining <= 0:
                self._last_request_at[user_id] = now
                return 0.0

            return remaining
