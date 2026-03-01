"""Price feed tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.prices.refresh_prices")
def refresh_prices() -> None:
    """Placeholder: refresh price feeds."""
    pass
