"""Background task manager for member scraping jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from app.services.cache import cache

logger = structlog.get_logger(__name__)

# job_id -> {status, member_number, started_at, completed_at, error}
job_status: dict[str, dict[str, Any]] = {}


async def scrape_and_store(member_number: str, db_session: Any) -> None:
    """Scrape USPSA data for *member_number*, persist to DB and cache."""
    from app.models import Member
    from app.services.uspsa_scraper import MemberNotFoundError, USPSAScraper

    job_id = _find_pending_job(member_number)
    if job_id is None:
        return

    job_status[job_id]["status"] = "in_progress"
    job_status[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()

    try:
        scraper = USPSAScraper()
        data = await scraper.scrape_member(member_number)

        member = db_session.query(Member).filter(Member.member_number == member_number).first()
        if not member:
            member = Member(member_number=member_number)
            db_session.add(member)
            db_session.flush()
        member.last_scraped_at = datetime.now(timezone.utc)
        db_session.commit()

        cache.set(f"analyze:{member_number}", data)

        job_status[job_id]["status"] = "complete"
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("scrape_complete", job_id=job_id, member_number=member_number)

    except MemberNotFoundError:
        job_status[job_id]["status"] = "error"
        job_status[job_id]["error"] = f"Member {member_number} not found in USPSA"
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.warning("member_not_found", job_id=job_id, member_number=member_number)
    except Exception as exc:
        job_status[job_id]["status"] = "error"
        job_status[job_id]["error"] = str(exc)
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.error("scrape_failed", job_id=job_id, member_number=member_number, error=str(exc))


def create_job(member_number: str) -> str:
    """Register a new pending job and return its job_id."""
    job_id = str(uuid.uuid4())
    job_status[job_id] = {
        "status": "pending",
        "member_number": member_number,
        "started_at": None,
        "completed_at": None,
        "error": None,
    }
    return job_id


def get_pending_job(member_number: str) -> str | None:
    """Return the job_id of an existing pending job for *member_number*, or None."""
    return _find_pending_job(member_number)


def _find_pending_job(member_number: str) -> str | None:
    for jid, job in job_status.items():
        if job["member_number"] == member_number and job["status"] == "pending":
            return jid
    return None
