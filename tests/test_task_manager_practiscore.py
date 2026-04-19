"""Unit tests for scrape_practiscore_and_store and updated job helpers."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import app.services.task_manager as task_manager
from app.services.task_manager import create_job, get_pending_job


# ---------------------------------------------------------------------------
# create_job / get_pending_job with job_type
# ---------------------------------------------------------------------------


class TestCreateJobWithType:
    def test_default_type_is_uspsa(self):
        job_id = create_job("A12345")
        assert task_manager.job_status[job_id]["job_type"] == "uspsa"

    def test_practiscore_type_stored(self):
        job_id = create_job("A12345", job_type="practiscore")
        assert task_manager.job_status[job_id]["job_type"] == "practiscore"

    def test_uspsa_and_practiscore_jobs_independent(self):
        uspsa_id = create_job("A12345", job_type="uspsa")
        ps_id = create_job("A12345", job_type="practiscore")
        assert uspsa_id != ps_id
        assert task_manager.job_status[uspsa_id]["job_type"] == "uspsa"
        assert task_manager.job_status[ps_id]["job_type"] == "practiscore"


class TestGetPendingJobWithType:
    def test_finds_pending_practiscore_job(self):
        job_id = create_job("A12345", job_type="practiscore")
        assert get_pending_job("A12345", job_type="practiscore") == job_id

    def test_does_not_find_uspsa_job_for_practiscore_type(self):
        create_job("A12345", job_type="uspsa")
        assert get_pending_job("A12345", job_type="practiscore") is None

    def test_does_not_find_practiscore_job_for_uspsa_type(self):
        create_job("A12345", job_type="practiscore")
        assert get_pending_job("A12345", job_type="uspsa") is None

    def test_default_type_is_uspsa(self):
        job_id = create_job("A12345")  # default uspsa
        assert get_pending_job("A12345") == job_id  # default uspsa

    def test_ignores_non_pending_status(self):
        job_id = create_job("A12345", job_type="practiscore")
        task_manager.job_status[job_id]["status"] = "complete"
        assert get_pending_job("A12345", job_type="practiscore") is None


# ---------------------------------------------------------------------------
# scrape_practiscore_and_store
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.all.return_value = []
    session.flush = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    return session


_MOCK_MATCHES = [
    {
        "match_name": "Spring Steel 2024",
        "match_date": "2024-03-15",
        "division": "Limited",
        "match_level": 2,
        "practiscore_match_id": "abc-111",
        "source_url": "https://practiscore.com/results/new/abc-111",
        "total_competitors": 30,
        "member_placement": 5,
        "member_percent": 84.5,
        "results": [
            {
                "shooter_name": "Alice Smith",
                "member_number": "A11111",
                "division": "Limited",
                "classification": "A",
                "total_points": 520.5,
                "total_time": None,
                "percent_of_winner": 100.0,
                "placement": 1,
                "is_queried_member": False,
            },
            {
                "shooter_name": "Bob Jones",
                "member_number": "A12345",
                "division": "Limited",
                "classification": "B",
                "total_points": 450.0,
                "total_time": None,
                "percent_of_winner": 84.5,
                "placement": 5,
                "is_queried_member": True,
            },
        ],
    }
]


class TestScrapePractiscoreAndStore:
    @pytest.mark.asyncio
    async def test_success_marks_job_complete(self, mock_db):
        job_id = create_job("A12345", job_type="practiscore")

        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "complete"

    @pytest.mark.asyncio
    async def test_success_sets_completed_at(self, mock_db):
        job_id = create_job("A12345", job_type="practiscore")

        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_db_commit_called(self, mock_db):
        create_job("A12345", job_type="practiscore")

        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("A12345", mock_db)

        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_deleted_on_success(self, mock_db):
        from app.services.cache import cache

        cache.set("practiscore:A12345", {"old": "data"})
        create_job("A12345", job_type="practiscore")

        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("A12345", mock_db)

        assert cache.get("practiscore:A12345") is None

    @pytest.mark.asyncio
    async def test_exception_sets_error_status(self, mock_db):
        job_id = create_job("A12345", job_type="practiscore")
        mock_db.commit.side_effect = RuntimeError("DB failure")

        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("A12345", mock_db)

        assert task_manager.job_status[job_id]["status"] == "error"
        assert "DB failure" in task_manager.job_status[job_id]["error_internal"]
        assert task_manager.job_status[job_id]["error_public"] == "An unexpected error occurred"

    @pytest.mark.asyncio
    async def test_exception_calls_rollback(self, mock_db):
        create_job("A12345", job_type="practiscore")
        mock_db.commit.side_effect = RuntimeError("DB failure")

        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("A12345", mock_db)

        mock_db.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_pending_job_returns_early(self, mock_db):
        """With no pending practiscore job, the function should not call the scraper."""
        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("NOJOB", mock_db)

        mock_scrape.assert_not_called()

    @pytest.mark.asyncio
    async def test_uspsa_job_does_not_satisfy_practiscore(self, mock_db):
        """A pending USPSA job should NOT be picked up by scrape_practiscore_and_store."""
        create_job("A12345", job_type="uspsa")

        with patch("app.services.practiscore_scraper.scrape_member_matches", new_callable=AsyncMock) as mock_scrape:
            mock_scrape.return_value = _MOCK_MATCHES
            await task_manager.scrape_practiscore_and_store("A12345", mock_db)

        mock_scrape.assert_not_called()
