# Deployment

## Quick start (Docker Compose)

From the repo root:

```bash
cd infra
docker compose up --build -d
```

This brings up PostgreSQL 16 + TimescaleDB, Redis 7 (256MB, allkeys-lru), FastAPI (4 workers), 3× Celery workers (2 concurrency each), Celery beat, Next.js, and Nginx. All services have healthchecks.

**Health endpoints:**

- **Backend:** `GET /api/admin/health` → `{"status":"ok"}`
- **Frontend:** `GET /` (Next.js app)
- **Nginx:** `GET /` (proxies to frontend), `GET /api/` (proxies to FastAPI), `GET /ws/` (WebSocket to FastAPI)
- **Postgres:** `pg_isready` (compose healthcheck)
- **Redis:** `redis-cli ping` (compose healthcheck)
- **Celery:** `celery inspect ping` (compose healthcheck)

**Routing:** Nginx routes `/api` → FastAPI, `/ws` → FastAPI WebSocket, `/` → Next.js.

---

- Environment variables (see `.env.example`)
- Database migrations (Alembic): run from `backend/` with `DATABASE_URL` set
- Nginx and SSL (production)
