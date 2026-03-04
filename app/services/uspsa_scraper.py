"""USPSA scraper using curl_cffi (primary) and Playwright (fallback) for Cloudflare bypass."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)

BASE_URL = "https://uspsa.org/classification/{member_number}"


class MemberNotFoundError(Exception):
    """Raised when a USPSA member number does not exist (404)."""

    def __init__(self, member_number: str) -> None:
        self.member_number = member_number
        super().__init__(f"USPSA member not found: {member_number}")


class USPSAScraper:
    def __init__(self, timeout: int = 30, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries
        self.log = logger.bind(scraper="uspsa")

    async def scrape_member(self, member_number: str) -> dict:
        url = BASE_URL.format(member_number=member_number)
        self.log.info("scraping_member", member_number=member_number, url=url)

        html = await self._fetch_with_retry(url, member_number)
        result = self._parse_page(html, member_number)
        self.log.info(
            "scrape_complete",
            member_number=member_number,
            classifications=len(result.get("current_classifications", [])),
            scores=len(result.get("classifier_scores", [])),
        )
        return result

    async def _fetch_with_retry(self, url: str, member_number: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                return await self._fetch_with_curl_cffi(url)
            except MemberNotFoundError:
                raise
            except Exception as exc:
                last_exc = exc
                self.log.warning(
                    "curl_cffi_failed",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < self.retries:
                    await asyncio.sleep(2 ** (attempt - 1))

        self.log.info("falling_back_to_playwright", url=url)
        try:
            return await self._fetch_with_playwright(url)
        except MemberNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"All fetch strategies failed for {url}: {exc}"
            ) from last_exc

    async def _fetch_with_curl_cffi(self, url: str) -> str:
        from curl_cffi.requests import AsyncSession

        self.log.debug("fetch_curl_cffi", url=url)
        async with AsyncSession() as session:
            response = await session.get(
                url,
                impersonate="chrome120",
                timeout=self.timeout,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            if response.status_code == 404:
                member_number = _extract_member_number(url)
                raise MemberNotFoundError(member_number)
            if response.status_code == 403:
                raise RuntimeError("Cloudflare blocked (403) — will retry")
            response.raise_for_status()
            return response.text

    async def _fetch_with_playwright(self, url: str) -> str:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout

        self.log.debug("fetch_playwright", url=url)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                )
                response = await page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                if response and response.status == 404:
                    member_number = _extract_member_number(url)
                    raise MemberNotFoundError(member_number)

                # Wait for Cloudflare challenge to resolve (up to 30s)
                try:
                    await page.wait_for_function(
                        "() => !document.title.includes('Just a moment') && "
                        "!document.title.includes('Checking your browser')",
                        timeout=30_000,
                    )
                except PWTimeout:
                    self.log.warning("cloudflare_challenge_timeout", url=url)

                return await page.content()
            finally:
                await browser.close()

    def _parse_page(self, html: str, member_number: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        return {
            "member_number": member_number,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "current_classifications": _parse_classifications(soup),
            "classifier_scores": _parse_classifier_scores(soup),
            "match_results": _parse_match_results(soup),
        }


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _extract_member_number(url: str) -> str:
    m = re.search(r"/classification/(.+)$", url)
    return m.group(1) if m else url


def _table_rows(table: Any) -> list[list[str]]:
    """Return rows as lists of stripped cell text."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def _parse_classifications(soup: BeautifulSoup) -> list[dict]:
    """Parse the Classification Summary table."""
    results = []
    # Look for tables with classification data (division + class + pct columns)
    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if not rows:
            continue
        header = [h.lower() for h in rows[0]]
        if not any("division" in h or "class" in h for h in header):
            continue

        # Try to map columns
        col = _col_map(header, {
            "division": ["division", "div"],
            "class": ["class", "classification"],
            "pct": ["pct", "percent", "%", "percentage"],
        })
        for row in rows[1:]:
            if len(row) <= max(col.values(), default=-1):
                continue
            entry: dict = {}
            if "division" in col:
                entry["division"] = row[col["division"]]
            if "class" in col:
                entry["class"] = row[col["class"]]
            if "pct" in col:
                entry["percentage"] = _safe_float(row[col["pct"]])
            if entry:
                results.append(entry)
        if results:
            break
    return results


def _parse_classifier_scores(soup: BeautifulSoup) -> list[dict]:
    """Parse the Classifier History table."""
    results = []
    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if not rows:
            continue
        header = [h.lower() for h in rows[0]]
        if not any("classifier" in h or "hit factor" in h for h in header):
            continue

        col = _col_map(header, {
            "date": ["date"],
            "match": ["match", "match name"],
            "classifier": ["classifier", "classifier #", "classifier number", "number"],
            "hit_factor": ["hit factor", "hf"],
            "points": ["points", "pts"],
            "percentage": ["pct", "percent", "%", "percentage"],
            "division": ["division", "div"],
            "class": ["class", "classification"],
        })
        for row in rows[1:]:
            entry: dict = {}
            for key, idx in col.items():
                if idx < len(row):
                    val = row[idx]
                    if key in ("hit_factor", "percentage"):
                        entry[key] = _safe_float(val)
                    elif key == "points":
                        entry[key] = _safe_int(val)
                    else:
                        entry[key] = val
            if entry:
                results.append(entry)
        if results:
            break
    return results


def _parse_match_results(soup: BeautifulSoup) -> list[dict]:
    """Parse the Match Results section if present."""
    results = []
    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if not rows:
            continue
        header = [h.lower() for h in rows[0]]
        if not any("match" in h for h in header) or any("classifier" in h for h in header):
            continue

        col = _col_map(header, {
            "date": ["date"],
            "match": ["match", "match name"],
            "division": ["division", "div"],
            "class": ["class"],
            "place": ["place", "rank"],
            "score": ["score", "pct", "percent"],
        })
        for row in rows[1:]:
            entry: dict = {}
            for key, idx in col.items():
                if idx < len(row):
                    val = row[idx]
                    if key == "score":
                        entry[key] = _safe_float(val)
                    elif key == "place":
                        entry[key] = _safe_int(val)
                    else:
                        entry[key] = val
            if entry:
                results.append(entry)
        if results:
            break
    return results


def _col_map(header: list[str], wanted: dict[str, list[str]]) -> dict[str, int]:
    """Map wanted field names to column indices based on possible header strings."""
    mapping: dict[str, int] = {}
    for field, candidates in wanted.items():
        for i, h in enumerate(header):
            if any(c in h for c in candidates):
                mapping[field] = i
                break
    return mapping


def _safe_float(val: str) -> float | None:
    try:
        return float(val.replace("%", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _safe_int(val: str) -> int | None:
    try:
        return int(re.sub(r"[^\d]", "", val))
    except (ValueError, AttributeError):
        return None
