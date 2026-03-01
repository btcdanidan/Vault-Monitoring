"""Celery configuration — broker, serialization, and full Beat schedule from §10."""

import os

from celery.schedules import crontab

broker_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
result_backend = os.getenv("REDIS_URL", "redis://localhost:6379/0")

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"
enable_utc = True

# ---------------------------------------------------------------------------
# Full Celery Beat schedule — all periodic tasks from §10 plus operational
# tasks. Remove a key from _DISABLED_TASKS to enable it when its
# implementation is ready.
# ---------------------------------------------------------------------------

_FULL_BEAT_SCHEDULE: dict[str, dict] = {
    # ── Critical queue (dedicated worker) ─────────────────────────────────
    "refresh-prices": {
        "task": "workers.tasks.prices.refresh_prices",
        "schedule": 30.0,
        "options": {"queue": "critical"},
    },
    "refresh-health-factors": {
        "task": "workers.tasks.prices.refresh_health_factors",
        "schedule": 15.0,
        "options": {"queue": "critical"},
    },
    # ── Default queue (shared workers) ────────────────────────────────────
    "refresh-vault-metrics": {
        "task": "workers.tasks.vault_metrics.refresh_vault_metrics",
        "schedule": 300.0,  # 5 min
        "options": {"queue": "default"},
    },
    "snapshot-positions": {
        "task": "workers.tasks.positions.snapshot_positions",
        "schedule": 900.0,  # 15 min
        "options": {"queue": "default"},
    },
    "refresh-pendle-positions": {
        "task": "workers.tasks.positions.refresh_pendle_positions",
        "schedule": 900.0,  # 15 min
        "options": {"queue": "default"},
    },
    "sync-new-events": {
        "task": "workers.tasks.positions.sync_new_events",
        "schedule": 900.0,  # 15 min
        "options": {"queue": "default"},
    },
    "track-api-usage": {
        "task": "workers.tasks.cost_monitoring.track_api_usage",
        "schedule": 900.0,  # 15 min
        "options": {"queue": "default"},
    },
    "compute-risk-scores": {
        "task": "workers.tasks.risk.compute_risk_scores",
        "schedule": crontab(minute=0),  # every hour
        "options": {"queue": "default"},
    },
    "compute-vault-whale-concentration": {
        "task": "workers.tasks.vault_metrics.compute_vault_whale_concentration",
        "schedule": crontab(minute=0, hour="*/6"),  # every 6 hours
        "options": {"queue": "default"},
    },
    "run-advisory-scan": {
        "task": "workers.tasks.advisory.run_advisory_scan",
        "schedule": crontab(minute=0, hour="*/6"),  # every 6 hours
        "options": {"queue": "default"},
    },
    "compute-daily-cost-summary": {
        "task": "workers.tasks.cost_monitoring.compute_daily_cost_summary",
        "schedule": crontab(minute=0, hour=2),  # daily 02:00 UTC
        "options": {"queue": "default"},
    },
    # ── Operational (not in §10, always enabled) ──────────────────────────
    "cleanup-orphaned-auth-users": {
        "task": "workers.tasks.account_cleanup.cleanup_orphaned_auth_users",
        "schedule": crontab(minute=0),  # every hour
        "options": {"queue": "default"},
    },
}

# Tasks whose implementations are still placeholders.  Remove a key here
# when the corresponding sprint delivers a real implementation.
_DISABLED_TASKS: set[str] = {
    "refresh-health-factors",
    "refresh-vault-metrics",
    "snapshot-positions",
    "refresh-pendle-positions",
    "sync-new-events",
    "track-api-usage",
    "compute-risk-scores",
    "compute-vault-whale-concentration",
    "run-advisory-scan",
    "compute-daily-cost-summary",
}

beat_schedule = {
    key: entry
    for key, entry in _FULL_BEAT_SCHEDULE.items()
    if key not in _DISABLED_TASKS
}
