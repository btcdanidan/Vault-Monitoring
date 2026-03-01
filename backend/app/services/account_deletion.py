"""Account deletion service (§19.9): cascade delete profile + Supabase auth cleanup."""

import asyncio
import uuid

import httpx
import redis
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.profile import Profile

logger = structlog.get_logger(__name__)

PENDING_AUTH_DELETIONS_KEY = "pending_auth_deletions"


async def _delete_supabase_auth_user(
    user_id: uuid.UUID,
    supabase_url: str,
    service_role_key: str,
) -> bool:
    """
    Call Supabase Admin API to delete the auth user.
    Returns True on success (2xx), False on failure.
    """
    url = f"{supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
    headers = {
        "Authorization": f"Bearer {service_role_key}",
        "apikey": service_role_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(url, headers=headers, timeout=15.0)
            if resp.status_code in (200, 204):
                return True
            logger.warning(
                "Supabase delete user failed",
                user_id=str(user_id),
                status=resp.status_code,
                body=resp.text[:500],
            )
            return False
    except Exception as e:  # noqa: BLE001
        logger.warning("Supabase delete user error", user_id=str(user_id), error=str(e))
        return False


def _redis_sadd_pending(user_id: uuid.UUID, redis_url: str) -> None:
    """Sync: add user_id to Redis set for later retry."""
    try:
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        r.sadd(PENDING_AUTH_DELETIONS_KEY, str(user_id))
        r.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis SADD pending deletion failed", user_id=str(user_id), error=str(e))


async def queue_failed_supabase_deletion(user_id: uuid.UUID) -> None:
    """Queue user_id for hourly Celery retry of Supabase auth deletion."""
    settings = get_settings()
    if not settings.redis_url:
        logger.warning("Redis not configured; cannot queue pending auth deletion", user_id=str(user_id))
        return
    await asyncio.to_thread(_redis_sadd_pending, user_id, settings.redis_url)


async def delete_user_account(session: AsyncSession, user_id: uuid.UUID) -> tuple[bool, bool]:
    """
    Delete user's profile (cascades to all user-owned tables), then remove Supabase auth user.
    If Supabase call fails, queue user_id for hourly cleanup task.

    Returns (deleted_ok, auth_cleanup_pending).
    Raises ValueError if profile not found or if target is an admin.
    """
    result = await session.execute(select(Profile).where(Profile.id == user_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise ValueError("Profile not found")
    if profile.is_admin:
        raise ValueError("Cannot delete an admin account")

    await session.execute(delete(Profile).where(Profile.id == user_id))
    await session.flush()
    # Commit is done by the caller (request scope).

    settings = get_settings()
    auth_cleanup_pending = False
    if settings.supabase_url and settings.supabase_service_role_key:
        ok = await _delete_supabase_auth_user(
            user_id,
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
        if not ok:
            await queue_failed_supabase_deletion(user_id)
            auth_cleanup_pending = True
    else:
        logger.warning("Supabase not configured; skipping auth user deletion", user_id=str(user_id))
        auth_cleanup_pending = True

    return (True, auth_cleanup_pending)
