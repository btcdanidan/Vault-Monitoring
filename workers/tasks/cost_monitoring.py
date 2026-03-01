"""Cost monitoring tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.cost_monitoring.monitor_costs")
def monitor_costs() -> None:
    """Placeholder: cost monitoring job."""
    pass
