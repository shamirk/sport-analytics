# sport-analytics

USPSA analytics application — scrapes member data from USPSA, stores it in PostgreSQL, and serves a FastAPI + Jinja2 dashboard with Chart.js visualizations.

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
                        ┌───────────────▼──────────────┐
                        │     USPSA Website (external)  │
                        │  Playwright + curl_cffi       │
                        └──────────────────────────────┘
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/` | Home page (HTML) |
| `GET` | `/dashboard/{member_number}` | Member dashboard (HTML) |
| `GET` | `/api/member/{member_number}` | Member data JSON |
| `GET` | `/api/member/{member_number}/dashboard` | Dashboard data JSON |
| `GET` | `/api/member/{member_number}/status` | Scrape job status |
| `POST` | `/api/analyze/{member_number}` | Trigger scrape + analysis |

### Example

```bash
# Trigger analysis for a member
curl -X POST http://localhost:8000/api/analyze/A12345

# Poll status
curl http://localhost:8000/api/member/A12345/status

# Get results
curl http://localhost:8000/api/member/A12345
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
    uspsa_scraper.py   # USPSA data scraper (Playwright + curl_cffi)
    analytics_engine.py # Classification analytics
    cache.py           # Redis cache helpers
    task_manager.py    # Background scrape tasks
  models/              # SQLAlchemy ORM models
  templates/           # Jinja2 HTML templates
  static/              # CSS/JS assets
alembic/               # Database migrations
  versions/
    001_initial_schema.py
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

## Deployment

### Render

1. Create a new **Web Service** pointed at this repo.
2. Set **Build Command**: `pip install -e .`
3. Set **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add a **PostgreSQL** database and a **Redis** instance from the Render dashboard.
5. Set environment variables (copy from `.env.example`), using the Render-provided `DATABASE_URL` and `REDIS_URL`.
6. After first deploy, run migrations via the Render shell: `alembic upgrade head`

### Railway

1. Create a new project, add a **GitHub** service pointing at this repo.
2. Add **PostgreSQL** and **Redis** plugins.
3. Set environment variables from `.env.example`, using the Railway-provided connection strings.
4. Railway auto-detects `Dockerfile` — no build command override needed.
5. Run migrations via the Railway shell or add to your start command:
   ```
   alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
