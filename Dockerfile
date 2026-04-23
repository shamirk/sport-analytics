FROM python:3.11-slim

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

ENV UV_SYSTEM_PYTHON=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

RUN uv run playwright install chromium --with-deps && chmod -R o+rx /ms-playwright

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
RUN uv sync --frozen

RUN useradd --system --no-create-home appuser && chown -R appuser /code
USER appuser

EXPOSE 8000

CMD ["/code/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
