"""USPSA match history scraper.

The USPSA classification page does not expose a match participation list —
it only shows per-division classifier scores.  This module extracts a
synthetic match list from the classifier_scores data that the existing
USPSAScraper already parses: each unique (club/match_name, date) pair
becomes one entry.

If USPSA ever adds a proper match-history endpoint, the scrape_match_list()
function can be replaced without touching callers.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

import structlog

from app.services.uspsa_scraper import USPSAScraper

logger = structlog.get_logger(__name__)


async def scrape_match_list(member_number: str) -> list[dict]:
    """Return a deduplicated list of matches for *member_number*.

    Each dict contains:
        match_name  (str)
        match_date  (str | None, ISO format YYYY-MM-DD)
        division    (str)
        match_level (int | None)   — None (not available from classifiers)

    Matches are de-duplicated by (match_name, match_date, division).
    """
    scraper = USPSAScraper()
    try:
        data = await scraper.scrape_member(member_number)
    except Exception as exc:
        logger.warning("uspsa_match_list_failed", member_number=member_number, error=str(exc))
        return []

    scores = data.get("classifier_scores", [])
    seen: set[tuple] = set()
    matches: list[dict] = []

    for score in scores:
        match_name = score.get("club") or ""
        division = score.get("division") or ""
        raw_date = score.get("date") or ""

        match_date: str | None = None
        for fmt in ("%m/%d/%y", "%m/%d/%Y"):
            try:
                match_date = datetime.strptime(raw_date, fmt).date().isoformat()
                break
            except ValueError:
                continue

        key = (match_name.strip().lower(), match_date, division)
        if key in seen or not match_name:
            continue
        seen.add(key)

        matches.append(
            {
                "match_name": match_name.strip(),
                "match_date": match_date,
                "division": division,
                "match_level": None,
            }
        )

    logger.info(
        "uspsa_match_list_complete",
        member_number=member_number,
        count=len(matches),
    )
    return matches
