"""FastAPI app factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import admin, auth, positions, vaults, wallets, ws
from app.exceptions import ForbiddenException, UnauthorizedException


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="DeFi Vault Intelligence Platform API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(UnauthorizedException)
    async def unauthorized_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: UnauthorizedException
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(ForbiddenException)
    async def forbidden_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: ForbiddenException
    ) -> JSONResponse:
        content = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content)

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(wallets.router, prefix="/api/wallets", tags=["wallets"])
    app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
    app.include_router(vaults.router, prefix="/api/vaults", tags=["vaults"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(ws.router, prefix="/ws", tags=["ws"])
    return app


app = create_app()
