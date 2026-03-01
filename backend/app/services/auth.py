"""Auth and profile resolution for JWT-backed requests."""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import Profile
from app.services import notifications as notifications_service


async def get_or_create_profile(
    session: AsyncSession,
    user_id: uuid.UUID,
    email: str | None = None,
) -> Profile:
    """Fetch profile by user id; if missing, create one and return it."""
    result = await session.execute(select(Profile).where(Profile.id == user_id))
    profile = result.scalar_one_or_none()
    if profile is not None:
        return profile
    now = datetime.now(UTC)
    profile = Profile(
        id=user_id,
        email=email or "",
        approved=False,
        rejected=False,
        is_admin=False,
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    await session.flush()
    await session.refresh(profile)
    # Fire-and-forget: notify admin of new signup (do not block auth response)
    asyncio.create_task(notifications_service.notify_telegram_new_signup(profile.email))
    return profile
