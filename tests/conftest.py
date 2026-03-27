"""Shared test fixtures and database setup.

Sets up an in-memory SQLite database before any app modules are imported so that
``app.database.engine`` points to SQLite rather than the Postgres URL in .env.
"""
import os

# Must be set before any app imports so app.database picks up SQLite.
os.environ["DATABASE_URL"] = "sqlite://"

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Patch app.database to use a shared in-memory SQLite engine.
import app.database as _db_module  # noqa: E402

_TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TEST_SESSION_FACTORY = sessionmaker(
    autocommit=False, autoflush=False, bind=_TEST_ENGINE
)
_db_module.engine = _TEST_ENGINE
_db_module.SessionLocal = _TEST_SESSION_FACTORY

# Register all models and create tables once for the session.
import app.models  # noqa: F401, E402
from app.database import Base  # noqa: E402

Base.metadata.create_all(bind=_TEST_ENGINE)

# Import app *after* patching so routes use the patched SessionLocal.
from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402
from app.services.cache import cache  # noqa: E402
import app.services.task_manager as _task_manager  # noqa: E402


# ---------------------------------------------------------------------------
# Auto-reset global singletons between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_globals():
    """Clear shared in-memory state so tests don't bleed into each other."""
    cache.clear()
    _task_manager.job_status.clear()
    # Truncate all tables between tests
    with _TEST_ENGINE.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.commit()
    yield
    cache.clear()
    _task_manager.job_status.clear()


# ---------------------------------------------------------------------------
# Database session fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    session = _TEST_SESSION_FACTORY()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# FastAPI test client with DB override
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
