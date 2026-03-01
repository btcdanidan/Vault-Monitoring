# CLAUDE.md — DeFi Vault Intelligence Platform

> Primary instruction file for Claude Code. Read this FIRST before touching any code.

## Project Overview

A self-hosted DeFi P&L terminal with risk intelligence and rebalancing advisory. Single-developer project built with Claude Code as the primary coding partner.

**Three questions this platform answers:**

1. **Am I making money?** → P&L Engine (lot-based FIFO + WAC cost basis, yield tracking, borrow costs)
2. **Am I safe?** → Risk Engine (5-layer Credora-inspired deterministic scoring)
3. **Should I move?** → Rebalancing Advisory (gas + bridge cost-aware, manual execution only)

**Chains:** Ethereum, Base, Solana. **No Bitcoin.**  
**MVP protocols:** Morpho, Aave v3, Pendle, Euler  
**Phase 2 protocols:** Gearbox, Compound v3, Yearn, Kamino, Jupiter, Jito, Lido, Rocket Pool

---

## Skill Roles

Claude Code operates under different "hats" depending on the task. Each skill role has a specific mindset, quality bar, and set of concerns. When picking up a ticket, identify which role(s) apply and follow their guidelines.

### 🎯 CPO — Chief Product Officer

**When:** Spec interpretation, feature scoping, UX flow decisions, acceptance criteria validation.

- The Product Specification (20 sections in Notion) is the source of truth
- §10 (System Architecture) is authoritative for ALL table schemas — if any section conflicts with §10, §10 wins
- Never invent features not in the spec. If something is ambiguous, ask before building
- Borrow positions are tracked as debt, NOT netted from portfolio value
- Rebalancing is always manual advisory — no tx building, no automation
- V1 is dashboard-first. Push notifications are V2. Don't build V2 features in V1 sprints
- Pendle PT/YT are excluded from APY-based triggers. LP is treated normally
- Tax: basic CSV export in V1. Tax-specific formatting is Phase 3

**Key spec URLs:**
- Product Spec index: `https://www.notion.so/312d81f6e2628176a09de9c8e7904645`
- §10 System Architecture (canonical schemas): `https://www.notion.so/315d81f6e26281febbbcf9718d0ce786`
- §5 P&L Engine: `https://www.notion.so/315d81f6e26281c9b197e21f6f155a41`
- §6 Risk Engine: `https://www.notion.so/315d81f6e2628195afa1cdfc95004732`
- §7 Rebalancing Advisory: `https://www.notion.so/315d81f6e26281c59a1df164e8eb3c8d`

### 🏗️ CTO — Chief Technology Officer

**When:** Architecture decisions, dependency management, performance analysis, system design reviews.

- The tech stack is SETTLED. Do not re-litigate. Do not introduce alternatives
- Dual-database topology: Supabase (auth only) + self-hosted PostgreSQL 16 + TimescaleDB (all app data)
- Budget ceiling: $100/month. Every dependency/service choice must justify its cost
- Celery 5.3 (not Taskiq). Redis 7 for cache + broker + pub/sub. FastAPI ≥0.115. Next.js 14+ App Router
- HyperSync Python client for EVM historical data (replaces The Graph, custom block scanning)
- Helius for Solana. DeFiLlama for prices/TVL. LI.FI for bridge costs
- WebSocket in V1 for health factors (15s) and prices (30s). REST polling for everything else
- Docker Compose on single VPS: 8GB RAM, 4 vCPU, 160GB SSD
- 3 Celery workers × 2 concurrency = 6 concurrent tasks. Critical queue with dedicated worker

**Do NOT add any dependency without justification. The spec defines the stack — stick to it.**

### 🐍 Backend Engineer (Python)

**When:** FastAPI routes, SQLAlchemy models, Pydantic schemas, service layer, Alembic migrations.

**Stack:** FastAPI, SQLAlchemy 2.0 (async), asyncpg, Pydantic v2, Alembic, structlog

**Patterns (mandatory):**
- Async everywhere: `async def` for all API handlers and DB operations
- SQLAlchemy 2.0 style: `select()` not `session.query()`
- Dependency injection via FastAPI `Depends()`
- Service layer between routes and models — routes must be thin
- All database access through the service layer, never directly in routes
- Custom exception classes in `app/exceptions.py`
- structlog for all logging
- Type hints on every function. Pyright strict mode must pass
- Ruff for linting and formatting

**Naming:**
- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- SQLAlchemy models: singular (`Position`, not `Positions`)
- Pydantic schemas: suffixed (`PositionResponse`, `PositionCreate`)
- Celery tasks: verb_noun (`refresh_prices`, `compute_risk_scores`)

**Auth pattern:**
```python
# Every endpoint extracts user_id from JWT
@router.get("/positions")
async def list_positions(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await position_service.list_for_user(db, user_id)
```

**Database migrations:**
- One Alembic migration per logical change
- Descriptive messages: `add_position_snapshots_hypertable`
- Always include both `upgrade()` and `downgrade()`
- TimescaleDB hypertables via `op.execute("SELECT create_hypertable(...)")`

### ⚙️ Celery / Worker Engineer

**When:** Background tasks, scheduled jobs, data collection pipelines, HyperSync integration.

**Stack:** Celery 5.3, Redis broker, Celery Beat, HyperSync Python client, httpx

**Celery Beat schedule (from §10 — authoritative):**

| Task | Cadence | Queue | Priority |
|------|---------|-------|----------|
| `refresh_prices` | 30s | critical | Critical |
| `refresh_health_factors` | 15s | critical | Critical |
| `refresh_vault_metrics` | 5 min | default | High |
| `snapshot_positions` | 15 min | default | High |
| `refresh_pendle_positions` | 15 min | default | High |
| `sync_new_events` | 15 min | default | High |
| `track_api_usage` | 15 min | default | Medium |
| `compute_risk_scores` | 1 hour | default | Medium |
| `compute_vault_whale_concentration` | 6h/24h | default | Low |
| `run_advisory_scan` | 6 hours | default | Low |
| `compute_daily_cost_summary` | Daily 02:00 UTC | default | Low |

**On-demand tasks** (triggered by user action, not Celery Beat):
- `reconstruct_wallet_history` — Full history for new wallet (§12)
- `analyse_portfolio` — Manual advisory scan (§7). Results expire after 30 min
- `recalculate_positions` — Full P&L recalc after manual entry or price correction (§15)

**Worker pool:** 3 workers × `--concurrency=2`. Critical tasks on dedicated `critical` queue with 1 reserved worker. Redis memory budget: ~200MB.

**Workers bypass RLS** — they use a `BYPASSRLS` database role. No JWT, no Supabase interaction.

### ⛓️ Blockchain Data Engineer

**When:** Protocol adapters, HyperSync queries, on-chain event parsing, share-to-asset conversions, Solana instruction decoding.

**Protocol adapter interface (mandatory):**
```python
class ProtocolAdapter(ABC):
    @abstractmethod
    async def fetch_live_metrics(self, vault_addresses: list[str]) -> list[VaultMetrics]: ...
    
    @abstractmethod
    async def fetch_positions(self, wallet: str) -> list[RawPosition]: ...
    
    @abstractmethod
    async def fetch_historical_events(
        self, wallet: str, from_block: int, to_block: int
    ) -> list[RawEvent]: ...
```

**Data sources per protocol:**

| Protocol | Live State | Historical Events | Adapter Priority |
|----------|-----------|-------------------|-----------------|
| Morpho | GraphQL API | HyperSync | MVP (Sprint 4) |
| Aave v3 | UiPoolDataProviderV3 multicall | HyperSync | MVP (Sprint 4) |
| Pendle | REST API | HyperSync + RouterStatic | Sprint 10 |
| Euler v2 | eVault reads | HyperSync | Sprint 10 |
| Gearbox | CreditFacade/CreditManager | HyperSync | Phase 2 |
| Compound v3 | Comet reads | HyperSync | Phase 2 |
| Yearn v3 | yDaemon API | ERC-4626 standard | Phase 2 |
| Kamino | Solana SDK | Helius | Phase 2 |
| Jupiter | Solana SDK | Helius | Phase 2 |
| Lido | Lido API | stETH rebasing | Phase 2 |

**HyperSync is the EVM historical indexing layer.** It replaces The Graph, custom block-scanning, and Envio hosted service. Runs inside Celery workers. $0 cost.

**HyperSync complements (not replaces):** Morpho GraphQL (live state), Aave UiPoolDataProvider (live state), Lido API, DeFiLlama API.

**DEX swap detection:** Index swap events from LI.FI, 1inch, CoW Protocol, Uniswap V2/V3 to detect swaps preceding vault deposits. Contract addresses in `dex_contracts.yaml`, not hardcoded.

**Swap → deposit matching:** Same `tx_hash` matching. Multi-hop swaps use top-level aggregator event. Solana uses Jupiter program instruction parsing from Helius data.

### 💰 P&L Engine Specialist

**When:** Cost basis computation, lot management, yield tracking, position lifecycle, FIFO/WAC calculations.

**This is the north star feature. Every architectural decision serves the P&L engine.**

**Core rules:**
- **Transaction lots are immutable once created.** Append-only. Withdrawals consume lots via FIFO (decrementing `remaining_amount`), never delete rows
- **FIFO is scoped per-position** (same user, same wallet, same vault/market). Lots from other positions are never mixed
- **Both FIFO and WAC** computed from the same lot data. Togglable per view
- **Position lifecycle:** active → closed when `remaining_amount = 0` across all lots. Re-entering a closed position creates a new position record
- **Borrow positions = debt.** Tracked separately, NOT subtracted from portfolio value
- **Yield computation varies by protocol type:**
  - ERC-4626 (Morpho, Yearn): `yield = current_value_of_shares - cost_basis_of_shares`
  - Rebasing (stETH): `yield = current_balance - initial_balance`
  - aTokens (Aave): `yield = current_aToken_balance - deposited_amount`
  - Borrow: `cost = accumulated_debt - original_borrow`
- **Transfer cost basis inheritance:** `transfer_in` lots inherit cost basis from `transfer_out` matched by `tx_hash` + `amount`. Cross-chain bridges: 30-min window matching
- **Price corrections** (§15): update `user_price_usd` and `price_overridden=true`, preserve original in `original_price_usd`
- **reconstruction_status:** `complete` (all auto), `partial` (mixed), `manual` (all manual)

**P&L formulas:**
- Supply: `Net P&L = current_value - cost_basis + yield_earned`
- Borrow: `Net Cost = total_borrow_cost_usd` (always negative contribution)
- Portfolio: aggregates supply P&L and borrow costs separately, `net_yield = total_yield - total_borrow_cost`

**Test with hand-calculated examples:**
```
Deposit 10 ETH @ $2000 → cost basis = $20,000
Deposit 5 ETH @ $2500  → FIFO basis = $32,500, WAC = $2,166.67/ETH
Withdraw 8 ETH @ $3000 → FIFO realised = $8,000
                          Remaining FIFO basis = 2×$2000 + 5×$2500 = $16,500
```

### 🛡️ Risk Engine Specialist

**When:** Risk scoring logic, Credora-inspired methodology, layer computations, alert thresholds.

**The risk engine is deterministic — same inputs MUST produce same outputs. No randomness, no ML, no external scoring APIs.**

**5-layer architecture:**
1. **L1 — Chain Risk (static lookup):** ETH=95, Base=80, Solana=75. Stored in `chain_risk_scores` table
2. **L2 — Protocol Risk (weighted rubric):** Audit quality (30%) + Contract maturity (25%) + Exploit history (20%) + Admin key (15%) + Bug bounty (10%). Stored in `protocol_risk_factors` table. Seeded from §6 tables
3. **L3 — Collateral Risk (computed):** Oracle quality (25%) + Liquidity depth (20%) + Volatility (20%) + Depeg risk (15%) + Concentration (10%) + Maturity (10%)
4. **L4 — Market/Vault Specific (protocol-family variants):** 4A (lending), 4B (Morpho curated), 4C (ERC-4626), 4D (Pendle), 4E (liquid staking), 4F (Gearbox)
5. **L5 — Composite:** `0.35×L1 + 0.30×L2 + 0.20×L3 + 0.15×L4`

**Grade scale (canonical, used everywhere):**
| Range | Grade | Color |
|-------|-------|-------|
| 85–100 | A | Green |
| 70–84 | B | Blue |
| 55–69 | C | Yellow |
| 40–54 | D | Orange |
| 0–39 | F | Red |

**Fallback:** If L4 uncomputable (manual position), use L4=50 and display "L4: Unscored".

**Alert thresholds (V1 = badge/indicator only, V2 = push notifications):**
- Risk score drops >10pts/24h → red badge
- Health factor <1.5 → amber indicator
- Health factor <1.2 → red pulsing indicator
- TVL drops >25%/24h → red 7d Flow badge
- Collateral depeg >2% → amber depeg badge

### 🎨 Frontend Engineer (TypeScript/React)

**When:** Next.js pages, React components, data fetching, charts, responsive design.

**Stack:** Next.js 14+ App Router, TypeScript (strict), Tailwind + shadcn/ui, TanStack Query, TradingView Lightweight Charts, Recharts, Wagmi v2 + Viem, native WebSocket API

**Patterns (mandatory):**
- Server Components by default, `'use client'` only when needed
- TanStack Query for ALL data fetching — no `useEffect` + `fetch`
- API client module (`lib/api.ts`) — single source for all endpoint calls
- Zod for runtime validation of API responses
- shadcn/ui components — don't build custom when shadcn has it
- ESLint + Prettier. Strict TypeScript (`strict: true`)

**Data fetching cadences:**
| Data | Method | Interval |
|------|--------|----------|
| Prices | WebSocket | 30s push |
| Health factors | WebSocket | 15s push |
| Alerts | WebSocket | Event-driven |
| Positions | TanStack Query | 60s poll |
| Vault metrics | TanStack Query | 5 min poll |
| Recommendations | TanStack Query | 5 min poll |

**WebSocket graceful degradation:** If WebSocket fails, fall back to TanStack Query polling (prices 30s, health factors 60s). Show "Live updates unavailable" in footer.

**Error state framework (§18.9):**
- `N/A` = data not available yet (grey). `✕` = computation failed (red)
- Backend returns `{value, status}` envelope. Frontend renders accordingly
- Cascade rules: if cost basis is N/A, unrealised P&L also shows N/A

**Key pages (§18):**
- Dashboard: 4 stat cards + dual donut charts
- Portfolio & P&L: positions table, FIFO/WAC toggle, CSV export
- Position Detail: transaction timeline + P&L chart (TradingView)
- Vault Explorer: full-screen searchable vault table
- Vault Detail: 7-section detail view
- Opportunities: rebalance suggestions + idle asset opportunities
- Historical P&L: date range selector + breakdown table
- Settings/Wallets: wallet management, add wallet flow

### 🔒 Security Engineer

**When:** Auth flows, JWT validation, RLS policies, data isolation, input validation.

**Auth architecture:**
1. User logs in via Next.js → Supabase Auth → receives JWT
2. Next.js sends JWT to FastAPI in `Authorization: Bearer` header
3. FastAPI validates JWT using `SUPABASE_JWT_SECRET` (never call Supabase API from FastAPI)
4. Extracts `user_id` from JWT `sub` claim
5. Sets `SET LOCAL app.current_user_id = '<user_id>'` on DB connection
6. All queries include `WHERE user_id = :current_user_id`

**Two-layer data isolation:**
- **Primary:** Application-level `WHERE user_id` in every query
- **Safety net:** PostgreSQL RLS policies reading `current_setting('app.current_user_id')`

**Celery workers:** `BYPASSRLS` role. Direct DB connection. No JWT, no Supabase.

**Invite-only registration:** `profiles.approved BOOLEAN DEFAULT false`. Admin approves via Telegram notification. First user auto-approved as admin via DB trigger.

**Never expose:** `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`. Frontend uses only the Supabase anon key (public).

### 🧪 QA Engineer

**When:** Writing tests, validating acceptance criteria, regression testing, data integrity checks.

**Testing strategy:**
- **Unit tests:** Every service function. Mock external dependencies. Focus on P&L calculations and risk scoring correctness
- **Integration tests:** Full request → response with `httpx.AsyncClient` + test database
- **Test database:** Separate PostgreSQL instance. Migrations before suite. Truncate between tests (not drop/recreate)

```python
# conftest.py pattern
@pytest.fixture(autouse=True)
async def clean_tables(db_session):
    yield
    for table in reversed(Base.metadata.sorted_tables):
        await db_session.execute(table.delete())
    await db_session.commit()
```

**Critical test scenarios:**
- P&L accuracy: known deposits/withdrawals → expected FIFO/WAC cost basis and realised gains
- Risk score determinism: same inputs → identical composite scores
- Lot consumption: partial withdrawals, multi-lot FIFO ordering
- Transfer cost basis inheritance: same-chain and cross-chain
- Position lifecycle: active → closed → re-opened creates new record
- Reconstruction pipeline: mock HyperSync → lots → positions
- Auth: JWT validation, RLS enforcement, cross-user isolation

### 🚀 DevOps Engineer

**When:** Docker Compose, Nginx, SSL, backup strategies, deployment, monitoring.

**Infrastructure:**
- Docker Compose on Hetzner CPX31 (4 vCPU, 8GB RAM, 160GB SSD, ~$14/mo)
- PostgreSQL 16 + TimescaleDB, Redis 7, FastAPI, Celery (3 workers + beat), Next.js, Nginx
- Nginx with Let's Encrypt SSL. WebSocket upgrade headers configured
- Daily `pg_dump` backup
- Redis: 256MB, allkeys-lru eviction

**Docker Compose services:**
```
postgres   → Port 5432 (internal only)
redis      → Port 6379 (internal only)
backend    → Port 8000 (internal, behind Nginx)
celery-worker-critical → critical queue
celery-worker-default1 → default queue
celery-worker-default2 → default queue
celery-beat → scheduler
frontend   → Port 3000 (internal, behind Nginx)
nginx      → Ports 80, 443 (public)
```

**Environment variables (key groups):**
- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
- `ALCHEMY_API_KEY` — EVM RPC + gas estimation
- `HELIUS_API_KEY` — Solana data
- `ANTHROPIC_API_KEY` — Claude API (via LiteLLM)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` — Admin alerts
- `APP_SECRET_KEY` — Internal signing
- `ENVIRONMENT` — development | staging | production

### 📊 UX/Data Visualization Specialist

**When:** Chart implementations, dashboard layout, data presentation, responsive design.

**Charting stack:**
- **TradingView Lightweight Charts:** P&L chart (§14) with 3 series (position value, cost basis step line, green/red P&L area), deposit/withdrawal markers
- **Recharts:** Simpler visualizations (donut charts, bar charts)
- **shadcn/ui:** All standard UI components (tables, cards, modals, badges)

**Dashboard stat cards (§18.1):** Portfolio Value, Net P&L, Total Yield, Total Debt (hidden if zero borrow positions)

**P&L Chart timeframes:** 1W → hourly aggregates, 1M → 4-hour, 3M/1Y/All → daily continuous aggregates from TimescaleDB

**Risk badges:** Green (A), Blue (B), Yellow (C), Orange (D), Red (F). Consistent everywhere.

**Error states:** N/A (grey) vs ✕ (red). Never show stale data without indicator.

---

## Repository Structure

```
defi-vault/
├── backend/                 # FastAPI application
│   ├── app/
│   │   ├── main.py          # App factory
│   │   ├── config.py        # pydantic-settings
│   │   ├── database.py      # SQLAlchemy async engine + session
│   │   ├── dependencies.py  # Shared FastAPI deps (get_current_user_id, get_db)
│   │   ├── exceptions.py    # Custom exception classes
│   │   ├── models/          # SQLAlchemy ORM models (singular names)
│   │   ├── schemas/         # Pydantic request/response (suffixed names)
│   │   ├── api/             # Route modules (thin!)
│   │   ├── services/        # Business logic layer
│   │   │   ├── pnl/         # P&L engine
│   │   │   ├── risk/        # Risk scoring engine
│   │   │   ├── advisory/    # Rebalancing advisory
│   │   │   └── prices.py    # Price feed service
│   │   ├── adapters/        # Protocol adapters
│   │   │   ├── base.py      # Abstract adapter interface
│   │   │   ├── morpho.py
│   │   │   ├── aave.py
│   │   │   ├── pendle.py
│   │   │   ├── euler.py
│   │   │   └── registry.py  # Adapter discovery
│   │   └── utils/
│   ├── alembic/
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── workers/                 # Celery workers
│   ├── tasks/
│   │   ├── prices.py
│   │   ├── vault_metrics.py
│   │   ├── positions.py
│   │   ├── risk.py
│   │   ├── advisory.py
│   │   ├── reconstruction.py
│   │   └── cost_monitoring.py
│   ├── celeryconfig.py
│   ├── celery_app.py
│   └── Dockerfile
├── frontend/                # Next.js 14 App Router
│   ├── src/
│   │   ├── app/             # App Router pages
│   │   ├── components/      # Shared components
│   │   ├── hooks/           # Custom hooks (useWebSocket, etc.)
│   │   ├── lib/             # Utilities, API client, zod schemas
│   │   ├── providers/       # Context providers
│   │   └── types/           # TypeScript types
│   ├── package.json
│   ├── tailwind.config.ts
│   └── Dockerfile
├── infra/
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   ├── nginx/default.conf
│   └── scripts/
│       ├── backup.sh
│       └── seed.py          # Risk engine static data seeding
├── docs/
├── .env.example
└── CLAUDE.md               # This file
```

---

## Sprint Dependency Graph

```
S01 (Infra) → S02 (Auth) → S03 (Wallets) → S04 (Protocol Adapters I)
                                                      │
                                                      ↓
                                             S05 (P&L Engine)
                                                 │        │
                                                 ↓        ↓
                                        S06 (Frontend)  S08 (Risk Engine)
                                             │              │
                                             ↓              ↓
                                        S07 (Charts)   S09 (Advisory)
                                                            │
                                                            ↓
                                                   S10 (Pendle + Adapters II)
                                                            │
                                                            ↓
                                                   S11 (Manual Entry + Admin)
                                                            │
                                                            ↓
                                                   S12 (WebSocket + Deploy)
```

---

## Critical Invariants — DO NOT VIOLATE

1. **§10 is authoritative for all table schemas.** If any section conflicts, §10 wins
2. **Transaction lots are immutable.** Append-only. Never delete, never update amount
3. **Borrow positions are debt.** Shown separately. Never netted from portfolio value
4. **Risk engine is deterministic.** Same inputs → same outputs. No randomness, no ML
5. **Manual execution only.** No tx building. No automation. Advisory tells user what to do
6. **Budget < $100/month.** Every service/dependency must justify its cost
7. **Two-layer data isolation.** App-level WHERE + RLS safety net. Both must exist
8. **Celery workers bypass RLS.** They use BYPASSRLS role, operate on behalf of the system
9. **net_apy is the canonical APY field.** Used everywhere (advisory, opportunities, UI). apy_gross is for breakdown only
10. **Cost basis from lots, not prices.** Position P&L fields are cached aggregations of their lots — lots are source of truth

---

## Git Workflow

**Branch naming:** `feat/s01-docker-compose`, `fix/s05-fifo-partial-withdrawal`

**Commit messages (conventional commits):**
```
feat(s01): add Docker Compose stack with TimescaleDB
feat(s04): implement Morpho adapter with HyperSync
fix(s05): correct FIFO lot consumption for partial withdrawals
test(s05): add P&L accuracy tests for multi-lot scenarios
```

**PR strategy:** One PR per ticket. Reference Notion ticket URL. List implemented vs deferred.

---

## How to Pick Up a Ticket

1. **Read the ticket** — full description + acceptance criteria
2. **Read referenced spec sections** — §X URLs point to authoritative source. If ticket and spec disagree, spec wins. If §10 and another section disagree, §10 wins
3. **Identify skill role(s)** — which hat(s) does this ticket require?
4. **Build incrementally** — Models first → Service layer → API routes → Tests → Frontend
5. **Verify against spec** — check every field name, enum value, formula after implementation
6. **Run tests** — `pytest` for backend, verify migrations both up and down

---

## Spec Reference Quick Links

| Section | Title | URL |
|---------|-------|-----|
| §1 | Product Vision | `https://www.notion.so/315d81f6e26281caa82affecded0f958` |
| §2 | Decision Log | `https://www.notion.so/315d81f6e26281d0aee4e9710587ee17` |
| §3 | Asset Universe | `https://www.notion.so/315d81f6e2628117afc1e08a1e69713c` |
| §4 | Tech Stack | `https://www.notion.so/315d81f6e26281d9a3cedcd64c057d3d` |
| §5 | P&L Engine | `https://www.notion.so/315d81f6e26281c9b197e21f6f155a41` |
| §5.A | Pendle P&L | `https://www.notion.so/315d81f6e262812da313f363f64b8b43` |
| §6 | Risk Engine | `https://www.notion.so/315d81f6e2628195afa1cdfc95004732` |
| §7 | Rebalancing Advisory | `https://www.notion.so/315d81f6e26281c59a1df164e8eb3c8d` |
| §8 | Notifications (V2) | `https://www.notion.so/315d81f6e2628182ba4fcca03518f521` |
| §9 | Data Sources | `https://www.notion.so/315d81f6e26281a686c5f9df138ddf7e` |
| §10 | System Architecture ⭐ | `https://www.notion.so/315d81f6e26281febbbcf9718d0ce786` |
| §11 | Opportunities Page | `https://www.notion.so/315d81f6e262812394d7dc001ff7d725` |
| §12 | History Reconstruction | `https://www.notion.so/315d81f6e26281e2968fd8003f7ce05a` |
| §13 | Recommendation Lifecycle | `https://www.notion.so/315d81f6e2628118a93be0a26ad0e476` |
| §14 | P&L Chart | `https://www.notion.so/315d81f6e26281adba72c11a1dd840a4` |
| §15 | Manual Entry | `https://www.notion.so/315d81f6e262813b9856e964feea3709` |
| §16 | Alert Configuration (V2) | `https://www.notion.so/315d81f6e262817eb66bf435a0330390` |
| §17 | Historical P&L | `https://www.notion.so/315d81f6e26281e59777ec4d3a446312` |
| §18 | Frontend Screens | `https://www.notion.so/315d81f6e262811d8777db6387dda025` |
| §19 | Auth & Multi-user | `https://www.notion.so/315d81f6e26281c8aac7fb003658c620` |
| §20 | Admin Cost Monitoring | `https://www.notion.so/315d81f6e26281c4993be59f6367c78b` |

---

## Common Pitfalls — Avoid These

1. **Don't add dependencies without justification.** Every pip/npm package must be in the spec or have a strong reason
2. **Don't skip types.** No untyped Python, no `any` in TypeScript
3. **Don't accept code without tests.** Every service function needs at least a happy-path test
4. **Don't hardcode values.** Config comes from env vars or DB config tables, not magic numbers
5. **Don't build V2 features in V1.** Push notifications, Telegram user alerts, Discord — all V2
6. **Don't net borrow from portfolio.** Total debt is a separate metric
7. **Don't call Supabase API from FastAPI.** JWT validation is local (using the secret). Only exception: admin user deletion
8. **Don't split the initial migration.** One Alembic migration for ALL §10 tables (foreign key ordering)
9. **Don't forget continuous aggregates.** Position snapshots need hourly/4-hour/daily aggregates + retention policies
10. **Don't trust Pendle APY triggers.** PT/YT excluded from APY-based triggers. LP treated normally
