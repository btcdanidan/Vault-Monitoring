FROM python:3.12-slim

WORKDIR /app

# When built from repo root (infra/docker-compose): use backend/ prefix
COPY backend/pyproject.toml ./
COPY backend/README.md ./
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
