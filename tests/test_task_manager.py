"""Unit tests for app.services.task_manager."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.services.task_manager as task_manager
from app.services.task_manager import create_job, get_pending_job, _find_pending_job


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
        assert job["error"] is None


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
        assert "Z99999" in task_manager.job_status[job_id]["error"]

    @pytest.mark.asyncio
    async def test_generic_exception_sets_error_status(self, mock_db):
        job_id = create_job("A12345")
        mock_db.commit.side_effect = RuntimeError("DB crash")

        with patch("app.services.uspsa_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            await task_manager.scrape_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "error"
        assert "DB crash" in task_manager.job_status[job_id]["error"]

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
