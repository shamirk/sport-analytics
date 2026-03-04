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
    """Parse the Classifications table.

    Actual page structure (Table 3):
      Row 0: ['Classifications']  ← single header cell
      Row N: ['Open', 'Class: U', 'Pct: 0.0000', 'High Pct: 0.0000']
    """
    results = []
    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if not rows:
            continue
        # Identify by single-cell header "Classifications"
        if rows[0] != ["Classifications"]:
            continue
        for row in rows[1:]:
            if len(row) < 3:
                continue
            # row[0] = division name, row[1] = "Class: X", row[2] = "Pct: X.XXXX"
            division = row[0].strip()
            cls = row[1].replace("Class:", "").strip() if len(row) > 1 else None
            pct = _safe_float(row[2].replace("Pct:", "").strip()) if len(row) > 2 else None
            high_pct = _safe_float(row[3].replace("High Pct:", "").strip()) if len(row) > 3 else None
            if division:
                results.append({
                    "division": division,
                    "class": cls,
                    "percentage": pct,
                    "high_percentage": high_pct,
                })
        break
    return results


def _parse_classifier_scores(soup: BeautifulSoup) -> list[dict]:
    """Parse per-division classifier score tables.

    Actual page structure (one table per division, e.g. Tables 5-7):
      Row 0: ['Limited Optics Classifiers(Click to Expand)']  ← division title
      Row 1: ['Date', 'Number', 'Club', 'F', 'Percent', 'HF', 'Entered', 'Source']
      Row N: ['3/01/26', '99-11', 'Custer Sportsmens Club', 'Y', '66.7947', '7.0773', ...]
    """
    results = []
    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if len(rows) < 2:
            continue
        title = rows[0][0] if rows[0] else ""
        if "Classifiers" not in title:
            continue

        # Extract division name from title like "Limited Optics Classifiers(Click to Expand)"
        division = re.sub(r"\s*Classifiers.*", "", title).strip()

        # Row 1 must be the column header row
        header = [h.lower() for h in rows[1]]
        col = _col_map(header, {
            "date": ["date"],
            "classifier": ["number"],
            "club": ["club"],
            "used": ["f"],
            "percentage": ["percent"],
            "hit_factor": ["hf"],
            "entered": ["entered"],
            "source": ["source"],
        })

        for row in rows[2:]:
            if len(row) < 4:
                continue
            entry: dict = {"division": division}
            for key, idx in col.items():
                if idx < len(row):
                    val = row[idx]
                    if key in ("hit_factor", "percentage"):
                        entry[key] = _safe_float(val)
                    else:
                        entry[key] = val
            results.append(entry)

    return results


def _parse_match_results(soup: BeautifulSoup) -> list[dict]:
    """USPSA classification page does not include a match results section.
    Returns empty list — match results would require a separate endpoint."""
    return []


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
