"""Auth and profile resolution for JWT-backed requests."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import Profile


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
    return profile
