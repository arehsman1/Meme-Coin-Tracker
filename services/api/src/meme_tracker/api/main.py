"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from meme_tracker import __version__
from meme_tracker.api.routes import admin, discovery, health, monitoring, tokens, wallets
from meme_tracker.api.security import SecurityMiddleware, cors_origins_from_settings
from meme_tracker.config import get_settings
from meme_tracker.providers.registry import get_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.started_at = datetime.now(timezone.utc)
    get_registry()
    yield
    try:
        await get_registry().close_all()
    except Exception:
        pass


app = FastAPI(
    title="Meme Coin Tracker API",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(SecurityMiddleware)

_origins = cors_origins_from_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=("*" not in _origins),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(tokens.router)
app.include_router(discovery.router)
app.include_router(admin.router)
app.include_router(monitoring.router)
app.include_router(wallets.router)
