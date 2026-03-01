"""Shared FastAPI dependencies."""

import uuid
from collections.abc import AsyncGenerator

import jwt
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.exceptions import ForbiddenException, UnauthorizedException
from app.services.auth import get_or_create_profile

__all__ = ["get_db_session", "get_current_user_id"]


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency alias for database session."""
    async for session in get_db():
        yield session


async def get_current_user_id(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> uuid.UUID:
    """
    Validate JWT, resolve profile, set RLS context, and return user_id.
    Raises UnauthorizedException (401) or ForbiddenException (403) on failure.
    """
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        raise UnauthorizedException(detail="Auth not configured")

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise UnauthorizedException(detail="Missing or invalid Authorization header")
    token = auth_header[7:].strip()
    if not token:
        raise UnauthorizedException(detail="Missing token")

    try:
        payload = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedException(detail="Token expired") from None
    except jwt.InvalidTokenError:
        raise UnauthorizedException(detail="Invalid token") from None

    sub = payload.get("sub")
    if not sub:
        raise UnauthorizedException(detail="Missing sub claim")
    try:
        user_id = uuid.UUID(sub)
    except (ValueError, TypeError):
        raise UnauthorizedException(detail="Invalid sub claim") from None
    email = payload.get("email")

    profile = await get_or_create_profile(db, user_id, email)

    if profile.rejected:
        raise ForbiddenException(detail="Account rejected", reason="rejected")
    if not profile.approved:
        raise ForbiddenException(
            detail="Account pending approval",
            reason="pending_approval",
        )

    # RLS: set session variable for PostgreSQL (no-op on SQLite for tests)
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SET LOCAL app.current_user_id = :uid"),
            {"uid": str(user_id)},
        )

    request.state.user_id = user_id
    return user_id
