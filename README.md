# sport-analytics

USPSA analytics application — scrapes member data from USPSA and PractiScore, stores it in PostgreSQL, and serves a FastAPI + Jinja2 dashboard with Chart.js visualizations.

Two dashboard tabs:
- **Classifier Stats** — per-division classification percentages, hit factor trends, top/bottom classifiers, statistical summary
- **Match Results** — finish % over time, placement rank over time, match level breakdown, full sortable match history (sourced from PractiScore)

## Requirements

- Docker and Docker Compose
- Python 3.11+ (for local development)

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

The API will be available at http://localhost:8000.
Health check: `curl http://localhost:8000/health`

Run database migrations on first start:

```bash
docker compose exec app alembic upgrade head
```

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Start postgres + redis only
docker compose up postgres redis -d

# Set DATABASE_URL for local postgres
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/uspsa_analytics
export REDIS_URL=redis://localhost:6379/0

alembic upgrade head
uvicorn app.main:app --reload
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL connection string |
| `POSTGRES_USER` | `postgres` | PostgreSQL username (docker-compose only) |
| `POSTGRES_PASSWORD` | `postgres` | PostgreSQL password (docker-compose only) |
| `POSTGRES_DB` | `uspsa_analytics` | PostgreSQL database name (docker-compose only) |
| `SECRET_KEY` | `changeme` | Application secret key |
| `REDIS_URL` | required | Redis connection string |
| `CACHE_TTL` | `86400` | Cache TTL in seconds (24 hours) |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `LOG_LEVEL` | `INFO` | Logging level |

Copy `.env.example` to `.env` and update values before running.

## Testing

The test suite uses SQLite in-memory — no running database or Redis required.

```bash
# One-time setup (if you haven't already)
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_analytics_engine.py

# Run a specific test class or case
pytest tests/test_routes.py::TestGetMember
pytest tests/test_routes.py::TestGetMember::test_returns_404_for_unknown_member
```

The test suite covers:

| Module | File |
|--------|------|
| Input validation | `tests/test_validation.py` |
| Custom exceptions | `tests/test_exceptions.py` |
| TTL cache | `tests/test_cache.py` |
| Analytics engine | `tests/test_analytics_engine.py` |
| USPSA HTML scraper (parsing only) | `tests/test_uspsa_scraper.py` |
| USPSA match list scraper | `tests/test_uspsa_match_scraper.py` |
| PractiScore HTML scraper (parsing only) | `tests/test_practiscore_scraper.py` |
| Background task manager (USPSA) | `tests/test_task_manager.py` |
| Background task manager (PractiScore) | `tests/test_task_manager_practiscore.py` |
| API routes (classifier/dashboard) | `tests/test_routes.py` |
| API routes (PractiScore) | `tests/test_routes_practiscore.py` |

## Architecture

```
                        ┌─────────────────────────────┐
                        │         Client Browser       │
                        └──────────────┬──────────────┘
                                       │ HTTP
                        ┌──────────────▼──────────────┐
                        │      FastAPI Application      │
                        │  ┌─────────┐ ┌────────────┐ │
                        │  │  REST   │ │   Jinja2   │ │
                        │  │   API   │ │ Dashboard  │ │
                        │  └────┬────┘ └─────┬──────┘ │
                        │       │            │        │
                        │  ┌────▼────────────▼──────┐ │
                        │  │    Background Tasks     │ │
                        │  │  (scrape + analytics)   │ │
                        │  └────────────┬────────────┘ │
                        └───────────────┼──────────────┘
                    ┌──────────────────┐│┌──────────────────┐
                    │   PostgreSQL 15  ││ │    Redis 7       │
                    │  (persistent     ││ │  (cache + rate   │
                    │   storage)       ││ │   limiting)      │
                    └──────────────────┘│└──────────────────┘
                                        │
               ┌───────────────▼──────────────┐ ┌────────────────────────────┐
               │     USPSA Website (external)  │ │  PractiScore (external)    │
               │  Playwright + curl_cffi       │ │  Playwright + curl_cffi    │
               └──────────────────────────────┘ └────────────────────────────┘
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/` | Home page (HTML) |
| `GET` | `/dashboard/{member_number}` | Member dashboard (HTML) |
| `GET` | `/api/member/{member_number}` | Member data JSON |
| `GET` | `/api/member/{member_number}/dashboard` | Classifier dashboard data JSON |
| `GET` | `/api/member/{member_number}/status` | USPSA scrape job status |
| `POST` | `/api/analyze/{member_number}` | Trigger USPSA scrape + analysis |
| `POST` | `/api/analyze/{member_number}/practiscore` | Trigger PractiScore match scrape |
| `GET` | `/api/member/{member_number}/practiscore` | PractiScore match results JSON |

### Example

```bash
# Trigger USPSA classifier analysis
curl -X POST http://localhost:8000/api/analyze/A12345

# Poll status
curl http://localhost:8000/api/member/A12345/status

# Get classifier results
curl http://localhost:8000/api/member/A12345/dashboard

# Trigger PractiScore match history scrape
curl -X POST http://localhost:8000/api/analyze/A12345/practiscore

# Get PractiScore match results
curl http://localhost:8000/api/member/A12345/practiscore
```

## Project Structure

```
app/
  main.py              # FastAPI entrypoint
  database.py          # SQLAlchemy engine/session
  limiter.py           # Rate limiting (slowapi)
  logging_config.py    # structlog configuration
  exceptions.py        # Structured error handlers
  validation.py        # Input validation helpers
  routes/
    health.py          # GET /health
    members.py         # /api/member/* endpoints
    pages.py           # HTML dashboard pages
  services/
    uspsa_scraper.py        # USPSA classifier scraper (Playwright + curl_cffi)
    uspsa_match_scraper.py  # Derives match list from classifier data
    practiscore_scraper.py  # PractiScore match results scraper
    analytics_engine.py     # Classification analytics (pandas/scipy)
    cache.py                # In-memory TTL cache
    task_manager.py         # Background scrape tasks (USPSA + PractiScore)
  models/              # SQLAlchemy ORM models
  templates/           # Jinja2 HTML templates
  static/              # CSS/JS assets
alembic/               # Database migrations
  versions/
    001_initial_schema.py   # members, divisions, classifications, classifier_results
    002_practiscore_tables.py # practiscore_matches, practiscore_results
Dockerfile
docker-compose.yml
pyproject.toml
```

## Database Migrations

```bash
# Apply all migrations
alembic upgrade head

# Create a new migration (auto-detect model changes)
alembic revision --autogenerate -m "description"

# Rollback one step
alembic downgrade -1
```

## Security

The following security measures are in place:

- **XSS**: All scraped data rendered in `dashboard.html` is escaped via `escapeHtml()` before insertion into the DOM.
- **SSRF**: `practiscore_scraper.py` validates URLs against a `practiscore.com` allowlist before fetching.
- **Docker network**: PostgreSQL and Redis ports are no longer published on `0.0.0.0`; Redis requires a password (`REDIS_PASSWORD`).
- **Credentials**: `.env.example` uses unambiguous angle-bracket placeholders (e.g. `<set-strong-password>`) — none of the defaults are valid credentials.
- **Rate limiting**: The rate-limiter key function is `X-Forwarded-For`-aware to prevent bypass behind a proxy.
- **Security headers**: Responses include `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, and `Referrer-Policy`.
- **SRI**: CDN-loaded scripts carry `integrity` hashes to prevent tampering.
- **Playwright concurrency**: `asyncio.Semaphore` limits concurrent browser instances to 3.
- **Error handling**: The job-status API returns sanitized messages only — internal errors are not leaked to clients.
- **Dependencies**: `uv` lockfile committed for reproducible installs; `psycopg2-binary` replaced with `psycopg2`.

## Deployment

### Before you start

Copy `.env.example` to `.env` and replace **every** angle-bracket placeholder with a real value before running:

```bash
cp .env.example .env
# Edit .env — replace <set-strong-password>, <generate-with-openssl-rand-hex-32>, etc.
openssl rand -hex 32   # generate a value for SECRET_KEY
```

### Render

1. Create a new **Web Service** pointed at this repo.
2. Set **Build Command**: `pip install -e .`
3. Set **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add a **PostgreSQL** database and a **Redis** instance from the Render dashboard.
5. Set environment variables (copy from `.env.example`), replacing all placeholders with the Render-provided `DATABASE_URL`, `REDIS_URL`, and a generated `SECRET_KEY`.
6. After first deploy, run migrations via the Render shell: `alembic upgrade head`

### Railway

1. Create a new project, add a **GitHub** service pointing at this repo.
2. Add **PostgreSQL** and **Redis** plugins.
3. Set environment variables from `.env.example`, replacing all placeholders with the Railway-provided connection strings and a generated `SECRET_KEY`.
4. Railway auto-detects `Dockerfile` — no build command override needed.
5. Run migrations via the Railway shell or add to your start command:
   ```
   alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
