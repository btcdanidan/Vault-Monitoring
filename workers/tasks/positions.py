"""Position snapshot and sync tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.positions.snapshot_positions")
def snapshot_positions() -> None:
    """Placeholder: snapshot current positions for historical tracking."""
    pass


@app.task(name="workers.tasks.positions.refresh_pendle_positions")
def refresh_pendle_positions() -> None:
    """Placeholder: refresh Pendle PT/YT/LP positions."""
    pass


@app.task(name="workers.tasks.positions.sync_new_events")
def sync_new_events() -> None:
    """Placeholder: sync new on-chain events since last checkpoint."""
    pass
