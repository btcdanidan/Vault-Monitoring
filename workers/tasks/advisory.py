"""Advisory / rebalancing tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.advisory.run_advisory")
def run_advisory() -> None:
    """Placeholder: run rebalancing advisory."""
    pass
