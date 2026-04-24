"""USPSA match-results-details scraper.

Uses Club-cell links from the classifier scores table (e.g.
``/match-results-details?index=N``) to find each match's USPSA page,
then parses:
  - match name
  - match date (if present)
  - match level (if present)
  - PractiScore URL (if the USPSA page links out to practiscore.com)

The match-results-details pages are JavaScript-rendered, so curl_cffi
returns a "Loading..." skeleton. We detect that and re-fetch with a
single shared Playwright browser (one launch per scrape job, not one
per URL) to keep the overhead manageable.

Falls back to club-name-as-match-name when no Club-cell link is found.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)

_LOADING_MARKERS = ("loading...", "loading…", "please wait", "just a moment")


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

    # --- Pass 1: collect unique match URLs and club-name fallbacks -----------
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

    logger.info(
        "uspsa_match_url_summary",
        member_number=member_number,
        with_url=len(url_to_score),
        fallback=len(fallback_matches),
    )

    # --- Pass 2: fetch USPSA match pages ------------------------------------
    # Step 2a: try curl_cffi for all URLs (fast)
    curl_results: dict[str, dict] = {}
    needs_playwright: list[str] = []

    for url in url_to_score:
        html = await _fetch_curl(url)
        if html and not _is_js_skeleton(html):
            curl_results[url] = _parse_match_page(html)
            logger.debug("match_page_curl_ok", url=url,
                         match_name=curl_results[url].get("match_name"))
        else:
            needs_playwright.append(url)
            logger.debug("match_page_needs_playwright", url=url)

    # Step 2b: Playwright for pages that returned a JS skeleton (one browser)
    pw_results: dict[str, dict] = {}
    if needs_playwright:
        logger.info("match_pages_playwright", count=len(needs_playwright))
        pw_results = await _fetch_all_playwright(needs_playwright)

    all_page_info = {**curl_results, **pw_results}

    # --- Pass 3: build output -----------------------------------------------
    matches: list[dict] = []
    for url, score in url_to_score.items():
        division = score.get("division") or ""
        match_date = _parse_date(score.get("date") or "")
        club = (score.get("club") or "").strip()
        info = all_page_info.get(url, {})

        matches.append({
            "match_name": info.get("match_name") or club,
            "match_date": info.get("match_date") or match_date,
            "division": division,
            "match_level": info.get("match_level"),
            "uspsa_match_url": url,
            "practiscore_url": info.get("practiscore_url"),
        })

    matches.extend(fallback_matches)

    logger.info(
        "uspsa_match_list_complete",
        member_number=member_number,
        total=len(matches),
    )
    return matches


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_curl(url: str) -> str | None:
    """Try fetching a USPSA page via curl_cffi."""
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
        logger.debug("fetch_curl_failed", url=url, error=str(exc))
        return None


def _is_js_skeleton(html: str) -> bool:
    """Return True if the page appears to be an unrendered JS app shell."""
    if not html:
        return True
    text_lower = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    return any(m in text_lower for m in _LOADING_MARKERS)


async def _fetch_all_playwright(urls: list[str]) -> dict[str, dict]:
    """Fetch multiple USPSA match pages using one shared Playwright browser."""
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    results: dict[str, dict] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for url in urls:
                try:
                    page = await browser.new_page(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    )
                    try:
                        await page.goto(url, wait_until="load", timeout=30_000)
                    except PWTimeout:
                        logger.debug("playwright_load_timeout", url=url)

                    # Wait for JS to replace "Loading..." with actual content
                    try:
                        await page.wait_for_function(
                            "() => !document.body.innerText.toLowerCase().includes('loading...')",
                            timeout=10_000,
                        )
                    except PWTimeout:
                        logger.debug("loading_indicator_still_present", url=url)

                    html = await page.content()
                    await page.close()

                    info = _parse_match_page(html)
                    results[url] = info
                    logger.debug(
                        "match_page_playwright_ok",
                        url=url,
                        match_name=info.get("match_name"),
                    )
                except Exception as exc:
                    logger.warning("playwright_match_page_failed", url=url, error=str(exc))
                    results[url] = {}

                await asyncio.sleep(0.3)
        finally:
            await browser.close()

    return results


# ---------------------------------------------------------------------------
# Parse helper
# ---------------------------------------------------------------------------

def _parse_match_page(html: str) -> dict:
    """Parse a (fully rendered) USPSA match-results-details page."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}

    page_text = soup.get_text(" ", strip=True)

    # Match name — find the first heading that isn't a loading placeholder
    for tag in ("h1", "h2", "h3"):
        for el in soup.find_all(tag):
            text = el.get_text(strip=True)
            if text and len(text) > 5 and not any(m in text.lower() for m in _LOADING_MARKERS):
                result["match_name"] = text
                break
        if "match_name" in result:
            break

    # Also try a label+value pattern: "Match Name" label followed by actual name
    if "match_name" not in result:
        mn_match = re.search(
            r"Match\s+Name[:\s]+([^\n\r]{5,80})", page_text, re.IGNORECASE
        )
        if mn_match:
            candidate = mn_match.group(1).strip()
            if not any(m in candidate.lower() for m in _LOADING_MARKERS):
                result["match_name"] = candidate

    # Page title as last resort
    if "match_name" not in result:
        title_el = soup.find("title")
        if title_el:
            text = re.sub(r"\s*[-|]\s*USPSA.*$", "", title_el.get_text(strip=True)).strip()
            if text and not any(m in text.lower() for m in _LOADING_MARKERS):
                result["match_name"] = text

    # Match date
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

    # PractiScore link
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "practiscore.com" in href:
            if not href.startswith("http"):
                href = "https://practiscore.com" + (href if href.startswith("/") else "/" + href)
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
