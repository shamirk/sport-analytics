"""Unit tests for app.services.practiscore_scraper — HTML parsing only (no network)."""
import pytest
from bs4 import BeautifulSoup

from app.services.practiscore_scraper import (
    _col_map,
    _enrich_with_results,
    _extract_match_id,
    _find_date_in_text,
    _find_results_table,
    _is_cf_challenge,
    _parse_date,
    _parse_match_list_from_links,
    _parse_member_match_list,
    _safe_float,
    _safe_int,
)


# ---------------------------------------------------------------------------
# _safe_float / _safe_int
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_valid(self):
        assert _safe_float("78.54") == pytest.approx(78.54)

    def test_empty_returns_none(self):
        assert _safe_float("") is None

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_strips_non_numeric(self):
        assert _safe_float("78.5%") == pytest.approx(78.5)

    def test_negative(self):
        assert _safe_float("-3.14") == pytest.approx(-3.14)

    def test_invalid_returns_none(self):
        assert _safe_float("N/A") is None


class TestSafeInt:
    def test_valid(self):
        assert _safe_int("42") == 42

    def test_empty_returns_none(self):
        assert _safe_int("") is None

    def test_none_returns_none(self):
        assert _safe_int(None) is None

    def test_strips_ordinal(self):
        assert _safe_int("3rd") == 3

    def test_invalid_returns_none(self):
        assert _safe_int("N/A") is None


# ---------------------------------------------------------------------------
# _extract_match_id
# ---------------------------------------------------------------------------


class TestExtractMatchId:
    def test_uuid_style(self):
        assert _extract_match_id("https://practiscore.com/results/new/abc-123-def") == "abc-123-def"

    def test_slug_style(self):
        assert _extract_match_id("https://practiscore.com/results/my-match-2024") == "my-match-2024"

    def test_no_match_returns_none(self):
        assert _extract_match_id("https://example.com/foo") is None

    def test_trailing_slash(self):
        result = _extract_match_id("https://practiscore.com/results/new/xyz-999/")
        assert result == "xyz-999"


# ---------------------------------------------------------------------------
# _parse_date / _find_date_in_text
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_mm_dd_yyyy(self):
        assert _parse_date("03/15/2024") == "2024-03-15"

    def test_mm_dd_yy(self):
        assert _parse_date("3/1/26") == "2026-03-01"

    def test_iso(self):
        assert _parse_date("2024-06-15") == "2024-06-15"

    def test_invalid_returns_none(self):
        assert _parse_date("not a date") is None

    def test_empty_returns_none(self):
        assert _parse_date("") is None

    def test_whitespace_stripped(self):
        assert _parse_date("  2024-01-01  ") == "2024-01-01"


class TestFindDateInText:
    def test_finds_mm_dd_yyyy(self):
        assert _find_date_in_text("Match held on 03/15/2024 at club") == "2024-03-15"

    def test_finds_iso(self):
        assert _find_date_in_text("Results: 2025-06-01 posted") == "2025-06-01"

    def test_no_date_returns_none(self):
        assert _find_date_in_text("No dates here") is None


# ---------------------------------------------------------------------------
# _is_cf_challenge
# ---------------------------------------------------------------------------


class TestIsCfChallenge:
    def test_detects_just_a_moment(self):
        assert _is_cf_challenge("<title>Just a moment...</title>") is True

    def test_detects_checking_browser(self):
        assert _is_cf_challenge("<title>Checking your browser</title>") is True

    def test_normal_page_false(self):
        assert _is_cf_challenge("<html><body>Match results here</body></html>") is False

    def test_case_insensitive(self):
        assert _is_cf_challenge("<title>JUST A MOMENT</title>") is True


# ---------------------------------------------------------------------------
# _col_map
# ---------------------------------------------------------------------------


class TestColMap:
    def test_maps_basic(self):
        header = ["match name", "date", "division", "place", "pct"]
        result = _col_map(header, {"match_name": ["match", "name"], "placement": ["place"]})
        assert result["match_name"] == 0
        assert result["placement"] == 3

    def test_missing_field_not_in_result(self):
        header = ["date", "division"]
        result = _col_map(header, {"placement": ["place", "rank"]})
        assert "placement" not in result


# ---------------------------------------------------------------------------
# _find_results_table
# ---------------------------------------------------------------------------

def _make_html_table(header: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in header)
    body = ""
    for row in rows:
        tds = "".join(f"<td>{v}</td>" for v in row)
        body += f"<tr>{tds}</tr>"
    return f"<table><tr>{th}</tr>{body}</table>"


class TestFindResultsTable:
    def test_finds_good_table(self):
        html = _make_html_table(
            ["Place", "Name", "Division", "Class", "Percent"],
            [["1", "Alice", "Limited", "A", "92.5"], ["2", "Bob", "Open", "B", "88.0"]],
        )
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        assert _find_results_table(tables) is not None

    def test_returns_none_for_irrelevant_table(self):
        html = _make_html_table(
            ["Match", "Date"],
            [["Foo Match", "2024-01-01"]],
        )
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        assert _find_results_table(tables) is None

    def test_returns_none_for_empty_tables(self):
        assert _find_results_table([]) is None


# ---------------------------------------------------------------------------
# _parse_member_match_list — with fixture HTML
# ---------------------------------------------------------------------------

_MEMBER_MATCH_LIST_HTML = """
<html><body>
<table>
  <tr><th>Match Name</th><th>Date</th><th>Division</th><th>Level</th><th>Place</th><th>Pct</th></tr>
  <tr>
    <td><a href="/results/new/abc-111">Spring Steel 2024</a></td>
    <td>03/15/2024</td><td>Limited</td><td>2</td><td>5</td><td>84.50</td>
  </tr>
  <tr>
    <td><a href="/results/new/def-222">Summer Classic</a></td>
    <td>06/01/2024</td><td>Limited</td><td>2</td><td>3</td><td>91.20</td>
  </tr>
</table>
</body></html>
"""


class TestParseMemberMatchList:
    def test_returns_two_matches(self):
        matches = _parse_member_match_list(_MEMBER_MATCH_LIST_HTML, "A12345")
        assert len(matches) == 2

    def test_match_name_parsed(self):
        matches = _parse_member_match_list(_MEMBER_MATCH_LIST_HTML, "A12345")
        names = [m["match_name"] for m in matches]
        assert "Spring Steel 2024" in names

    def test_date_parsed_as_iso(self):
        matches = _parse_member_match_list(_MEMBER_MATCH_LIST_HTML, "A12345")
        by_name = {m["match_name"]: m for m in matches}
        assert by_name["Spring Steel 2024"]["match_date"] == "2024-03-15"

    def test_source_url_set(self):
        matches = _parse_member_match_list(_MEMBER_MATCH_LIST_HTML, "A12345")
        by_name = {m["match_name"]: m for m in matches}
        assert "abc-111" in by_name["Spring Steel 2024"]["source_url"]

    def test_practiscore_match_id_extracted(self):
        matches = _parse_member_match_list(_MEMBER_MATCH_LIST_HTML, "A12345")
        by_name = {m["match_name"]: m for m in matches}
        assert by_name["Spring Steel 2024"]["practiscore_match_id"] == "abc-111"

    def test_member_placement_parsed(self):
        matches = _parse_member_match_list(_MEMBER_MATCH_LIST_HTML, "A12345")
        by_name = {m["match_name"]: m for m in matches}
        assert by_name["Spring Steel 2024"]["member_placement"] == 5

    def test_member_percent_parsed(self):
        matches = _parse_member_match_list(_MEMBER_MATCH_LIST_HTML, "A12345")
        by_name = {m["match_name"]: m for m in matches}
        assert by_name["Summer Classic"]["member_percent"] == pytest.approx(91.20)

    def test_empty_html_returns_empty(self):
        assert _parse_member_match_list("<html></html>", "A12345") == []


# ---------------------------------------------------------------------------
# _parse_match_list_from_links — fallback parser
# ---------------------------------------------------------------------------

_LINKS_HTML = """
<html><body>
<ul>
  <li><a href="/results/new/xyz-789">Fall Open 2024</a> — 09/20/2024</li>
  <li><a href="/results/new/by-member-number/A12345">My Profile</a></li>
  <li><a href="https://example.com">External</a></li>
</ul>
</body></html>
"""


class TestParseMatchListFromLinks:
    def test_finds_match_links(self):
        soup = BeautifulSoup(_LINKS_HTML, "html.parser")
        matches = _parse_match_list_from_links(soup, "A12345")
        assert len(matches) == 1

    def test_skips_by_member_number_links(self):
        soup = BeautifulSoup(_LINKS_HTML, "html.parser")
        matches = _parse_match_list_from_links(soup, "A12345")
        ids = [m["practiscore_match_id"] for m in matches]
        assert "by-member-number" not in str(ids)

    def test_match_name_from_link_text(self):
        soup = BeautifulSoup(_LINKS_HTML, "html.parser")
        matches = _parse_match_list_from_links(soup, "A12345")
        assert matches[0]["match_name"] == "Fall Open 2024"

    def test_no_duplicate_matches(self):
        html = """
        <html><body>
          <a href="/results/new/abc-111">Match A</a>
          <a href="/results/new/abc-111">Match A again</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        matches = _parse_match_list_from_links(soup, "A12345")
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# _enrich_with_results — parses individual match page
# ---------------------------------------------------------------------------

_MATCH_RESULTS_HTML = """
<html><body>
<h1>Spring Steel 2024 — Level 2 USPSA Match</h1>
<table>
  <tr>
    <th>Place</th><th>Name</th><th>Member #</th>
    <th>Division</th><th>Class</th><th>Points</th><th>Pct</th>
  </tr>
  <tr><td>1</td><td>Alice Smith</td><td>A11111</td><td>Limited</td><td>A</td><td>520.5</td><td>100.00</td></tr>
  <tr><td>2</td><td>Bob Jones</td><td>A12345</td><td>Limited</td><td>B</td><td>450.0</td><td>86.45</td></tr>
  <tr><td>3</td><td>Carol Doe</td><td>A33333</td><td>Limited</td><td>C</td><td>390.0</td><td>75.00</td></tr>
</table>
</body></html>
"""


class TestEnrichWithResults:
    @pytest.fixture
    def entry(self):
        return {
            "match_name": "Spring Steel 2024",
            "match_date": "2024-03-15",
            "division": "Limited",
            "match_level": None,
            "member_placement": None,
            "member_percent": None,
            "results": [],
        }

    def test_total_competitors_set(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        assert entry["total_competitors"] == 3

    def test_member_placement_detected(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        assert entry["member_placement"] == 2

    def test_member_percent_detected(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        assert entry["member_percent"] == pytest.approx(86.45)

    def test_is_queried_member_flag(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        member_rows = [r for r in entry["results"] if r["is_queried_member"]]
        assert len(member_rows) == 1
        assert member_rows[0]["shooter_name"] == "Bob Jones"

    def test_all_competitors_in_results(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        assert len(entry["results"]) == 3

    def test_match_level_extracted(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        assert entry["match_level"] == 2

    def test_placement_data_for_all(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        placements = [r["placement"] for r in entry["results"]]
        assert placements == [1, 2, 3]

    def test_percent_of_winner_parsed(self, entry):
        _enrich_with_results(entry, _MATCH_RESULTS_HTML, "A12345")
        winner = next(r for r in entry["results"] if r["placement"] == 1)
        assert winner["percent_of_winner"] == pytest.approx(100.0)

    def test_empty_page_no_crash(self, entry):
        _enrich_with_results(entry, "<html></html>", "A12345")
        # No table found → results unchanged, total_competitors not set
        assert entry["results"] == []
        assert entry.get("total_competitors") is None
