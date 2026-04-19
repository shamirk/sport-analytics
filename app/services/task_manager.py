"""Background task manager for member scraping jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from app.services.cache import cache

logger = structlog.get_logger(__name__)

# job_id -> {status, member_number, started_at, completed_at, error_public, error_internal, job_type}
job_status: dict[str, dict[str, Any]] = {}

_JOB_TTL_SECONDS = 3600  # remove terminal jobs after 1 hour


def _public_error_message(exc: Exception) -> str:
    """Return a safe user-facing message for *exc*; never expose internals."""
    try:
        from sqlalchemy.exc import IntegrityError as _SAIntegrityError
        if isinstance(exc, _SAIntegrityError):
            return "Data processing error, please retry"
    except ImportError:
        pass

    try:
        from app.exceptions import RateLimitError, ScrapingError
        if isinstance(exc, (ScrapingError, RateLimitError)):
            return "Scraping failed, please retry"
    except ImportError:
        pass

    if "timeout" in type(exc).__name__.lower():
        return "Request timed out, please retry"

    return "An unexpected error occurred"


def _cleanup_expired_jobs() -> None:
    """Remove terminal jobs whose completed_at is older than _JOB_TTL_SECONDS."""
    now = datetime.now(timezone.utc)
    expired = [
        jid for jid, job in job_status.items()
        if job["status"] in ("complete", "error")
        and job.get("completed_at")
        and (now - datetime.fromisoformat(job["completed_at"])).total_seconds() > _JOB_TTL_SECONDS
    ]
    for jid in expired:
        del job_status[jid]


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
        _KNOWN_ABBREVS = {
            "Open": "OPN", "Limited": "LTD", "Limited 10": "L10",
            "Production": "PROD", "Revolver": "REV", "Single Stack": "SS",
            "Carry Optics": "CO", "PCC": "PCC", "Limited Optics": "LO",
        }
        _div_cache: dict[str, int] = {}

        def _get_division_id(name: str) -> int | None:
            if not name:
                return None
            if name in _div_cache:
                return _div_cache[name]
            div = db_session.query(Division).filter(Division.name == name).first()
            if not div:
                abbr = _KNOWN_ABBREVS.get(name, name[:10].upper().replace(" ", ""))
                # Ensure uniqueness by appending a counter if needed
                base, counter = abbr, 1
                while db_session.query(Division).filter(Division.abbreviation == abbr).first():
                    abbr = f"{base[:8]}{counter}"
                    counter += 1
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
        job_status[job_id]["error_public"] = f"Member {member_number} not found in USPSA"
        job_status[job_id]["error_internal"] = f"MemberNotFoundError: {member_number}"
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.warning("member_not_found", job_id=job_id, member_number=member_number)
    except Exception as exc:
        db_session.rollback()
        job_status[job_id]["status"] = "error"
        job_status[job_id]["error_public"] = _public_error_message(exc)
        job_status[job_id]["error_internal"] = str(exc)
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.error("scrape_failed", job_id=job_id, member_number=member_number, error=str(exc))


async def scrape_practiscore_and_store(member_number: str, db_session: Any) -> None:
    """Scrape PractiScore data for *member_number*, persist to DB and cache."""
    from datetime import datetime as dt

    from app.models import Member
    from app.models.practiscore_match import PractiscoreMatch
    from app.models.practiscore_result import PractiscoreResult
    from app.services.practiscore_scraper import scrape_member_matches

    job_id = _find_pending_job_by_type(member_number, "practiscore")
    if job_id is None:
        return

    job_status[job_id]["status"] = "in_progress"
    job_status[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()

    try:
        # Ensure the member row exists
        member = db_session.query(Member).filter(Member.member_number == member_number).first()
        if not member:
            member = Member(member_number=member_number)
            db_session.add(member)
            db_session.flush()

        matches = await scrape_member_matches(member_number)

        # Clear existing PractiScore data for this member
        existing_match_ids = [
            row.id
            for row in db_session.query(PractiscoreMatch)
            .filter(PractiscoreMatch.member_id == member.id)
            .all()
        ]
        if existing_match_ids:
            db_session.query(PractiscoreResult).filter(
                PractiscoreResult.match_id.in_(existing_match_ids)
            ).delete(synchronize_session=False)
        db_session.query(PractiscoreMatch).filter(
            PractiscoreMatch.member_id == member.id
        ).delete()

        for m in matches:
            ps_match = PractiscoreMatch(
                member_id=member.id,
                match_name=m["match_name"],
                match_date=_parse_iso_date(m.get("match_date")),
                match_level=m.get("match_level"),
                division=m.get("division") or "",
                practiscore_match_id=m.get("practiscore_match_id"),
                source_url=m.get("source_url"),
                total_competitors=m.get("total_competitors"),
            )
            db_session.add(ps_match)
            db_session.flush()

            for r in m.get("results", []):
                db_session.add(PractiscoreResult(
                    match_id=ps_match.id,
                    shooter_name=r.get("shooter_name") or "",
                    member_number=r.get("member_number"),
                    division=r.get("division") or "",
                    classification=r.get("classification"),
                    total_points=r.get("total_points"),
                    total_time=r.get("total_time"),
                    percent_of_winner=r.get("percent_of_winner"),
                    placement=r.get("placement"),
                    is_queried_member=bool(r.get("is_queried_member")),
                ))

        db_session.commit()

        cache.delete(f"practiscore:{member_number}")

        job_status[job_id]["status"] = "complete"
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(
            "practiscore_scrape_complete",
            job_id=job_id,
            member_number=member_number,
            matches=len(matches),
        )

    except Exception as exc:
        db_session.rollback()
        job_status[job_id]["status"] = "error"
        job_status[job_id]["error_public"] = _public_error_message(exc)
        job_status[job_id]["error_internal"] = str(exc)
        job_status[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.error(
            "practiscore_scrape_failed",
            job_id=job_id,
            member_number=member_number,
            error=str(exc),
        )


def _parse_iso_date(date_str: str | None):
    if not date_str:
        return None
    from datetime import date
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def create_job(member_number: str, job_type: str = "uspsa") -> str:
    """Register a new pending job and return its job_id."""
    _cleanup_expired_jobs()
    job_id = str(uuid.uuid4())
    job_status[job_id] = {
        "status": "pending",
        "member_number": member_number,
        "job_type": job_type,
        "started_at": None,
        "completed_at": None,
        "error_public": None,
        "error_internal": None,
    }
    return job_id


def get_pending_job(member_number: str, job_type: str = "uspsa") -> str | None:
    """Return the job_id of an existing pending job for *member_number*, or None."""
    return _find_pending_job_by_type(member_number, job_type)


def _find_pending_job(member_number: str) -> str | None:
    """Legacy helper: find pending USPSA job."""
    return _find_pending_job_by_type(member_number, "uspsa")


def _find_pending_job_by_type(member_number: str, job_type: str) -> str | None:
    for jid, job in job_status.items():
        if (
            job["member_number"] == member_number
            and job.get("job_type", "uspsa") == job_type
            and job["status"] == "pending"
        ):
            return jid
    return None
