# Phase 6 — Production

> **Status:** not started
> **Depends on:** Phases 3–5
> **Goal:** something you can deploy, operate, and hand to a client.

Phase 6 is not about features. It is about the difference between
*"it works on my machine"* and *"it runs, and when it breaks at 2am
someone can fix it."*

For a client project this phase is not optional — it is most of what
they are actually paying for.

---

## Target topology

```text
                        Internet
                            │
                            ▼
                     Reverse proxy
                  (nginx / Caddy / ALB)
                     TLS termination
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
        ┌───────────┐              ┌───────────┐
        │  Next.js  │              │  FastAPI  │  × N
        │  frontend │              │  backend  │
        └───────────┘              └─────┬─────┘
                                         │
                      ┌──────────────────┼──────────────────┐
                      ▼                  ▼                  ▼
                ┌──────────┐      ┌───────────┐      ┌───────────┐
                │   MCP    │      │ PostgreSQL│      │   Redis   │
                │  server  │      │           │      │           │
                └──────────┘      └───────────┘      └───────────┘
                                        │
                                   ┌────▼────┐
                                   │ backups │
                                   └─────────┘
```

---

# Phase 6.1 — Configuration

Twelve-factor: **config comes from the environment, never from code.**

```text
config/
├── settings.py          shared (exists)
├── api_settings.py      backend
└── .env.example         COMMITTED - documents every variable
```

## `.env.example` is a deliverable

A client's new developer must be able to run the project from the
repository alone. That file is the contract.

```bash
# ---- Application ----
MCP_ENVIRONMENT=development        # development | staging | production
MCP_LOG_LEVEL=INFO

# ---- Database ----
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/agenthub

# ---- Redis ----
REDIS_URL=redis://localhost:6379/0

# ---- Security ----
JWT_SECRET_KEY=                    # openssl rand -hex 32
CREDENTIAL_ENCRYPTION_KEY=         # Fernet.generate_key()
ALLOWED_ORIGINS=http://localhost:3000

# ---- LLM ----
OLLAMA_MODEL=minimax-m3:cloud
OLLAMA_HOST=http://localhost:11434

# ---- OAuth (Phase 5) ----
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
SLACK_OAUTH_CLIENT_ID=
SLACK_OAUTH_CLIENT_SECRET=
```

## Fail fast on missing config

```python
def validate_settings() -> None:
    """
    Refuse to start with a broken configuration.

    A server that boots and then fails on the first real request is
    far worse than one that never boots: the first looks healthy to
    your load balancer and takes traffic.
    """

    if settings.environment == "production":
        if not settings.jwt_secret_key:
            raise ConfigurationError("JWT_SECRET_KEY is required")

        if "*" in settings.allowed_origins:
            raise ConfigurationError("CORS wildcard is not allowed")

        if settings.log_level == "DEBUG":
            raise ConfigurationError("DEBUG logging leaks data")
```

> Lesson already learned in this project: Phase 2 debugging traced a
> 22-second failure to a **relative** path in `.env` resolving against
> the wrong working directory. `config/settings.py:resolve_path()`
> exists because of it. Validate paths at startup, not on first use.

---

# Phase 6.2 — Docker

```text
docker/
├── Dockerfile.api
├── Dockerfile.mcp
├── Dockerfile.frontend
└── nginx.conf

docker-compose.yml           local development
docker-compose.prod.yml      production overrides
```

## Multi-stage, non-root

```dockerfile
# Dockerfile.api
FROM python:3.14-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.14-slim
# Never run as root. A container escape starts from whatever user the
# process has.
RUN useradd --create-home --shell /bin/false appuser
WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local
COPY --chown=appuser:appuser . .
USER appuser
ENV PATH=/home/appuser/.local/bin:$PATH

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Compose

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: agenthub
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes: [redisdata:/data]

  api:
    build: { context: ., dockerfile: docker/Dockerfile.api }
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_started }
    env_file: .env

  mcp:
    build: { context: ., dockerfile: docker/Dockerfile.mcp }
    env_file: .env

  frontend:
    build: { context: ./frontend, dockerfile: ../docker/Dockerfile.frontend }
    depends_on: [api]

volumes:
  pgdata:
  redisdata:
```

## `.dockerignore` is a security control

```text
.env
*.json          # credentials.json, token.json
venv/
.git/
__pycache__/
```

Without this you ship your OAuth tokens inside the image.

---

# Phase 6.3 — Database in production

## Migrations run before the app starts

```text
deploy
  ↓
run `alembic upgrade head`   ← one process, not N
  ↓
start / roll the API containers
```

Never run migrations from application startup — N containers racing to
migrate the same database is a corruption incident.

## Connection pooling

```python
engine = create_async_engine(
    settings.database_url,
    pool_size=10,          # per worker process
    max_overflow=5,
    pool_pre_ping=True,    # detect connections killed by the network
    pool_recycle=1800,     # under typical idle timeouts
)
```

```text
total connections = workers × (pool_size + max_overflow)

4 workers × 15 = 60      ← must stay under Postgres max_connections
```

Get this wrong and the app dies under load with `too many connections`
— always after the demo, never during it.

## Backups

```text
DAILY     full dump, retained 30 days
HOURLY    WAL archiving (point-in-time recovery)
WEEKLY    RESTORE TEST  ← an untested backup is not a backup
OFFSITE   a different provider or region
```

Write down and measure:

```text
RPO  how much data may we lose?      target: < 1 hour
RTO  how long may recovery take?     target: < 4 hours
```

---

# Phase 6.4 — Redis

```text
USE                     WHY
─────────────────────   ────────────────────────────────────────
rate limiting           shared counters across workers
session / refresh       fast revocation lists
tool result cache       repeat reads within a turn
approval events         cross-worker signalling (Phase 5)
job queue               background work
```

## Approval across workers

Phase 5's `asyncio.Event` only works inside one process. With N
workers, the approval POST can land on a different worker than the one
holding the turn.

```text
worker 1 holds the turn         worker 3 receives the approval
        │                                   │
        │  SUBSCRIBE approval:apr_9x8        │
        │◄───────────────────────────────────│ PUBLISH approval:apr_9x8
        ▼
    continue
```

Redis pub/sub fixes it in a few lines. Until then, **pin to one worker
and know that you have.**

## Cache with intent

```text
CACHE        tool catalogue, plugin metadata, user profile
NEVER CACHE  tool results across turns (stale data → wrong answers),
             permission decisions (revocation must be instant),
             anything credential-shaped
```

---

# Phase 6.5 — Observability

## Structured logs, not strings

```python
logger.info(
    "tool_executed",
    extra={
        "request_id": request_id,
        "user_id": user.id,
        "agent_id": agent.id,
        "execution_id": record.execution_id,
        "tool": record.tool_name,
        "status": record.status.value,
        "duration_ms": record.duration_ms,
        "attempts": record.attempts,
    },
)
```

JSON logs are queryable. `f"Tool {name} took {ms}ms"` is not.

> Every field above already exists on `ExecutionRecord`. Phase 2.8 was
> built for this moment.

## Correlation IDs

```text
request_id     one HTTP request
conversation_id one chat thread
turn_id        one user message and everything it caused
execution_id   one tool call            ← already exists
```

Thread `request_id` through every log line. Return it in every
response. A user report becomes one query.

## Metrics worth having on day one

```text
COUNTERS
  http_requests_total{method, path, status}
  tool_executions_total{tool, namespace, status}
  approvals_total{outcome}
  auth_attempts_total{outcome}

HISTOGRAMS
  http_request_duration_seconds
  tool_execution_duration_seconds{tool}
  agent_turn_duration_seconds
  llm_tokens_per_turn

GAUGES
  db_pool_in_use
  mcp_sessions_active
  approvals_pending
```

## Alert on symptoms, not causes

```text
ALERT                                  WHY
────────────────────────────────────   ──────────────────────────
tool failure rate > 10% for 5 min      an integration is broken
p95 turn duration > 30s                users are giving up
5xx rate > 1%                          we are broken
db_pool_in_use > 80%                   about to fall over
auth failures spike                    credential stuffing
approvals_pending growing              nobody is being notified
```

Do not alert on CPU. Alert on what users feel.

## Health endpoints

```text
GET /health     liveness  - is the process alive?  (no dependencies)
GET /ready      readiness - can it serve traffic?  (db, redis, mcp)
```

They are different. If `/health` checks the database, a brief database
blip makes your orchestrator kill every container — turning a small
outage into a total one.

---

# Phase 6.6 — CI/CD

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: postgres }
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install -r requirements.txt
      - run: ruff check .
      - run: mypy agent api
      - run: alembic upgrade head
      - run: pytest --cov=agent --cov=api --cov-fail-under=70
      - run: pip-audit

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20", cache: npm }
      - run: npm ci
      - run: npm run lint && npm run typecheck && npm run build
      - run: npx playwright install --with-deps && npm run test:e2e
```

## Pipeline order matters

```text
lint (seconds) → types (seconds) → unit (a minute) → integration → e2e
```

Fastest first. A developer should learn about a formatting error in
15 seconds, not after an 8-minute E2E suite.

## Deployment

```text
1. build and tag images with the commit SHA
2. push to a registry
3. run migrations (once)
4. rolling update, one instance at a time
5. wait for /ready on each
6. smoke test
7. roll back automatically on failure
```

Tag with the SHA, never `latest`. `latest` makes "which version is
running?" unanswerable — the question you always need answered during
an incident.

---

# Phase 6.7 — Performance

## Measure before optimising

```text
LAYER              BUDGET      HOW TO CHECK
────────────────   ─────────   ───────────────────────────
routing            < 15 ms     RoutingDecision.duration_ms
db query           < 50 ms     slow query log
tool execution     < 2 s       ExecutionRecord.duration_ms
LLM turn           < 10 s      turn metrics
total (1 tool)     < 15 s      end to end
```

Phase 2 measured routing at **3–9 ms** on 61 tools. That is already
within budget and not where to look first.

## Where the time actually goes

```text
LLM inference       70-85%     ← the real cost
tool API calls      10-25%
your code           < 2%
routing             < 0.1%
```

**Do not micro-optimise Python here.** The wins are: fewer tool
rounds, fewer tools in the prompt (Phase 2.3 already), a faster model,
and parallel tool calls.

## Load testing

```text
k6 / Locust

Scenarios:
  10 concurrent users, 5-minute conversations
  100 concurrent users, mostly reads
  1 user, 50-message conversation (context growth)
  burst: 50 users arriving at once

Watch:
  db_pool_in_use, mcp_sessions_active, p95 turn duration, error rate
```

---

# Phase 6.8 — Operations

## Runbooks — write them before you need them

```text
docs/runbooks/
├── deploy.md
├── rollback.md
├── database-restore.md
├── oauth-token-expiry.md
├── mcp-server-down.md
└── incident-response.md
```

Each one: symptoms → diagnosis → fix → prevention.

For a client project these are a deliverable. Software they cannot
operate without you is not finished software.

## What to hand over

```text
✓ README with local setup that actually works from a clean machine
✓ .env.example documenting every variable
✓ Architecture diagram
✓ phases_doc/ (this folder)
✓ devloper_docs/Understand_Phse2.md
✓ API docs (FastAPI generates OpenAPI - keep the descriptions good)
✓ Runbooks
✓ Backup and restore procedure, tested
✓ Access to registry, hosting, DNS, OAuth apps
✓ A walkthrough session, recorded
```

---

# One thing I would NOT build yet

```text
❌ Kubernetes          Compose or a PaaS is enough until real scale
❌ Microservices       one API is fine
❌ Multi-region        one region, good backups
❌ Custom autoscaling  your platform has it
❌ Service mesh        no
❌ Feature flags       unless you need them
```

Every one of these adds operational burden. Add them when a specific
problem demands it, not in advance.

---

# Phase 6 milestone

### Test 1 — clean machine, one command

```bash
git clone <repo> && cd agent-hub
cp .env.example .env      # fill in secrets
docker compose up
```

Working app at `localhost:3000`. **No undocumented step.**

### Test 2 — CI gates merges

Open a PR with a failing test. It must be blocked.

### Test 3 — deploy and roll back

Deploy a change. Roll it back. Both under 10 minutes, no data loss.

### Test 4 — restore from backup

Restore yesterday's backup into a scratch database and confirm the
data. **An untested backup is not a backup.**

### Test 5 — survive a dependency outage

```text
stop postgres  → /ready fails, /health passes, clear 503s
stop redis     → degraded, not down
stop mcp       → chat fails cleanly, the rest of the app works
stop ollama    → clear error, history intact
```

Nothing should hang or return a stack trace to the user.

### Test 6 — an incident is diagnosable

Break something deliberately. Using only logs and metrics, find it in
under 5 minutes.

### Test 7 — load

50 concurrent users for 10 minutes: error rate < 1%, p95 within
budget, no connection exhaustion, memory flat.

**If all seven pass, Phase 6 is genuinely done and the project is
deliverable.**

---

## 🎯 Your next immediate task

```text
1.  .env.example + settings validation
        ↓
2.  Dockerfiles (api, mcp, frontend)
        ↓
3.  docker-compose for local development
        ↓
4.  /health and /ready
        ↓
5.  Structured JSON logging + request IDs
        ↓
6.  CI: lint, types, tests
        ↓
7.  Alembic in the deploy pipeline
        ↓
8.  Redis: rate limiting + approval pub/sub
        ↓
9.  Prometheus metrics
        ↓
10. Backups + a tested restore
        ↓
11. Production compose / hosting
        ↓
12. CD with rollback
        ↓
13. Load test
        ↓
14. Runbooks
        ↓
15. Handover
```

> **Step 10 before step 11.** Never put real user data somewhere you
> have not proven you can restore from.
