"""PractiScore match-results scraper.

Strategy
--------
1.  Hit ``https://practiscore.com/results/new/by-member-number/{member_number}``
    with curl_cffi (Chrome impersonation).  PractiScore uses Cloudflare with a
    JS challenge so this will usually 403; if it does we fall back to Playwright.

2.  Parse the member's match list from that page (table rows with match name,
    date, division link).

3.  For each match URL found, fetch the match-results page and parse the
    competitors table (placement, shooter name, member number, division,
    classification, percent of winner).

The caller (task_manager) is responsible for persisting to DB.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from bs4 import BeautifulSoup, Tag

logger = structlog.get_logger(__name__)

PS_MEMBER_URL = "https://practiscore.com/results/new/by-member-number/{member_number}"
_CF_MARKERS = ("just a moment", "checking your browser", "enable javascript")
_ALLOWED_HOSTS = frozenset({"practiscore.com", "www.practiscore.com"})


def _validate_url(url: str) -> bool:
    """Return True only if url uses http/https and targets an allowed host."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and parsed.netloc in _ALLOWED_HOSTS


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def scrape_member_matches(member_number: str) -> list[dict]:
    """Return a list of match dicts scraped from PractiScore for *member_number*.

    Each dict:
        match_name          str
        match_date          str | None   (ISO YYYY-MM-DD)
        division            str
        match_level         int | None
        practiscore_match_id str | None  (slug from URL)
        source_url          str | None
        total_competitors   int | None
        member_placement    int | None
        member_percent      float | None
        results             list[dict]   — per-shooter rows
    """
    url = PS_MEMBER_URL.format(member_number=member_number)
    log = logger.bind(member_number=member_number)
    log.info("practiscore_scrape_start", url=url)

    html = await _fetch_with_fallback(url)
    if not html:
        log.warning("practiscore_no_html")
        return []

    match_entries = _parse_member_match_list(html, member_number)
    log.info("practiscore_match_list", count=len(match_entries))

    results: list[dict] = []
    for entry in match_entries:
        match_url = entry.get("source_url")
        if not match_url:
            results.append(entry)
            continue
        try:
            match_html = await _fetch_with_fallback(match_url)
            if match_html:
                _enrich_with_results(entry, match_html, member_number)
        except Exception as exc:
            log.warning(
                "practiscore_match_fetch_failed",
                match_url=match_url,
                error=str(exc),
            )
        results.append(entry)
        # polite delay between match fetches
        await asyncio.sleep(1.0)

    log.info("practiscore_scrape_complete", matches=len(results))
    return results


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_with_fallback(url: str) -> str | None:
    """Try curl_cffi first, then Playwright."""
    if not _validate_url(url):
        logger.warning("practiscore_blocked_url", url=url)
        return None
    try:
        html = await _fetch_curl_cffi(url)
        if html and not _is_cf_challenge(html):
            return html
    except Exception as exc:
        logger.debug("curl_cffi_failed", url=url, error=str(exc))

    try:
        return await _fetch_playwright(url)
    except Exception as exc:
        logger.warning("playwright_failed", url=url, error=str(exc))
        return None


async def _fetch_curl_cffi(url: str) -> str:
    from curl_cffi.requests import AsyncSession

    async with AsyncSession() as session:
        response = await session.get(
            url,
            impersonate="chrome120",
            timeout=30,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        if response.status_code in (403, 503):
            raise RuntimeError(f"Cloudflare blocked ({response.status_code})")
        response.raise_for_status()
        return response.text


async def _fetch_playwright(url: str, timeout_ms: int = 45_000) -> str:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    logger.debug("fetch_playwright", url=url)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await ctx.new_page()

            # Use "load" instead of "networkidle" — Cloudflare keeps background
            # requests alive indefinitely so networkidle never fires.
            try:
                await page.goto(url, wait_until="load", timeout=timeout_ms)
            except PWTimeout:
                # Page may still have usable content even after a timeout
                logger.debug("playwright_load_timeout", url=url)

            # Wait for Cloudflare JS challenge to resolve
            try:
                await page.wait_for_function(
                    "() => !document.title.toLowerCase().includes('just a moment') && "
                    "!document.title.toLowerCase().includes('checking your browser')",
                    timeout=30_000,
                )
            except PWTimeout:
                logger.warning("cloudflare_challenge_timeout", url=url)

            # Give the page JS a moment to render content
            await asyncio.sleep(2)
            html = await page.content()
            logger.debug("playwright_got_content", url=url, bytes=len(html))
            return html
        finally:
            await browser.close()


def _is_cf_challenge(html: str) -> bool:
    lower = html.lower()
    return any(m in lower for m in _CF_MARKERS)


# ---------------------------------------------------------------------------
# Parsing: member match list
# ---------------------------------------------------------------------------

def _parse_member_match_list(html: str, member_number: str) -> list[dict]:
    """Parse the ``/results/new/by-member-number/{member}`` page.

    PractiScore renders a table with columns like:
        Match Name | Date | Division | Class | Place | Pct
    and links each match name to ``/results/new/{uuid}`` or ``/results/{slug}``.
    """
    soup = BeautifulSoup(html, "html.parser")
    matches: list[dict] = []

    # Try to find the results table — PS uses a DataTable or similar
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = [td.get_text(strip=True).lower() for td in rows[0].find_all(["th", "td"])]
        # Must have at least a match/name column and a date column
        if not any("match" in h or "name" in h for h in header_cells):
            continue

        col = _col_map(header_cells, {
            "match_name":  ["match", "name", "event"],
            "match_date":  ["date"],
            "division":    ["division", "div"],
            "match_level": ["level", "lvl"],
            "placement":   ["place", "rank", "pos"],
            "percent":     ["pct", "percent", "%"],
        })

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]

            # Match name + URL
            name_idx = col.get("match_name", 0)
            name_cell: Tag = cells[name_idx] if name_idx < len(cells) else cells[0]
            match_name = name_cell.get_text(strip=True)
            link = name_cell.find("a")
            source_url: str | None = None
            ps_match_id: str | None = None
            if link and link.get("href"):
                href = str(link["href"])
                if href.startswith("/"):
                    href = "https://practiscore.com" + href
                if _validate_url(href):
                    source_url = href
                    ps_match_id = _extract_match_id(href)
                else:
                    logger.warning("practiscore_blocked_href", href=href)

            if not match_name:
                continue

            # Date
            date_idx = col.get("match_date")
            raw_date = cell_texts[date_idx] if date_idx is not None and date_idx < len(cell_texts) else ""
            match_date = _parse_date(raw_date)

            # Division
            div_idx = col.get("division")
            division = cell_texts[div_idx].strip() if div_idx is not None and div_idx < len(cell_texts) else ""

            # Level
            lvl_idx = col.get("match_level")
            match_level: int | None = None
            if lvl_idx is not None and lvl_idx < len(cell_texts):
                match_level = _safe_int(cell_texts[lvl_idx])

            # Member's own placement & pct from this listing page
            place_idx = col.get("placement")
            member_placement: int | None = None
            if place_idx is not None and place_idx < len(cell_texts):
                member_placement = _safe_int(cell_texts[place_idx])

            pct_idx = col.get("percent")
            member_percent: float | None = None
            if pct_idx is not None and pct_idx < len(cell_texts):
                member_percent = _safe_float(cell_texts[pct_idx])

            matches.append({
                "match_name": match_name,
                "match_date": match_date,
                "division": division,
                "match_level": match_level,
                "practiscore_match_id": ps_match_id,
                "source_url": source_url,
                "total_competitors": None,
                "member_placement": member_placement,
                "member_percent": member_percent,
                "results": [],
            })

    # Fallback: if no table found, look for match links in the page
    if not matches:
        matches = _parse_match_list_from_links(soup, member_number)

    return matches


def _parse_match_list_from_links(soup: BeautifulSoup, member_number: str) -> list[dict]:
    """Fallback parser: find all /results/new/... links in the page."""
    matches: list[dict] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if not re.search(r"/results/(new/)?[a-zA-Z0-9\-]+", href):
            continue
        if "/by-member-number" in href:
            continue
        if href.startswith("/"):
            href = "https://practiscore.com" + href
        if not _validate_url(href):
            logger.warning("practiscore_blocked_href", href=href)
            continue
        ps_id = _extract_match_id(href)
        if not ps_id or ps_id in seen:
            continue
        seen.add(ps_id)

        # Try to get name from link text or surrounding context
        name = a.get_text(strip=True) or ps_id
        # Date from sibling text in the same row/li
        parent = a.parent
        raw_text = parent.get_text(" ", strip=True) if parent else ""
        match_date = _find_date_in_text(raw_text)

        matches.append({
            "match_name": name,
            "match_date": match_date,
            "division": "",
            "match_level": None,
            "practiscore_match_id": ps_id,
            "source_url": href,
            "total_competitors": None,
            "member_placement": None,
            "member_percent": None,
            "results": [],
        })

    return matches


# ---------------------------------------------------------------------------
# Parsing: individual match results page
# ---------------------------------------------------------------------------

def _enrich_with_results(entry: dict, html: str, queried_member: str) -> None:
    """Parse a match results page and attach competitor data to *entry*."""
    soup = BeautifulSoup(html, "html.parser")

    # Attempt to extract match level from page title or metadata
    if entry.get("match_level") is None:
        entry["match_level"] = _extract_match_level(soup)

    # Find the results table
    tables = soup.find_all("table")
    best_table = _find_results_table(tables)
    if best_table is None:
        return

    rows = best_table.find_all("tr")
    if len(rows) < 2:
        return

    header_cells = [td.get_text(strip=True).lower() for td in rows[0].find_all(["th", "td"])]
    col = _col_map(header_cells, {
        "placement":      ["place", "rank", "#", "pos"],
        "shooter_name":   ["name", "shooter", "competitor", "member"],
        "member_number":  ["number", "member #", "member no", "uspsa"],
        "division":       ["division", "div"],
        "classification": ["class", "classification", "cls"],
        "total_points":   ["points", "pts", "score"],
        "total_time":     ["time", "sec"],
        "percent":        ["pct", "percent", "%"],
    })

    competitors: list[dict] = []
    member_upper = queried_member.upper()

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        texts = [c.get_text(strip=True) for c in cells]

        placement = _safe_int(texts[col["placement"]]) if "placement" in col and col["placement"] < len(texts) else None
        name = texts[col["shooter_name"]] if "shooter_name" in col and col["shooter_name"] < len(texts) else ""
        mem_num = texts[col["member_number"]] if "member_number" in col and col["member_number"] < len(texts) else None
        division = texts[col["division"]] if "division" in col and col["division"] < len(texts) else entry.get("division", "")
        classification = texts[col["classification"]] if "classification" in col and col["classification"] < len(texts) else None
        total_points = _safe_float(texts[col["total_points"]]) if "total_points" in col and col["total_points"] < len(texts) else None
        total_time = _safe_float(texts[col["total_time"]]) if "total_time" in col and col["total_time"] < len(texts) else None
        pct = _safe_float(texts[col["percent"]]) if "percent" in col and col["percent"] < len(texts) else None

        if not name:
            continue

        is_member = bool(
            mem_num and mem_num.upper() == member_upper
        ) or (
            not mem_num and member_upper in name.upper()
        )

        comp = {
            "shooter_name": name,
            "member_number": (mem_num or "").strip() or None,
            "division": division.strip(),
            "classification": (classification or "").strip() or None,
            "total_points": total_points,
            "total_time": total_time,
            "percent_of_winner": pct,
            "placement": placement,
            "is_queried_member": is_member,
        }
        competitors.append(comp)

        if is_member:
            entry["member_placement"] = placement
            entry["member_percent"] = pct

    entry["total_competitors"] = len(competitors) if competitors else None
    entry["results"] = competitors


def _find_results_table(tables: list) -> Tag | None:
    """Return the table most likely to contain match results."""
    best: Tag | None = None
    best_score = 0
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        header_text = " ".join(
            td.get_text(strip=True).lower()
            for td in rows[0].find_all(["th", "td"])
        )
        score = sum(
            1 for kw in ("place", "name", "percent", "pct", "division", "class")
            if kw in header_text
        )
        if score > best_score:
            best_score = score
            best = table
    return best if best_score >= 2 else None


def _extract_match_level(soup: BeautifulSoup) -> int | None:
    """Try to detect match level (1-4) from page content."""
    text = soup.get_text(" ", strip=True)
    m = re.search(r"level\s*([1-4])", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _extract_match_id(url: str) -> str | None:
    """Extract PractiScore match slug/UUID from a URL."""
    # /results/new/UUID or /results/slug
    m = re.search(r"/results/(?:new/)?([a-zA-Z0-9\-]+)(?:/|$)", url)
    return m.group(1) if m else None


def _parse_date(raw: str) -> str | None:
    """Parse a date string into ISO format, trying common formats."""
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _find_date_in_text(text: str) -> str | None:
    """Find the first date-like pattern in arbitrary text."""
    patterns = [
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b([A-Za-z]+ \d{1,2},? \d{4})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return _parse_date(m.group(1))
    return None


def _col_map(header: list[str], wanted: dict[str, list[str]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for field, candidates in wanted.items():
        for i, h in enumerate(header):
            if any(c in h for c in candidates):
                mapping[field] = i
                break
    return mapping


def _safe_float(val: str) -> float | None:
    if not val:
        return None
    try:
        return float(re.sub(r"[^\d.\-]", "", val))
    except (ValueError, TypeError):
        return None


def _safe_int(val: str) -> int | None:
    if not val:
        return None
    try:
        return int(re.sub(r"[^\d]", "", val))
    except (ValueError, TypeError):
        return None
