"""Admin account management service (§19.3.1)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import Profile

StatusFilter = str  # "all" | "pending" | "approved" | "rejected"


async def list_accounts(
    session: AsyncSession,
    status_filter: StatusFilter = "all",
) -> list[Profile]:
    """
    List all profiles with optional status filter.
    pending = not approved and not rejected; approved/rejected/all = no filter.
    """
    stmt = select(Profile).order_by(Profile.created_at.desc())
    if status_filter == "pending":
        stmt = stmt.where(Profile.approved.is_(False), Profile.rejected.is_(False))
    elif status_filter == "approved":
        stmt = stmt.where(Profile.approved.is_(True))
    elif status_filter == "rejected":
        stmt = stmt.where(Profile.rejected.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_accounts(
    session: AsyncSession,
    status_filter: StatusFilter = "all",
) -> int:
    """Count profiles matching the same status filter as list_accounts."""
    stmt = select(func.count()).select_from(Profile)
    if status_filter == "pending":
        stmt = stmt.where(Profile.approved.is_(False), Profile.rejected.is_(False))
    elif status_filter == "approved":
        stmt = stmt.where(Profile.approved.is_(True))
    elif status_filter == "rejected":
        stmt = stmt.where(Profile.rejected.is_(True))
    result = await session.execute(stmt)
    return result.scalar_one() or 0


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> Profile | None:
    """Fetch a single profile by id. Returns None if not found."""
    result = await session.execute(select(Profile).where(Profile.id == user_id))
    return result.scalar_one_or_none()


async def approve_account(
    session: AsyncSession,
    target_user_id: uuid.UUID,
    admin_user_id: uuid.UUID,
) -> Profile:
    """
    Set profile approved=True, rejected=False, approved_at=now(), approved_by=admin_user_id.
    Raises ValueError if profile not found.
    """
    profile = await get_profile(session, target_user_id)
    if profile is None:
        raise ValueError("Profile not found")
    now = datetime.now(UTC)
    profile.approved = True
    profile.rejected = False
    profile.approved_at = now
    profile.approved_by = admin_user_id
    profile.updated_at = now
    await session.flush()
    await session.refresh(profile)
    return profile


async def reject_account(
    session: AsyncSession,
    target_user_id: uuid.UUID,
) -> Profile:
    """
    Set profile rejected=True, approved=False.
    Raises ValueError if profile not found; raises ValueError if target is admin.
    """
    profile = await get_profile(session, target_user_id)
    if profile is None:
        raise ValueError("Profile not found")
    if profile.is_admin:
        raise ValueError("Cannot reject an admin")
    now = datetime.now(UTC)
    profile.rejected = True
    profile.approved = False
    profile.approved_at = None
    profile.approved_by = None
    profile.updated_at = now
    await session.flush()
    await session.refresh(profile)
    return profile
