"""FastAPI app factory."""

import logging
import os
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import admin, auth, positions, vaults, wallets, ws
from app.exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)


def _configure_structlog() -> None:
    """Configure structlog for structured JSON logging."""
    is_dev = os.getenv("ENVIRONMENT", "development") == "development"

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_dev:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


_configure_structlog()


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

    @app.exception_handler(BadRequestException)
    async def bad_request_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: BadRequestException
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(NotFoundException)
    async def not_found_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: NotFoundException
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(ConflictException)
    async def conflict_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: ConflictException
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(wallets.router, prefix="/api/wallets", tags=["wallets"])
    app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
    app.include_router(vaults.router, prefix="/api/vaults", tags=["vaults"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(ws.router, prefix="/ws", tags=["ws"])
    return app


app = create_app()
