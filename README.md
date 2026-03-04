# sport-analytics

USPSA analytics application — FastAPI backend with PostgreSQL and Redis.

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

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Project Structure

```
app/
  main.py              # FastAPI entrypoint
  database.py          # SQLAlchemy engine/session
  routes/              # API route modules
  services/
    uspsa_scraper.py   # USPSA data scraper (Playwright + curl_cffi)
    analytics_engine.py # Classification analytics
  models/              # SQLAlchemy ORM models
  templates/           # Jinja2 templates
  static/              # CSS/JS assets
alembic/               # Database migrations
Dockerfile
docker-compose.yml
pyproject.toml
```

## Database Migrations

```bash
alembic upgrade head
alembic revision --autogenerate -m "description"
```
