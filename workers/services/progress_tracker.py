"""Redis-backed progress tracker for wallet history reconstruction (§12).

Writes per-wallet reconstruction status to Redis so the frontend can poll
progress during the onboarding screen.

Key format: ``reconstruction:{wallet_id}``
Value: JSON ``{phase, progress_pct, events_found, transactions_found, error_message, last_updated}``
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import redis
import structlog

from workers.services.schemas import ProgressStatus, ReconstructionPhase

logger = structlog.get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RECONSTRUCTION_KEY_PREFIX = "reconstruction"
RECONSTRUCTION_KEY_TTL_SECONDS = 86_400  # 24 hours

_redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(connection_pool=_redis_pool)


class ProgressTracker:
    """Track reconstruction progress for a single wallet via Redis."""

    def __init__(self, wallet_id: str) -> None:
        self._wallet_id = wallet_id
        self._key = f"{RECONSTRUCTION_KEY_PREFIX}:{wallet_id}"
        self._redis = _get_redis()
        self._events_found = 0
        self._transactions_found = 0

    def update(
        self,
        phase: ReconstructionPhase,
        progress_pct: int,
        *,
        events_found: int | None = None,
        transactions_found: int | None = None,
    ) -> None:
        """Write current progress to Redis."""
        if events_found is not None:
            self._events_found = events_found
        if transactions_found is not None:
            self._transactions_found = transactions_found

        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "phase": phase.value,
            "progress_pct": max(0, min(100, progress_pct)),
            "events_found": self._events_found,
            "transactions_found": self._transactions_found,
            "error_message": None,
            "last_updated": now.isoformat(),
        }
        self._redis.set(self._key, json.dumps(payload), ex=RECONSTRUCTION_KEY_TTL_SECONDS)
        logger.debug(
            "reconstruction_progress",
            wallet_id=self._wallet_id,
            phase=phase.value,
            progress_pct=progress_pct,
        )

    def set_error(self, message: str) -> None:
        """Record a fatal error."""
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "phase": ReconstructionPhase.ERROR.value,
            "progress_pct": 0,
            "events_found": self._events_found,
            "transactions_found": self._transactions_found,
            "error_message": message[:500],
            "last_updated": now.isoformat(),
        }
        self._redis.set(self._key, json.dumps(payload), ex=RECONSTRUCTION_KEY_TTL_SECONDS)
        logger.warning(
            "reconstruction_error",
            wallet_id=self._wallet_id,
            error=message[:200],
        )

    def get_status(self) -> ProgressStatus | None:
        """Read current progress from Redis (returns None if no key)."""
        raw = self._redis.get(self._key)
        if raw is None:
            return None
        data = json.loads(raw)
        return ProgressStatus(
            phase=ReconstructionPhase(data["phase"]),
            progress_pct=data["progress_pct"],
            events_found=data["events_found"],
            transactions_found=data["transactions_found"],
            error_message=data.get("error_message"),
            last_updated=datetime.fromisoformat(data["last_updated"]) if data.get("last_updated") else None,
        )

    def clear(self) -> None:
        """Remove the progress key (called after reconstruction completes)."""
        self._redis.delete(self._key)
