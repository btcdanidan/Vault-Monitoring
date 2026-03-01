"""Celery configuration from environment."""

import os

from celery.schedules import crontab

broker_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
result_backend = os.getenv("REDIS_URL", "redis://localhost:6379/0")

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"
enable_utc = True

# Celery Beat schedule — only S02-relevant tasks for now.
# Additional tasks (prices, vault_metrics, risk, etc.) will be added in their sprints.
beat_schedule = {
    "cleanup-orphaned-auth-users": {
        "task": "workers.tasks.account_cleanup.cleanup_orphaned_auth_users",
        "schedule": crontab(minute=0),  # every hour
        "options": {"queue": "default"},
    },
}
