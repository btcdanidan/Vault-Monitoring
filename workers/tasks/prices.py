"""Price feed and health factor tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.prices.refresh_prices")
def refresh_prices() -> None:
    """Placeholder: refresh price feeds."""
    pass


@app.task(name="workers.tasks.prices.refresh_health_factors")
def refresh_health_factors() -> None:
    """Placeholder: refresh health factors for lending positions."""
    pass
