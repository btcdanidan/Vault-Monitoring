FROM python:3.12-slim

WORKDIR /app

COPY workers/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# When built from repo root: workers/ is at context root
COPY workers/ ./workers/
ENV PYTHONPATH=/app

CMD ["celery", "-A", "workers.celery_app:app", "worker", "--loglevel=info"]
