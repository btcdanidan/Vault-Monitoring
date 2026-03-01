"""Risk scoring tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.risk.compute_risk_scores")
def compute_risk_scores() -> None:
    """Placeholder: compute risk scores."""
    pass
