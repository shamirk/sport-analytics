"""PractiScore match-results scraper — USPSA-sourced match approach.

New flow (replacing the broken by-member-number URL):

1.  Get match names (and optional direct PractiScore URLs) from USPSA via
    ``uspsa_match_scraper.scrape_match_list()``.

2.  For each match:
    a.  If USPSA page already had a PractiScore link, use it directly.
    b.  Otherwise, search ``practiscore.com/results`` by match name:
        - Try curl_cffi first (fast, bypasses CF on most pages).
        - Fall back to Playwright if CF blocks.

3.  Once we have a PractiScore match URL, try fetching the results:
    - Try the S3 JSON endpoint (no CF, very fast).
    - Fall back to HTML scraping the match page.

4.  Parse competitor rows, locate the queried member, return results in the
    format expected by ``task_manager.scrape_practiscore_and_store``.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus, urlparse

import structlog
from bs4 import BeautifulSoup, Tag

logger = structlog.get_logger(__name__)

_ALLOWED_HOSTS = frozenset({"practiscore.com", "www.practiscore.com"})
_CF_MARKERS = ("just a moment", "checking your browser", "enable javascript")

# PractiScore S3 bucket pattern: match data lives at
# https://s3.amazonaws.com/ps-uploads/{uuid}/match_{uuid}.json
_PS_S3_BASE = "https://s3.amazonaws.com/ps-uploads"


def _validate_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and parsed.netloc in _ALLOWED_HOSTS


def _is_cf_challenge(html: str) -> bool:
    lower = html.lower()
    return any(m in lower for m in _CF_MARKERS)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def scrape_member_matches(member_number: str) -> list[dict]:
    """Return a list of match dicts for *member_number*.

    Each dict (compatible with task_manager):
        match_name          str
        match_date          str | None   (ISO YYYY-MM-DD)
        division            str
        match_level         int | None
        practiscore_match_id str | None
        source_url          str | None
        total_competitors   int | None
        member_placement    int | None
        member_percent      float | None
        results             list[dict]
    """
    from app.services.uspsa_match_scraper import scrape_match_list as uspsa_match_list

    log = logger.bind(member_number=member_number)

    uspsa_matches = await uspsa_match_list(member_number)
    log.info("practiscore_scrape_start", uspsa_matches=len(uspsa_matches))

    if not uspsa_matches:
        return []

    results: list[dict] = []
    for match_info in uspsa_matches:
        try:
            entry = await _process_match(match_info, member_number)
            if entry:
                results.append(entry)
        except Exception as exc:
            log.warning(
                "practiscore_match_failed",
                match_name=match_info.get("match_name"),
                error=str(exc),
            )
        await asyncio.sleep(0.5)

    log.info("practiscore_scrape_complete", matches=len(results))
    return results


# ---------------------------------------------------------------------------
# Per-match processing
# ---------------------------------------------------------------------------

async def _process_match(match_info: dict, member_number: str) -> dict:
    """Build and enrich one match entry."""
    match_name = match_info.get("match_name") or ""
    log = logger.bind(match_name=match_name)

    entry: dict = {
        "match_name": match_name,
        "match_date": match_info.get("match_date"),
        "division": match_info.get("division") or "",
        "match_level": match_info.get("match_level"),
        "practiscore_match_id": None,
        "source_url": None,
        "total_competitors": None,
        "member_placement": None,
        "member_percent": None,
        "results": [],
    }

    # 1. Try direct PractiScore URL from USPSA page
    ps_url = match_info.get("practiscore_url")

    # 2. Search PractiScore by match name
    if not ps_url and match_name:
        ps_url = await _find_practiscore_url(match_name, log)

    if not ps_url:
        log.debug("no_practiscore_url_found")
        return entry  # return with null placements — match still recorded

    if not _validate_url(ps_url):
        log.warning("invalid_practiscore_url", url=ps_url)
        return entry

    entry["source_url"] = ps_url
    match_id = _extract_match_id(ps_url)
    entry["practiscore_match_id"] = match_id
    log.debug("practiscore_url_found", url=ps_url)

    # 3a. Try S3 JSON (fast, no CF)
    if match_id and _looks_like_uuid(match_id):
        json_data = await _fetch_ps_s3_json(match_id, log)
        if json_data:
            _enrich_from_json(entry, json_data, member_number)
            return entry

    # 3b. Fall back to HTML scraping the match page
    html = await _fetch_with_fallback(ps_url)
    if html and not _is_cf_challenge(html):
        _enrich_with_results(entry, html, member_number)
    else:
        log.warning("practiscore_page_blocked_or_empty", url=ps_url)

    return entry


# ---------------------------------------------------------------------------
# PractiScore URL discovery
# ---------------------------------------------------------------------------

async def _find_practiscore_url(match_name: str, log: Any) -> str | None:
    """Search practiscore.com/results for *match_name*, return match URL."""
    # Try 1: GET search URL via curl_cffi (fast, often bypasses CF)
    search_url = (
        f"https://practiscore.com/results?search={quote_plus(match_name)}"
    )
    try:
        html = await _fetch_curl_cffi(search_url)
        if html and not _is_cf_challenge(html):
            url = _extract_first_match_url(html)
            if url:
                log.debug("found_via_curl_search", url=url)
                return url
    except Exception as exc:
        log.debug("curl_search_failed", error=str(exc))

    # Try 2: Playwright search (handles CF JS challenge)
    try:
        url = await _search_practiscore_playwright(match_name)
        if url:
            log.debug("found_via_playwright_search", url=url)
            return url
    except Exception as exc:
        log.warning("playwright_search_failed", match_name=match_name, error=str(exc))

    return None


def _extract_first_match_url(html: str) -> str | None:
    """Pull the first PractiScore match result URL from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if re.search(r"/results/(new/)?[a-zA-Z0-9\-]{8,}", href):
            if "/by-member-number" in href or "/search" in href:
                continue
            if href.startswith("/"):
                href = "https://practiscore.com" + href
            if _validate_url(href):
                return href
    return None


async def _search_practiscore_playwright(match_name: str) -> str | None:
    """Use Playwright to search practiscore.com/results and return the first match URL."""
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    logger.debug("practiscore_playwright_search", match_name=match_name)

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

            try:
                await page.goto(
                    "https://practiscore.com/results",
                    wait_until="load",
                    timeout=45_000,
                )
            except PWTimeout:
                logger.debug("practiscore_results_load_timeout")

            # Wait past Cloudflare challenge
            try:
                await page.wait_for_function(
                    "() => !document.title.toLowerCase().includes('just a moment') && "
                    "!document.title.toLowerCase().includes('checking your browser')",
                    timeout=30_000,
                )
            except PWTimeout:
                logger.warning("practiscore_cf_timeout")
                return None

            await asyncio.sleep(2)

            # Find search input — try common selectors
            search_input = None
            for sel in [
                "input[type='search']",
                "input[placeholder*='search' i]",
                "input[placeholder*='match' i]",
                "#search",
                "input[name='search']",
                ".search-input",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2_000):
                        search_input = el
                        break
                except Exception:
                    continue

            if search_input is None:
                logger.warning("practiscore_search_input_not_found")
                # Try extracting links from page as-is (may have pre-loaded results)
                return _extract_first_match_url(await page.content())

            # Type search term (use up to 40 chars to avoid over-specific)
            await search_input.click()
            await search_input.fill(match_name[:40])
            await asyncio.sleep(2.5)

            html = await page.content()
            return _extract_first_match_url(html)
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# PractiScore S3 JSON fetch
# ---------------------------------------------------------------------------

async def _fetch_ps_s3_json(match_id: str, log: Any) -> dict | None:
    """Try fetching match JSON from PractiScore's S3 bucket (no CF)."""
    # Known S3 URL formats
    urls = [
        f"{_PS_S3_BASE}/{match_id}/match_{match_id}.json",
        f"{_PS_S3_BASE}/{match_id}/results.json",
    ]
    from curl_cffi.requests import AsyncSession

    async with AsyncSession() as session:
        for url in urls:
            try:
                resp = await session.get(url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    log.debug("ps_s3_json_found", url=url)
                    return data
            except Exception as exc:
                log.debug("ps_s3_json_failed", url=url, error=str(exc))

    return None


def _enrich_from_json(entry: dict, data: dict, member_number: str) -> None:
    """Parse PractiScore S3 JSON and populate entry."""
    # Top-level match metadata
    match_def = data.get("match", {})
    if not entry.get("match_name") and match_def.get("match_name"):
        entry["match_name"] = match_def["match_name"]
    if not entry.get("match_date") and match_def.get("match_date"):
        entry["match_date"] = _parse_date_str(match_def["match_date"])
    if match_def.get("match_level") and entry.get("match_level") is None:
        entry["match_level"] = _safe_int(str(match_def["match_level"]))

    # Competitor results — PS JSON structure varies; try common keys
    competitors_raw = (
        data.get("match_shooters")
        or data.get("shooters")
        or data.get("competitors")
        or []
    )
    if not competitors_raw:
        return

    member_upper = member_number.upper()
    competitors: list[dict] = []

    for shooter in competitors_raw:
        mem_num = (
            shooter.get("sh_id")  # USPSA member number key in PS JSON
            or shooter.get("sh_uid")
            or shooter.get("member_number")
            or ""
        ).strip().upper()

        name = (
            shooter.get("sh_fn", "") + " " + shooter.get("sh_ln", "")
        ).strip() or shooter.get("shooter_name") or shooter.get("name") or ""

        division = shooter.get("sh_dvp") or shooter.get("division") or entry.get("division") or ""
        classification = shooter.get("sh_cls") or shooter.get("classification")
        placement = _safe_int(str(shooter.get("sh_place") or shooter.get("placement") or ""))
        pct = _safe_float(str(shooter.get("sh_pcnt") or shooter.get("percent_of_winner") or ""))

        is_member = bool(mem_num and mem_num == member_upper) or (
            not mem_num and member_upper in name.upper()
        )

        comp = {
            "shooter_name": name,
            "member_number": mem_num or None,
            "division": division,
            "classification": classification,
            "total_points": None,
            "total_time": None,
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


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_with_fallback(url: str) -> str | None:
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
            try:
                await page.goto(url, wait_until="load", timeout=timeout_ms)
            except PWTimeout:
                logger.debug("playwright_load_timeout", url=url)

            try:
                await page.wait_for_function(
                    "() => !document.title.toLowerCase().includes('just a moment') && "
                    "!document.title.toLowerCase().includes('checking your browser')",
                    timeout=30_000,
                )
            except PWTimeout:
                logger.warning("cloudflare_challenge_timeout", url=url)

            await asyncio.sleep(2)
            html = await page.content()
            logger.debug("playwright_got_content", url=url, bytes=len(html))
            return html
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# HTML results-page parser (unchanged logic, used as fallback)
# ---------------------------------------------------------------------------

def _enrich_with_results(entry: dict, html: str, queried_member: str) -> None:
    """Parse a PractiScore match results HTML page and populate *entry*."""
    soup = BeautifulSoup(html, "html.parser")

    if entry.get("match_level") is None:
        entry["match_level"] = _extract_match_level(soup)

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

        def _get(key: str) -> str:
            idx = col.get(key)
            return texts[idx] if idx is not None and idx < len(texts) else ""

        placement = _safe_int(_get("placement"))
        name = _get("shooter_name")
        mem_num = _get("member_number")
        division = _get("division") or entry.get("division", "")
        classification = _get("classification") or None
        total_points = _safe_float(_get("total_points"))
        total_time = _safe_float(_get("total_time"))
        pct = _safe_float(_get("percent"))

        if not name:
            continue

        is_member = bool(mem_num and mem_num.upper() == member_upper) or (
            not mem_num and member_upper in name.upper()
        )

        comp = {
            "shooter_name": name,
            "member_number": mem_num.strip() or None,
            "division": division.strip(),
            "classification": classification,
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
    text = soup.get_text(" ", strip=True)
    m = re.search(r"level\s*([1-4])", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _extract_match_id(url: str) -> str | None:
    m = re.search(r"/results/(?:new/)?([a-zA-Z0-9\-]+)(?:/|$)", url)
    return m.group(1) if m else None


def _looks_like_uuid(s: str) -> bool:
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", s, re.IGNORECASE))


def _parse_date_str(raw: str) -> str | None:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
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
