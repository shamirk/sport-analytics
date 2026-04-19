"""Unit tests for app.services.task_manager."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import app.services.task_manager as task_manager
from app.services.task_manager import (
    create_job,
    get_pending_job,
    _find_pending_job,
    _public_error_message,
    _cleanup_expired_jobs,
)


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------


class TestCreateJob:
    def test_returns_uuid_string(self):
        job_id = create_job("A12345")
        assert isinstance(job_id, str)
        assert len(job_id) == 36  # UUID4 format

    def test_job_stored_as_pending(self):
        job_id = create_job("A12345")
        assert task_manager.job_status[job_id]["status"] == "pending"

    def test_job_stores_member_number(self):
        job_id = create_job("B99999")
        assert task_manager.job_status[job_id]["member_number"] == "B99999"

    def test_multiple_jobs_are_independent(self):
        id1 = create_job("A11111")
        id2 = create_job("B22222")
        assert id1 != id2
        assert task_manager.job_status[id1]["member_number"] == "A11111"
        assert task_manager.job_status[id2]["member_number"] == "B22222"

    def test_initial_fields_are_none(self):
        job_id = create_job("X12345")
        job = task_manager.job_status[job_id]
        assert job["started_at"] is None
        assert job["completed_at"] is None
        assert job["error_public"] is None
        assert job["error_internal"] is None


# ---------------------------------------------------------------------------
# get_pending_job / _find_pending_job
# ---------------------------------------------------------------------------


class TestGetPendingJob:
    def test_returns_none_when_no_jobs(self):
        assert get_pending_job("A12345") is None

    def test_returns_job_id_for_pending(self):
        job_id = create_job("A12345")
        assert get_pending_job("A12345") == job_id

    def test_ignores_in_progress_jobs(self):
        job_id = create_job("A12345")
        task_manager.job_status[job_id]["status"] = "in_progress"
        assert get_pending_job("A12345") is None

    def test_ignores_complete_jobs(self):
        job_id = create_job("A12345")
        task_manager.job_status[job_id]["status"] = "complete"
        assert get_pending_job("A12345") is None

    def test_ignores_error_jobs(self):
        job_id = create_job("A12345")
        task_manager.job_status[job_id]["status"] = "error"
        assert get_pending_job("A12345") is None

    def test_returns_none_for_different_member(self):
        create_job("A12345")
        assert get_pending_job("B99999") is None


# ---------------------------------------------------------------------------
# scrape_and_store
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """A minimal mock of a SQLAlchemy session."""
    session = MagicMock()
    member_mock = MagicMock()
    member_mock.id = 1
    # query(...).filter(...).first() returns None (new member)
    session.query.return_value.filter.return_value.first.return_value = None
    session.flush = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    return session


_MOCK_SCRAPE_DATA = {
    "current_classifications": [
        {"division": "Limited", "class": "B", "percentage": 72.5},
    ],
    "classifier_scores": [
        {
            "division": "Limited",
            "classifier": "99-11",
            "date": "01/15/26",
            "club": "Test Club",
            "hit_factor": 7.0,
            "percentage": 72.5,
            "used": "Y",
        }
    ],
}


class TestScrapeAndStore:
    @pytest.mark.asyncio
    async def test_success_marks_job_complete(self, mock_db):
        job_id = create_job("A12345")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "complete"

    @pytest.mark.asyncio
    async def test_success_sets_completed_at(self, mock_db):
        job_id = create_job("A12345")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_member_not_found_sets_error_status(self, mock_db):
        from app.services.uspsa_scraper import MemberNotFoundError

        job_id = create_job("Z99999")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(
                side_effect=MemberNotFoundError("Z99999")
            )

            await task_manager.scrape_and_store("Z99999", mock_db)

        assert task_manager.job_status[job_id]["status"] == "error"
        assert "Z99999" in task_manager.job_status[job_id]["error_public"]

    @pytest.mark.asyncio
    async def test_generic_exception_sets_error_status(self, mock_db):
        job_id = create_job("A12345")
        mock_db.commit.side_effect = RuntimeError("DB crash")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "error"
        assert "DB crash" in task_manager.job_status[job_id]["error_internal"]
        assert task_manager.job_status[job_id]["error_public"] == "An unexpected error occurred"

    @pytest.mark.asyncio
    async def test_generic_exception_calls_rollback(self, mock_db):
        create_job("A12345")
        mock_db.commit.side_effect = RuntimeError("DB crash")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        mock_db.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_pending_job_returns_early(self, mock_db):
        """If there's no pending job for the member, scrape_and_store exits without calling scraper."""
        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            # No job created → should return immediately without calling scraper
            await task_manager.scrape_and_store("NOJOB", mock_db)

        mock_instance.scrape_member.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_set_on_success(self, mock_db):
        from app.services.cache import cache

        job_id = create_job("A12345")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        assert cache.get("analyze:A12345") == _MOCK_SCRAPE_DATA

    @pytest.mark.asyncio
    async def test_db_integrity_error_sets_safe_public_message(self, mock_db):
        from sqlalchemy.exc import IntegrityError

        job_id = create_job("A12345")
        mock_db.commit.side_effect = IntegrityError("stmt", {}, Exception("unique violation"))

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "error"
        assert task_manager.job_status[job_id]["error_public"] == "Data processing error, please retry"
        assert "unique violation" in task_manager.job_status[job_id]["error_internal"]

    @pytest.mark.asyncio
    async def test_scraping_error_sets_scraping_public_message(self, mock_db):
        from app.exceptions import ScrapingError

        job_id = create_job("A12345")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(
                side_effect=ScrapingError("Cloudflare blocked", status_code=403)
            )

            await task_manager.scrape_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "error"
        assert task_manager.job_status[job_id]["error_public"] == "Scraping failed, please retry"

    @pytest.mark.asyncio
    async def test_timeout_error_sets_timeout_public_message(self, mock_db):
        job_id = create_job("A12345")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(side_effect=TimeoutError("request timed out"))

            await task_manager.scrape_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "error"
        assert task_manager.job_status[job_id]["error_public"] == "Request timed out, please retry"

    @pytest.mark.asyncio
    async def test_error_internal_never_surfaces_in_public(self, mock_db):
        """Sensitive details in the exception must not appear in error_public."""
        job_id = create_job("A12345")
        secret_detail = "DETAIL: Key (member_number)=(A12345) already exists."
        mock_db.commit.side_effect = RuntimeError(secret_detail)

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        assert secret_detail not in task_manager.job_status[job_id]["error_public"]
        assert secret_detail in task_manager.job_status[job_id]["error_internal"]


# ---------------------------------------------------------------------------
# _public_error_message
# ---------------------------------------------------------------------------


class TestPublicErrorMessage:
    def test_sqlalchemy_integrity_error(self):
        from sqlalchemy.exc import IntegrityError
        exc = IntegrityError("stmt", {}, Exception("unique violation"))
        assert _public_error_message(exc) == "Data processing error, please retry"

    def test_scraping_error(self):
        from app.exceptions import ScrapingError
        exc = ScrapingError("Cloudflare blocked", status_code=403)
        assert _public_error_message(exc) == "Scraping failed, please retry"

    def test_rate_limit_error(self):
        from app.exceptions import RateLimitError
        exc = RateLimitError("rate limited")
        assert _public_error_message(exc) == "Scraping failed, please retry"

    def test_timeout_error(self):
        assert _public_error_message(TimeoutError("timed out")) == "Request timed out, please retry"

    def test_generic_runtime_error(self):
        assert _public_error_message(RuntimeError("DB crash")) == "An unexpected error occurred"

    def test_generic_value_error(self):
        assert _public_error_message(ValueError("bad value")) == "An unexpected error occurred"


# ---------------------------------------------------------------------------
# _cleanup_expired_jobs
# ---------------------------------------------------------------------------


class TestCleanupExpiredJobs:
    def test_removes_complete_jobs_older_than_ttl(self):
        job_id = create_job("A12345")
        task_manager.job_status[job_id]["status"] = "complete"
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=task_manager._JOB_TTL_SECONDS + 1)).isoformat()
        task_manager.job_status[job_id]["completed_at"] = old_ts

        _cleanup_expired_jobs()

        assert job_id not in task_manager.job_status

    def test_removes_error_jobs_older_than_ttl(self):
        job_id = create_job("A12345")
        task_manager.job_status[job_id]["status"] = "error"
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=task_manager._JOB_TTL_SECONDS + 1)).isoformat()
        task_manager.job_status[job_id]["completed_at"] = old_ts

        _cleanup_expired_jobs()

        assert job_id not in task_manager.job_status

    def test_keeps_recent_terminal_jobs(self):
        job_id = create_job("A12345")
        task_manager.job_status[job_id]["status"] = "complete"
        task_manager.job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()

        _cleanup_expired_jobs()

        assert job_id in task_manager.job_status

    def test_keeps_pending_jobs_regardless_of_age(self):
        job_id = create_job("A12345")
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=task_manager._JOB_TTL_SECONDS + 1)).isoformat()
        task_manager.job_status[job_id]["completed_at"] = old_ts

        _cleanup_expired_jobs()

        assert job_id in task_manager.job_status

    def test_cleanup_called_on_create_job(self):
        job_id = create_job("A12345")
        task_manager.job_status[job_id]["status"] = "complete"
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=task_manager._JOB_TTL_SECONDS + 1)).isoformat()
        task_manager.job_status[job_id]["completed_at"] = old_ts

        create_job("B99999")  # triggers cleanup

        assert job_id not in task_manager.job_status
