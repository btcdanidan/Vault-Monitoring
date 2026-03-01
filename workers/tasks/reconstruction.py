"""Reconstruction tasks."""

from workers.celery_app import app


@app.task(name="workers.tasks.reconstruction.run_reconstruction")
def run_reconstruction() -> None:
    """Placeholder: run position reconstruction."""
    pass
