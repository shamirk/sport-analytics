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
    from datetime import datetime as dt

    from app.models import ClassifierResult, CurrentClassification, Division, Member
    from app.services.uspsa_scraper import MemberNotFoundError, USPSAScraper

    job_id = _find_pending_job(member_number)
    if job_id is None:
        return

    job_status[job_id]["status"] = "in_progress"
    job_status[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()

    try:
        scraper = USPSAScraper()
        data = await scraper.scrape_member(member_number)

        # ── Upsert Member ────────────────────────────────────────────────────
        member = db_session.query(Member).filter(Member.member_number == member_number).first()
        if not member:
            member = Member(member_number=member_number)
            db_session.add(member)
            db_session.flush()
        member.last_scraped_at = datetime.now(timezone.utc)
        db_session.flush()

        # ── Helper: get or create Division ───────────────────────────────────
        _div_cache: dict[str, int] = {}

        def _get_division_id(name: str) -> int | None:
            if not name:
                return None
            if name in _div_cache:
                return _div_cache[name]
            div = db_session.query(Division).filter(Division.name == name).first()
            if not div:
                abbr = "".join(w[0] for w in name.split())[:10]
                div = Division(name=name, abbreviation=abbr)
                db_session.add(div)
                db_session.flush()
            _div_cache[name] = div.id
            return div.id

        # ── Persist CurrentClassifications ───────────────────────────────────
        db_session.query(CurrentClassification).filter(
            CurrentClassification.member_id == member.id
        ).delete()

        for cls_data in data.get("current_classifications", []):
            div_id = _get_division_id(cls_data.get("division", ""))
            if div_id is None:
                continue
            db_session.add(CurrentClassification(
                member_id=member.id,
                division_id=div_id,
                classification_class=cls_data.get("class") or "U",
                percentage=cls_data.get("percentage"),
            ))

        # ── Persist ClassifierResults ─────────────────────────────────────────
        db_session.query(ClassifierResult).filter(
            ClassifierResult.member_id == member.id
        ).delete()

        for score in data.get("classifier_scores", []):
            div_id = _get_division_id(score.get("division", ""))
            if div_id is None:
                continue

            match_date = None
            for fmt in ("%m/%d/%y", "%m/%d/%Y"):
                try:
                    match_date = dt.strptime(score.get("date") or "", fmt).date()
                    break
                except ValueError:
                    continue

            db_session.add(ClassifierResult(
                member_id=member.id,
                division_id=div_id,
                classifier_number=score.get("classifier") or "",
                match_name=score.get("club"),
                match_date=match_date,
                hit_factor=score.get("hit_factor"),
                percentage=score.get("percentage"),
                classification_at_time=score.get("used"),
            ))

        db_session.commit()

        cache.set(f"analyze:{member_number}", data)
        cache.delete(f"dashboard:{member_number}")

        job_status[job_id]["status"] = "complete"
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("scrape_complete", job_id=job_id, member_number=member_number)

    except MemberNotFoundError:
        job_status[job_id]["status"] = "error"
        job_status[job_id]["error"] = f"Member {member_number} not found in USPSA"
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.warning("member_not_found", job_id=job_id, member_number=member_number)
    except Exception as exc:
        db_session.rollback()
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
