"""USPSA match-results-details scraper.

Uses Source-column links from the classifier scores table to find each
match's USPSA page (``/match-results-details?index=N``), then parses:
  - match name
  - match date (if present)
  - match level (if present)
  - PractiScore URL (if the USPSA page links out to practiscore.com)

Falls back to club-name-as-match-name when no Source links are present.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)


async def scrape_match_list(member_number: str) -> list[dict]:
    """Return deduplicated match list for *member_number*.

    Each dict:
        match_name       str
        match_date       str | None  (ISO YYYY-MM-DD)
        division         str
        match_level      int | None
        uspsa_match_url  str | None
        practiscore_url  str | None  — direct PS link if USPSA page has one
    """
    from app.services.uspsa_scraper import USPSAScraper

    scraper = USPSAScraper()
    try:
        data = await scraper.scrape_member(member_number)
    except Exception as exc:
        logger.warning("uspsa_scrape_failed", member_number=member_number, error=str(exc))
        return []

    scores = data.get("classifier_scores", [])
    if not scores:
        return []

    # --- Pass 1: collect unique Source URLs and club-name fallbacks -----------
    seen_urls: set[str] = set()
    url_to_score: dict[str, dict] = {}   # url → representative score row
    seen_clubs: set[tuple] = set()
    fallback_matches: list[dict] = []

    for score in scores:
        match_url = score.get("match_url")
        club = (score.get("club") or "").strip()
        division = score.get("division") or ""
        match_date = _parse_date(score.get("date") or "")

        if match_url:
            if match_url not in seen_urls:
                seen_urls.add(match_url)
                url_to_score[match_url] = score
        else:
            key = (club.lower(), match_date, division)
            if club and key not in seen_clubs:
                seen_clubs.add(key)
                fallback_matches.append({
                    "match_name": club,
                    "match_date": match_date,
                    "division": division,
                    "match_level": None,
                    "uspsa_match_url": None,
                    "practiscore_url": None,
                })

    # --- Pass 2: fetch USPSA match-results-details pages ---------------------
    matches: list[dict] = []
    for url, score in url_to_score.items():
        division = score.get("division") or ""
        match_date = _parse_date(score.get("date") or "")
        club = (score.get("club") or "").strip()

        try:
            html = await _fetch_uspsa_page(url)
            info = _parse_match_page(html) if html else {}
        except Exception as exc:
            logger.warning("uspsa_match_page_failed", url=url, error=str(exc))
            info = {}

        matches.append({
            "match_name": info.get("match_name") or club,
            "match_date": info.get("match_date") or match_date,
            "division": division,
            "match_level": info.get("match_level"),
            "uspsa_match_url": url,
            "practiscore_url": info.get("practiscore_url"),
        })

        await asyncio.sleep(0.3)  # polite delay

    matches.extend(fallback_matches)

    logger.info(
        "uspsa_match_list_complete",
        member_number=member_number,
        total=len(matches),
        with_uspsa_url=len(url_to_score),
        fallback=len(fallback_matches),
    )
    return matches


# ---------------------------------------------------------------------------
# Fetch / parse helpers
# ---------------------------------------------------------------------------

async def _fetch_uspsa_page(url: str) -> str | None:
    """Fetch a USPSA page via curl_cffi (same strategy as the classifier scraper)."""
    from curl_cffi.requests import AsyncSession

    try:
        async with AsyncSession() as session:
            resp = await session.get(
                url,
                impersonate="chrome120",
                timeout=20,
                headers={
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://uspsa.org/",
                },
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        logger.warning("fetch_uspsa_page_failed", url=url, error=str(exc))
        return None


def _parse_match_page(html: str) -> dict:
    """Parse a USPSA match-results-details page for key fields."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}

    # Match name — try headings first, then page title
    for tag in ("h1", "h2", "h3"):
        el = soup.find(tag)
        if el:
            text = el.get_text(strip=True)
            if text and len(text) > 5:
                result["match_name"] = text
                break

    if "match_name" not in result:
        title_el = soup.find("title")
        if title_el:
            text = re.sub(r"\s*[-|]\s*USPSA.*$", "", title_el.get_text(strip=True)).strip()
            if text:
                result["match_name"] = text

    # Match date — scan page text for date patterns
    page_text = soup.get_text(" ", strip=True)
    date_m = re.search(
        r"\b((?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},?\s+\d{4}"
        r"|\d{1,2}/\d{1,2}/\d{2,4})\b",
        page_text,
    )
    if date_m:
        result["match_date"] = _parse_date(date_m.group(1))

    # Match level
    level_m = re.search(r"\blevel\s*([1-4])\b", page_text, re.IGNORECASE)
    if level_m:
        result["match_level"] = int(level_m.group(1))

    # PractiScore link — first anchor containing practiscore.com
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "practiscore.com" in href:
            if not href.startswith("http"):
                href = "https://practiscore.com" + (href if href.startswith("/") else "/" + href)
            # Only keep links that look like match result pages
            if re.search(r"/results/", href):
                result["practiscore_url"] = href
                break

    return result


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None
