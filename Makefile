.PHONY: up down logs build migrate rollback lint test format shell-db shell-redis

COMPOSE := docker compose -f infra/docker-compose.yml

# ---------------------------------------------------------------------------
# Docker Compose
# ---------------------------------------------------------------------------
up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

build:
	$(COMPOSE) up -d --build

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
migrate:
	$(COMPOSE) exec backend alembic upgrade head

rollback:
	$(COMPOSE) exec backend alembic downgrade -1

shell-db:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-defi} -d $${POSTGRES_DB:-defi_vault}

shell-redis:
	$(COMPOSE) exec redis redis-cli

# ---------------------------------------------------------------------------
# Backend (Python)
# ---------------------------------------------------------------------------
lint:
	cd backend && ruff check . && ruff format --check .

format:
	cd backend && ruff check --fix . && ruff format .

test:
	cd backend && pytest

typecheck:
	cd backend && pyright

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
lint-frontend:
	cd frontend && npm run lint

# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------
up-prod:
	DOMAIN=$${DOMAIN} $(COMPOSE) -f infra/docker-compose.prod.yml up -d

init-ssl:
	bash infra/scripts/init-ssl.sh

backup:
	bash infra/scripts/backup.sh
