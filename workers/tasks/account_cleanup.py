"""Account cleanup: retry failed Supabase auth user deletions (§19.9)."""

import os

import httpx
import redis
from workers.celery_app import app

# Must match backend/app/services/account_deletion.py
PENDING_AUTH_DELETIONS_KEY = "pending_auth_deletions"


def _delete_supabase_auth_user_sync(user_id: str, supabase_url: str, service_role_key: str) -> bool:
    """Sync call to Supabase Admin API to delete auth user. Returns True on success."""
    url = f"{supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
    headers = {
        "Authorization": f"Bearer {service_role_key}",
        "apikey": service_role_key,
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.delete(url, headers=headers)
            return resp.status_code in (200, 204)
    except Exception:  # noqa: BLE001
        return False


@app.task(name="workers.tasks.account_cleanup.cleanup_orphaned_auth_users")
def cleanup_orphaned_auth_users() -> None:
    """
    Retry Supabase auth user deletion for user_ids in Redis pending_auth_deletions set.
    Intended to be run hourly via Celery Beat.
    """
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        return
    try:
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        pending = r.smembers(PENDING_AUTH_DELETIONS_KEY)
    except Exception:  # noqa: BLE001
        return
    for user_id in pending:
        if _delete_supabase_auth_user_sync(user_id, supabase_url, service_role_key):
            try:
                r.srem(PENDING_AUTH_DELETIONS_KEY, user_id)
            except Exception:  # noqa: BLE001
                pass
    try:
        r.close()
    except Exception:  # noqa: BLE001
        pass
