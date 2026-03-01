"""Vault metrics tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.vault_metrics.refresh_vault_metrics")
def refresh_vault_metrics() -> None:
    """Placeholder: refresh vault metrics from protocol APIs."""
    pass


@app.task(name="workers.tasks.vault_metrics.compute_vault_whale_concentration")
def compute_vault_whale_concentration() -> None:
    """Placeholder: compute whale concentration per vault (6h regular / 24h full)."""
    pass
