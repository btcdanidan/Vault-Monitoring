"""Position sync tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.positions.sync_positions")
def sync_positions() -> None:
    """Placeholder: sync positions from chain."""
    pass
