"""Cost monitoring and API usage tracking tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.cost_monitoring.compute_daily_cost_summary")
def compute_daily_cost_summary() -> None:
    """Placeholder: compute daily cost summary (runs 02:00 UTC)."""
    pass


@app.task(name="workers.tasks.cost_monitoring.track_api_usage")
def track_api_usage() -> None:
    """Placeholder: track external API call counts and costs."""
    pass
