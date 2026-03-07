FROM python:3.12-slim

WORKDIR /app

COPY workers/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# When built from repo root: workers/ is at context root
COPY workers/ ./workers/

# Copy backend app package — workers tasks (vault_metrics, event_scanner) import
# from app.adapters, app.services, and app.schemas at runtime
COPY backend/app/ ./app/

ENV PYTHONPATH=/app

CMD ["celery", "-A", "workers.celery_app:app", "worker", "--loglevel=info"]
