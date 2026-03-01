"""Reconstruction tasks (§12)."""

from workers.celery_app import app


@app.task(name="workers.tasks.reconstruction.run_reconstruction")
def run_reconstruction() -> None:
    """Placeholder: run full position reconstruction (Celery Beat)."""
    pass


@app.task(
    name="workers.tasks.reconstruction.reconstruct_wallet_history",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def reconstruct_wallet_history(self, wallet_id: str, user_id: str) -> None:  # type: ignore[no-untyped-def]
    """On-demand: reconstruct full history for a newly added wallet.

    Triggered by wallet creation. Updates wallets.sync_status through the
    pipeline phases: scanning -> backfilling -> computing -> synced.
    Full implementation in a later sprint (S04+).
    """
    # TODO(S04): implement HyperSync scanning, price backfill, lot creation
    _ = wallet_id, user_id
