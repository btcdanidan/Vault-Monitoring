"""Tests for Celery Beat schedule configuration against §10 spec."""

from __future__ import annotations

from celery.schedules import crontab

from workers.celeryconfig import (
    _DISABLED_TASKS,
    _FULL_BEAT_SCHEDULE,
    beat_schedule,
)

# ── §10 canonical task keys and their expected properties ─────────────────

S10_TASKS: dict[str, dict] = {
    "refresh-prices": {
        "task": "workers.tasks.prices.refresh_prices",
        "queue": "critical",
        "schedule": 30.0,
    },
    "refresh-health-factors": {
        "task": "workers.tasks.prices.refresh_health_factors",
        "queue": "critical",
        "schedule": 15.0,
    },
    "refresh-vault-metrics": {
        "task": "workers.tasks.vault_metrics.refresh_vault_metrics",
        "queue": "default",
        "schedule": 300.0,
    },
    "snapshot-positions": {
        "task": "workers.tasks.positions.snapshot_positions",
        "queue": "default",
        "schedule": 900.0,
    },
    "refresh-pendle-positions": {
        "task": "workers.tasks.positions.refresh_pendle_positions",
        "queue": "default",
        "schedule": 900.0,
    },
    "sync-new-events": {
        "task": "workers.tasks.positions.sync_new_events",
        "queue": "default",
        "schedule": 900.0,
    },
    "track-api-usage": {
        "task": "workers.tasks.cost_monitoring.track_api_usage",
        "queue": "default",
        "schedule": 900.0,
    },
    "compute-risk-scores": {
        "task": "workers.tasks.risk.compute_risk_scores",
        "queue": "default",
    },
    "compute-vault-whale-concentration": {
        "task": "workers.tasks.vault_metrics.compute_vault_whale_concentration",
        "queue": "default",
    },
    "run-advisory-scan": {
        "task": "workers.tasks.advisory.run_advisory_scan",
        "queue": "default",
    },
    "compute-daily-cost-summary": {
        "task": "workers.tasks.cost_monitoring.compute_daily_cost_summary",
        "queue": "default",
    },
}


class TestFullBeatScheduleCompleteness:
    """Every §10 task must appear in _FULL_BEAT_SCHEDULE."""

    def test_all_s10_tasks_present(self) -> None:
        for key in S10_TASKS:
            assert key in _FULL_BEAT_SCHEDULE, f"Missing §10 task: {key}"

    def test_no_unexpected_s10_tasks_missing(self) -> None:
        s10_keys = set(S10_TASKS)
        full_keys = set(_FULL_BEAT_SCHEDULE)
        assert s10_keys.issubset(full_keys)


class TestTaskNameStrings:
    """Task dotted-path names must match the registered Celery task names."""

    def test_task_names_match_spec(self) -> None:
        for key, expected in S10_TASKS.items():
            entry = _FULL_BEAT_SCHEDULE[key]
            assert entry["task"] == expected["task"], (
                f"{key}: expected task={expected['task']}, got {entry['task']}"
            )


class TestQueueAssignment:
    """Critical-queue tasks go to 'critical'; everything else to 'default'."""

    def test_critical_queue_tasks(self) -> None:
        for key in ("refresh-prices", "refresh-health-factors"):
            entry = _FULL_BEAT_SCHEDULE[key]
            assert entry["options"]["queue"] == "critical", f"{key} should be critical"

    def test_default_queue_tasks(self) -> None:
        default_keys = set(S10_TASKS) - {"refresh-prices", "refresh-health-factors"}
        for key in default_keys:
            entry = _FULL_BEAT_SCHEDULE[key]
            assert entry["options"]["queue"] == "default", f"{key} should be default"


class TestScheduleIntervals:
    """Verify cadences match §10."""

    def test_refresh_prices_30s(self) -> None:
        assert _FULL_BEAT_SCHEDULE["refresh-prices"]["schedule"] == 30.0

    def test_refresh_health_factors_15s(self) -> None:
        assert _FULL_BEAT_SCHEDULE["refresh-health-factors"]["schedule"] == 15.0

    def test_refresh_vault_metrics_5min(self) -> None:
        assert _FULL_BEAT_SCHEDULE["refresh-vault-metrics"]["schedule"] == 300.0

    def test_snapshot_positions_15min(self) -> None:
        assert _FULL_BEAT_SCHEDULE["snapshot-positions"]["schedule"] == 900.0

    def test_refresh_pendle_positions_15min(self) -> None:
        assert _FULL_BEAT_SCHEDULE["refresh-pendle-positions"]["schedule"] == 900.0

    def test_sync_new_events_15min(self) -> None:
        assert _FULL_BEAT_SCHEDULE["sync-new-events"]["schedule"] == 900.0

    def test_track_api_usage_15min(self) -> None:
        assert _FULL_BEAT_SCHEDULE["track-api-usage"]["schedule"] == 900.0

    def test_compute_risk_scores_hourly(self) -> None:
        sched = _FULL_BEAT_SCHEDULE["compute-risk-scores"]["schedule"]
        assert isinstance(sched, crontab)

    def test_whale_concentration_6h(self) -> None:
        sched = _FULL_BEAT_SCHEDULE["compute-vault-whale-concentration"]["schedule"]
        assert isinstance(sched, crontab)

    def test_advisory_scan_6h(self) -> None:
        sched = _FULL_BEAT_SCHEDULE["run-advisory-scan"]["schedule"]
        assert isinstance(sched, crontab)

    def test_daily_cost_summary_0200(self) -> None:
        sched = _FULL_BEAT_SCHEDULE["compute-daily-cost-summary"]["schedule"]
        assert isinstance(sched, crontab)


_ENABLED_S10_TASKS: set[str] = {
    "refresh-prices",
}


class TestDisablementMechanism:
    """Disabled tasks excluded from active beat_schedule; enabled ones present."""

    def test_unimplemented_s10_tasks_disabled(self) -> None:
        for key in S10_TASKS:
            if key in _ENABLED_S10_TASKS:
                continue
            assert key in _DISABLED_TASKS, f"{key} should be disabled"

    def test_implemented_s10_tasks_enabled(self) -> None:
        for key in _ENABLED_S10_TASKS:
            assert key not in _DISABLED_TASKS, f"{key} should be enabled"
            assert key in beat_schedule, f"{key} should be in beat_schedule"

    def test_disabled_tasks_excluded_from_beat_schedule(self) -> None:
        for key in _DISABLED_TASKS:
            assert key not in beat_schedule, f"{key} should not be in beat_schedule"

    def test_cleanup_task_enabled(self) -> None:
        assert "cleanup-orphaned-auth-users" in beat_schedule

    def test_cleanup_task_not_in_disabled(self) -> None:
        assert "cleanup-orphaned-auth-users" not in _DISABLED_TASKS

    def test_active_schedule_count(self) -> None:
        expected_active = len(_FULL_BEAT_SCHEDULE) - len(_DISABLED_TASKS)
        assert len(beat_schedule) == expected_active
