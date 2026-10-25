"""API security middleware: rate limiting, headers, request guards."""

from __future__ import annotations

from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from meme_tracker.api.rate_limit import (
    DEFAULT_LIMITER,
    SECURITY_HEADERS,
    WRITE_LIMITER,
    is_write_path,
)


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        if path in ("/health", "/ready", "/"):
            response = await call_next(request)
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
            return response

        key = client_key(request)
        limiter = WRITE_LIMITER if (
            request.method in ("POST", "PUT", "PATCH", "DELETE") or is_write_path(path)
        ) else DEFAULT_LIMITER

        if not limiter.allow(key):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Retry later.",
                    "limit": limiter.limit,
                    "window_seconds": limiter.window_seconds,
                },
                headers={
                    **SECURITY_HEADERS,
                    "Retry-After": str(int(limiter.window_seconds)),
                },
            )

        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers["X-RateLimit-Remaining"] = str(limiter.remaining(key))
        return response


def cors_origins_from_settings() -> list[str]:
    from meme_tracker.config import get_settings

    settings = get_settings()
    origins: list[str] = []
    for url in (settings.mini_app_url, settings.app_base_url):
        if url and url.startswith("http"):
            origins.append(url.rstrip("/"))
    if settings.environment in ("development", "test", "dev") or not origins:
        origins.append("*")
    seen: set[str] = set()
    out: list[str] = []
    for o in origins:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out
