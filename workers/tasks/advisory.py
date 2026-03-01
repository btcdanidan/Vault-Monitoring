"""Advisory / rebalancing tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.advisory.run_advisory_scan")
def run_advisory_scan() -> None:
    """Placeholder: run rebalancing advisory scan."""
    pass
