"""Unit tests for app.services.uspsa_scraper — HTML parsing only (no network)."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import app.services.uspsa_scraper as scraper_mod
import pytest
from bs4 import BeautifulSoup

from app.services.uspsa_scraper import (
    USPSAScraper,
    _col_map,
    _extract_member_number,
    _parse_classifier_scores,
    _parse_classifications,
    _parse_match_results,
    _safe_float,
    _safe_int,
    _table_rows,
)


# ---------------------------------------------------------------------------
# Helpers: _safe_float, _safe_int
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_valid_float(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_strips_percent(self):
        assert _safe_float("75.5%") == pytest.approx(75.5)

    def test_strips_commas(self):
        assert _safe_float("1,234.56") == pytest.approx(1234.56)

    def test_whitespace_stripped(self):
        assert _safe_float("  42.0  ") == pytest.approx(42.0)

    def test_non_numeric_returns_none(self):
        assert _safe_float("N/A") is None

    def test_empty_string_returns_none(self):
        assert _safe_float("") is None

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_zero(self):
        assert _safe_float("0") == pytest.approx(0.0)

    def test_negative(self):
        assert _safe_float("-5.5") == pytest.approx(-5.5)


class TestSafeInt:
    def test_valid_int(self):
        assert _safe_int("42") == 42

    def test_strips_non_digits(self):
        assert _safe_int("1st") == 1

    def test_empty_returns_none(self):
        assert _safe_int("") is None

    def test_letters_only_returns_none(self):
        assert _safe_int("abc") is None


# ---------------------------------------------------------------------------
# _extract_member_number
# ---------------------------------------------------------------------------


class TestExtractMemberNumber:
    def test_extracts_from_url(self):
        assert _extract_member_number("https://uspsa.org/classification/A12345") == "A12345"

    def test_falls_back_to_url_if_no_match(self):
        result = _extract_member_number("https://example.com/foo")
        assert result == "https://example.com/foo"

    def test_numeric_member_number(self):
        assert _extract_member_number("https://uspsa.org/classification/99999") == "99999"


# ---------------------------------------------------------------------------
# _col_map
# ---------------------------------------------------------------------------


class TestColMap:
    def test_maps_by_candidate(self):
        header = ["date", "number", "club", "f", "percent", "hf"]
        wanted = {"percentage": ["percent"], "classifier": ["number"]}
        result = _col_map(header, wanted)
        assert result["percentage"] == 4
        assert result["classifier"] == 1

    def test_skips_missing_candidates(self):
        header = ["date", "club"]
        wanted = {"percentage": ["percent", "pct"], "date": ["date"]}
        result = _col_map(header, wanted)
        assert "percentage" not in result
        assert result["date"] == 0

    def test_empty_header(self):
        result = _col_map([], {"percentage": ["percent"]})
        assert result == {}


# ---------------------------------------------------------------------------
# _table_rows
# ---------------------------------------------------------------------------


def _make_table(rows_data: list[list[str]]) -> "Tag":
    html_rows = ""
    for row in rows_data:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        html_rows += f"<tr>{cells}</tr>"
    soup = BeautifulSoup(f"<table>{html_rows}</table>", "html.parser")
    return soup.find("table")


class TestTableRows:
    def test_basic_rows(self):
        table = _make_table([["A", "B"], ["C", "D"]])
        rows = _table_rows(table)
        assert rows == [["A", "B"], ["C", "D"]]

    def test_empty_table(self):
        soup = BeautifulSoup("<table></table>", "html.parser")
        table = soup.find("table")
        assert _table_rows(table) == []

    def test_strips_whitespace(self):
        html = "<table><tr><td>  hello  </td><td>world  </td></tr></table>"
        soup = BeautifulSoup(html, "html.parser")
        rows = _table_rows(soup.find("table"))
        assert rows == [["hello", "world"]]

    def test_th_cells_included(self):
        html = "<table><tr><th>Header</th></tr><tr><td>Value</td></tr></table>"
        soup = BeautifulSoup(html, "html.parser")
        rows = _table_rows(soup.find("table"))
        assert rows[0] == ["Header"]
        assert rows[1] == ["Value"]


# ---------------------------------------------------------------------------
# _parse_classifications — with fixture HTML
# ---------------------------------------------------------------------------

_CLASSIFICATIONS_HTML = """
<html><body>
<table>
  <tr><td>Some Other Table</td></tr>
  <tr><td>col1</td><td>col2</td></tr>
</table>
<table>
  <tr><td>Classifications</td></tr>
  <tr><td>Open</td><td>Class: A</td><td>Pct: 85.1234</td><td>High Pct: 90.0000</td></tr>
  <tr><td>Limited</td><td>Class: B</td><td>Pct: 72.5000</td><td>High Pct: 75.0000</td></tr>
  <tr><td>Production</td><td>Class: C</td><td>Pct: 55.0000</td><td>High Pct: 60.0000</td></tr>
</table>
</body></html>
"""


class TestParseClassifications:
    @pytest.fixture
    def soup(self):
        return BeautifulSoup(_CLASSIFICATIONS_HTML, "html.parser")

    def test_returns_three_divisions(self, soup):
        results = _parse_classifications(soup)
        assert len(results) == 3

    def test_division_names(self, soup):
        results = _parse_classifications(soup)
        divisions = [r["division"] for r in results]
        assert "Open" in divisions
        assert "Limited" in divisions
        assert "Production" in divisions

    def test_class_parsed(self, soup):
        results = _parse_classifications(soup)
        by_div = {r["division"]: r for r in results}
        assert by_div["Open"]["class"] == "A"
        assert by_div["Limited"]["class"] == "B"

    def test_percentage_parsed(self, soup):
        results = _parse_classifications(soup)
        by_div = {r["division"]: r for r in results}
        assert by_div["Open"]["percentage"] == pytest.approx(85.1234)
        assert by_div["Production"]["percentage"] == pytest.approx(55.0)

    def test_high_percentage_parsed(self, soup):
        results = _parse_classifications(soup)
        by_div = {r["division"]: r for r in results}
        assert by_div["Open"]["high_percentage"] == pytest.approx(90.0)

    def test_ignores_non_classifications_table(self, soup):
        # The "Some Other Table" table should be skipped
        results = _parse_classifications(soup)
        divisions = [r["division"] for r in results]
        assert "col1" not in divisions

    def test_empty_html_returns_empty(self):
        soup = BeautifulSoup("<html></html>", "html.parser")
        assert _parse_classifications(soup) == []


# ---------------------------------------------------------------------------
# _parse_classifier_scores — with fixture HTML
# ---------------------------------------------------------------------------

_CLASSIFIER_SCORES_HTML = """
<html><body>
<table>
  <tr><td>Limited Optics Classifiers(Click to Expand)</td></tr>
  <tr><td>Date</td><td>Number</td><td>Club</td><td>F</td><td>Percent</td><td>HF</td><td>Entered</td><td>Source</td></tr>
  <tr><td>3/01/26</td><td>99-11</td><td>Custer Club</td><td>Y</td><td>66.7947</td><td>7.0773</td><td>3/05/26</td><td>USPSA</td></tr>
  <tr><td>1/15/26</td><td>18-01</td><td>River Club</td><td>N</td><td>71.2000</td><td>6.5500</td><td>1/20/26</td><td>USPSA</td></tr>
</table>
<table>
  <tr><td>Open Classifiers(Click to Expand)</td></tr>
  <tr><td>Date</td><td>Number</td><td>Club</td><td>F</td><td>Percent</td><td>HF</td><td>Entered</td><td>Source</td></tr>
  <tr><td>2/10/26</td><td>03-06</td><td>Metro Club</td><td>Y</td><td>92.5000</td><td>9.1000</td><td>2/12/26</td><td>USPSA</td></tr>
</table>
</body></html>
"""


class TestParseClassifierScores:
    @pytest.fixture
    def soup(self):
        return BeautifulSoup(_CLASSIFIER_SCORES_HTML, "html.parser")

    def test_total_scores_parsed(self, soup):
        results = _parse_classifier_scores(soup)
        assert len(results) == 3

    def test_division_extracted(self, soup):
        results = _parse_classifier_scores(soup)
        divisions = {r["division"] for r in results}
        assert "Limited Optics" in divisions
        assert "Open" in divisions

    def test_classifier_number_parsed(self, soup):
        results = _parse_classifier_scores(soup)
        classifiers = {r["classifier"] for r in results}
        assert "99-11" in classifiers
        assert "18-01" in classifiers
        assert "03-06" in classifiers

    def test_percentage_as_float(self, soup):
        results = _parse_classifier_scores(soup)
        by_clf = {r["classifier"]: r for r in results}
        assert by_clf["99-11"]["percentage"] == pytest.approx(66.7947)
        assert by_clf["03-06"]["percentage"] == pytest.approx(92.5)

    def test_hit_factor_as_float(self, soup):
        results = _parse_classifier_scores(soup)
        by_clf = {r["classifier"]: r for r in results}
        assert by_clf["99-11"]["hit_factor"] == pytest.approx(7.0773)

    def test_club_name_preserved(self, soup):
        results = _parse_classifier_scores(soup)
        by_clf = {r["classifier"]: r for r in results}
        assert by_clf["99-11"]["club"] == "Custer Club"

    def test_empty_html_returns_empty(self):
        soup = BeautifulSoup("<html></html>", "html.parser")
        assert _parse_classifier_scores(soup) == []


# ---------------------------------------------------------------------------
# _parse_match_results
# ---------------------------------------------------------------------------


class TestParseMatchResults:
    def test_always_returns_empty(self):
        soup = BeautifulSoup(_CLASSIFIER_SCORES_HTML, "html.parser")
        assert _parse_match_results(soup) == []

    def test_empty_html_returns_empty(self):
        assert _parse_match_results(BeautifulSoup("", "html.parser")) == []


# ---------------------------------------------------------------------------
# USPSAScraper._parse_page
# ---------------------------------------------------------------------------

_FULL_PAGE_HTML = _CLASSIFICATIONS_HTML.replace("</html>", "") + _CLASSIFIER_SCORES_HTML


class TestUSPSAScraperParsePage:
    def test_returns_member_number(self):
        scraper = USPSAScraper()
        result = scraper._parse_page(_CLASSIFICATIONS_HTML, "A12345")
        assert result["member_number"] == "A12345"

    def test_returns_scraped_at(self):
        scraper = USPSAScraper()
        result = scraper._parse_page(_CLASSIFICATIONS_HTML, "A12345")
        assert "scraped_at" in result

    def test_classifications_included(self):
        scraper = USPSAScraper()
        result = scraper._parse_page(_CLASSIFICATIONS_HTML, "A12345")
        assert len(result["current_classifications"]) == 3

    def test_classifier_scores_included(self):
        scraper = USPSAScraper()
        result = scraper._parse_page(_CLASSIFIER_SCORES_HTML, "A12345")
        assert len(result["classifier_scores"]) == 3

    def test_match_results_empty(self):
        scraper = USPSAScraper()
        result = scraper._parse_page(_CLASSIFICATIONS_HTML, "A12345")
        assert result["match_results"] == []


# ---------------------------------------------------------------------------
# Playwright semaphore, browser singleton, and circuit breaker
# ---------------------------------------------------------------------------


class TestPlaywrightSemaphore:
    def test_semaphore_limit_is_three(self):
        assert scraper_mod._playwright_semaphore._value == 3


class TestPlaywrightBrowserSingleton:
    def setup_method(self):
        self._orig_browser = scraper_mod._playwright_browser
        self._orig_pw = scraper_mod._playwright_pw
        scraper_mod._playwright_browser = None
        scraper_mod._playwright_pw = None

    def teardown_method(self):
        scraper_mod._playwright_browser = self._orig_browser
        scraper_mod._playwright_pw = self._orig_pw

    async def test_reuses_existing_connected_browser(self):
        mock_browser = MagicMock()
        mock_browser.is_connected = MagicMock(return_value=True)
        scraper_mod._playwright_browser = mock_browser

        result = await scraper_mod._get_or_create_playwright_browser()
        assert result is mock_browser

    async def test_creates_browser_when_none_exists(self):
        mock_browser = MagicMock()
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium = MagicMock()
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw_obj = MagicMock()
        mock_pw_obj.start = AsyncMock(return_value=mock_pw_instance)

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_obj):
            result = await scraper_mod._get_or_create_playwright_browser()

        assert result is mock_browser

    async def test_recreates_browser_when_disconnected(self):
        old_browser = MagicMock()
        old_browser.is_connected = MagicMock(return_value=False)
        old_pw = AsyncMock()
        scraper_mod._playwright_browser = old_browser
        scraper_mod._playwright_pw = old_pw

        new_browser = MagicMock()
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium = MagicMock()
        mock_pw_instance.chromium.launch = AsyncMock(return_value=new_browser)
        mock_pw_obj = MagicMock()
        mock_pw_obj.start = AsyncMock(return_value=mock_pw_instance)

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_obj):
            result = await scraper_mod._get_or_create_playwright_browser()

        assert result is new_browser
        old_pw.stop.assert_awaited_once()


class TestPlaywrightCircuitBreaker:
    def setup_method(self):
        self._orig_failures = scraper_mod._playwright_consecutive_failures
        self._orig_open_until = scraper_mod._playwright_circuit_open_until
        scraper_mod._playwright_consecutive_failures = 0
        scraper_mod._playwright_circuit_open_until = 0.0

    def teardown_method(self):
        scraper_mod._playwright_consecutive_failures = self._orig_failures
        scraper_mod._playwright_circuit_open_until = self._orig_open_until

    async def test_raises_immediately_when_circuit_open(self):
        scraper_mod._playwright_circuit_open_until = time.monotonic() + 60.0
        scraper = USPSAScraper()
        with pytest.raises(RuntimeError, match="circuit breaker open"):
            await scraper._fetch_with_playwright("https://uspsa.org/classification/A00001")

    async def test_circuit_opens_after_three_cf_timeouts(self):
        from playwright.async_api import TimeoutError as PWTimeout

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=MagicMock(status=200))
        mock_page.wait_for_function = AsyncMock(side_effect=PWTimeout("timeout"))
        mock_page.content = AsyncMock(return_value="<html></html>")
        mock_page.close = AsyncMock()

        mock_browser = MagicMock()
        mock_browser.new_page = AsyncMock(return_value=mock_page)

        scraper = USPSAScraper()
        url = "https://uspsa.org/classification/A00001"

        with patch(
            "app.services.uspsa_scraper._get_or_create_playwright_browser",
            AsyncMock(return_value=mock_browser),
        ):
            for _ in range(3):
                await scraper._fetch_with_playwright(url)

        assert scraper_mod._playwright_circuit_open_until > time.monotonic()

    async def test_consecutive_failures_reset_on_success(self):
        scraper_mod._playwright_consecutive_failures = 2

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=MagicMock(status=200))
        mock_page.wait_for_function = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value="<html>content</html>")
        mock_page.close = AsyncMock()

        mock_browser = MagicMock()
        mock_browser.new_page = AsyncMock(return_value=mock_page)

        scraper = USPSAScraper()
        url = "https://uspsa.org/classification/A00001"

        with patch(
            "app.services.uspsa_scraper._get_or_create_playwright_browser",
            AsyncMock(return_value=mock_browser),
        ):
            await scraper._fetch_with_playwright(url)

        assert scraper_mod._playwright_consecutive_failures == 0
