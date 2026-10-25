"""Pure rate-limit primitives (no Starlette dependency)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class SlidingWindowLimiter:
    """Simple sliding-window rate limiter (requests per window_seconds)."""

    limit: int
    window_seconds: float
    _hits: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        cutoff = now - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def remaining(self, key: str, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        cutoff = now - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        return max(0, self.limit - len(q))


DEFAULT_LIMITER = SlidingWindowLimiter(limit=120, window_seconds=60.0)
WRITE_LIMITER = SlidingWindowLimiter(limit=30, window_seconds=60.0)

WRITE_PREFIXES = (
    "/api/v1/tokens/evaluate",
    "/api/v1/jobs/",
    "/api/v1/wallets/track",
    "/api/v1/wallets/untrack",
    "/api/v1/wallets/transactions",
    "/api/v1/discovery/",
    "/api/v1/admin/",
)


def is_write_path(path: str) -> bool:
    return any(path.startswith(p) for p in WRITE_PREFIXES)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}
