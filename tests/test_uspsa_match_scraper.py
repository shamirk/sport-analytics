"""Unit tests for app.services.uspsa_match_scraper."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.uspsa_match_scraper import scrape_match_list


_MOCK_SCRAPE_DATA = {
    "current_classifications": [],
    "classifier_scores": [
        {
            "classifier": "99-11",
            "division": "Limited",
            "date": "03/15/24",
            "club": "Custer Sportsmens Club",
            "percentage": 84.5,
            "hit_factor": 7.0,
        },
        {
            "classifier": "18-01",
            "division": "Limited",
            "date": "03/15/24",
            "club": "Custer Sportsmens Club",  # same club+date → same match
            "percentage": 72.0,
            "hit_factor": 6.0,
        },
        {
            "classifier": "99-11",
            "division": "Limited",
            "date": "06/01/24",
            "club": "River Valley Shooting Club",
            "percentage": 88.0,
            "hit_factor": 7.5,
        },
        {
            "classifier": "99-11",
            "division": "Open",
            "date": "06/01/24",
            "club": "River Valley Shooting Club",  # same club+date but different division → different entry
            "percentage": 90.0,
            "hit_factor": 9.0,
        },
    ],
    "match_results": [],
}


class TestScrapeMatchList:
    @pytest.mark.asyncio
    async def test_deduplicates_same_club_date(self):
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            matches = await scrape_match_list("A12345")

        # 2 unique (club, date, division) combos from scores
        names = [m["match_name"] for m in matches]
        assert names.count("Custer Sportsmens Club") == 1

    @pytest.mark.asyncio
    async def test_different_club_dates_are_separate(self):
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            matches = await scrape_match_list("A12345")

        names = [m["match_name"] for m in matches]
        assert "Custer Sportsmens Club" in names
        assert "River Valley Shooting Club" in names

    @pytest.mark.asyncio
    async def test_different_divisions_same_club_date_separate(self):
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            matches = await scrape_match_list("A12345")

        # River Valley appears once for Limited and once for Open
        river_matches = [m for m in matches if m["match_name"] == "River Valley Shooting Club"]
        divisions = {m["division"] for m in river_matches}
        assert "Limited" in divisions
        assert "Open" in divisions

    @pytest.mark.asyncio
    async def test_date_converted_to_iso(self):
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            matches = await scrape_match_list("A12345")

        dates = [m["match_date"] for m in matches if m["match_date"]]
        assert all("-" in d for d in dates), "Dates should be ISO format"
        assert "2024-03-15" in dates

    @pytest.mark.asyncio
    async def test_empty_classifier_scores_returns_empty(self):
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(
                return_value={"current_classifications": [], "classifier_scores": [], "match_results": []}
            )

            matches = await scrape_match_list("A12345")

        assert matches == []

    @pytest.mark.asyncio
    async def test_scraper_error_returns_empty(self):
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(side_effect=RuntimeError("network error"))

            matches = await scrape_match_list("A12345")

        assert matches == []

    @pytest.mark.asyncio
    async def test_match_level_is_none(self):
        """Match level is not available from classifier page — always None."""
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=_MOCK_SCRAPE_DATA)

            matches = await scrape_match_list("A12345")

        assert all(m["match_level"] is None for m in matches)

    @pytest.mark.asyncio
    async def test_score_without_club_skipped(self):
        data = {
            "classifier_scores": [
                {"classifier": "99-11", "division": "Limited", "date": "01/01/24", "club": ""},
                {"classifier": "18-01", "division": "Limited", "date": "02/01/24", "club": "Valid Club"},
            ],
        }
        with patch("app.services.uspsa_match_scraper.USPSAScraper") as MockScraper:
            mock_instance = MockScraper.return_value
            mock_instance.scrape_member = AsyncMock(return_value=data)

            matches = await scrape_match_list("A12345")

        assert len(matches) == 1
        assert matches[0]["match_name"] == "Valid Club"
