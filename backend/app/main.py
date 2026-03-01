"""FastAPI app factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, auth, positions, vaults, wallets, ws
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title="DeFi Vault Intelligence Platform API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(wallets.router, prefix="/api/wallets", tags=["wallets"])
    app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
    app.include_router(vaults.router, prefix="/api/vaults", tags=["vaults"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(ws.router, prefix="/ws", tags=["ws"])
    return app


app = create_app()
