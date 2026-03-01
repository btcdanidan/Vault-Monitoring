# DeFi Vault Intelligence Platform

Monorepo for the DeFi Vault Intelligence Platform: backend (FastAPI), workers (Celery), and frontend (Next.js 14).

## Tech stack

- **Backend:** FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic
- **Workers:** Celery, Redis
- **Frontend:** Next.js 14 (App Router), TypeScript, Tailwind, TanStack Query, shadcn/ui
- **Infra:** Docker Compose, PostgreSQL + TimescaleDB, Nginx

## Quick start

1. Copy `.env.example` to `.env` and fill in values.
2. From `infra/` run:

   ```bash
   docker compose up
   ```

   This brings up PostgreSQL + TimescaleDB, Redis, FastAPI, Celery worker, Next.js, and Nginx.

3. Open the app via the URL Nginx is configured for (e.g. http://localhost).

## Repository layout

- `backend/` — FastAPI app, models, API routes, services, protocol adapters
- `workers/` — Celery tasks (prices, vault metrics, risk, advisory, etc.)
- `frontend/` — Next.js 14 App Router app
- `infra/` — Docker Compose, Nginx config, backup/seed scripts
- `docs/` — Deployment, troubleshooting, monitoring

See [docs/](docs/) for deployment and operational guides.
