"""Celery application and task autodiscovery."""

from celery import Celery

from workers.celeryconfig import broker_url, result_backend

app = Celery(
    "defi_vault_workers",
    broker=broker_url,
    backend=result_backend,
    include=[
    "workers.tasks.prices",
    "workers.tasks.vault_metrics",
    "workers.tasks.positions",
    "workers.tasks.risk",
    "workers.tasks.advisory",
    "workers.tasks.reconstruction",
    "workers.tasks.cost_monitoring",
],
)
app.config_from_object("workers.celeryconfig")
