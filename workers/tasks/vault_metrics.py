"""Vault metrics tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.vault_metrics.compute_vault_metrics")
def compute_vault_metrics() -> None:
    """Placeholder: compute vault metrics."""
    pass
