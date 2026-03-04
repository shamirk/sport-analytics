"""Member API routes."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.limiter import limiter
from app.models import ClassifierResult, CurrentClassification, Division, MatchResult, Member

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

# Cache: key -> {"data": any, "expires_at": float (monotonic)}
_cache: dict[str, dict] = {}
CACHE_TTL_SECONDS: float = 86_400  # 24 hours

# Job tracking: job_id -> {"status": str, "member_number": str, "error"?: str, "data"?: any}
_jobs: dict[str, dict] = {}

MEMBER_NUMBER_RE = re.compile(r"^[A-Za-z0-9]{5,10}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_member_number(member_number: str) -> str:
    if not MEMBER_NUMBER_RE.match(member_number):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Invalid member number",
                "detail": "Member number must be 5-10 alphanumeric characters",
                "code": 422,
            },
        )
    return member_number.upper()


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and entry["expires_at"] > time.monotonic():
        return entry["data"]
    if entry:
        del _cache[key]
    return None


def _cache_set(key: str, data: Any, ttl: float = CACHE_TTL_SECONDS) -> None:
    _cache[key] = {"data": data, "expires_at": time.monotonic() + ttl}


def _get_member_or_404(member_number: str, db: Session) -> Member:
    member = db.query(Member).filter(Member.member_number == member_number).first()
    if not member:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Member not found",
                "detail": f"No data found for member {member_number}",
                "code": 404,
            },
        )
    return member


async def _run_scrape(job_id: str, member_number: str) -> None:
    from datetime import datetime, timezone

    from app.services.uspsa_scraper import MemberNotFoundError, USPSAScraper

    try:
        scraper = USPSAScraper()
        data = await scraper.scrape_member(member_number)

        db = SessionLocal()
        try:
            member = db.query(Member).filter(Member.member_number == member_number).first()
            if not member:
                member = Member(member_number=member_number)
                db.add(member)
                db.flush()
            member.last_scraped_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

        _cache_set(f"analyze:{member_number}", data)
        _jobs[job_id]["status"] = "complete"
        _jobs[job_id]["data"] = data
        logger.info("scrape_complete", job_id=job_id, member_number=member_number)

    except MemberNotFoundError:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = f"Member {member_number} not found in USPSA"
        logger.warning("member_not_found", job_id=job_id, member_number=member_number)
    except Exception as exc:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        logger.error("scrape_failed", job_id=job_id, member_number=member_number, error=str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/analyze/{member_number}")
@limiter.limit("10/minute")
async def analyze_member(
    member_number: str,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Trigger a scrape or return cached member data.

    Returns 200 with full data if cached, 202 with job_id if scraping in progress.
    """
    member_number = _validate_member_number(member_number)

    cached = _cache_get(f"analyze:{member_number}")
    if cached:
        response.status_code = 200
        return {"status": "complete", "data": cached}

    # Return existing pending job if one exists
    for jid, job in _jobs.items():
        if job["member_number"] == member_number and job["status"] == "pending":
            response.status_code = 202
            return {"status": "pending", "job_id": jid}

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "member_number": member_number}
    background_tasks.add_task(_run_scrape, job_id, member_number)

    response.status_code = 202
    return {"status": "accepted", "job_id": job_id}


@router.get("/member/{member_number}")
@limiter.limit("10/minute")
async def get_member(
    member_number: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return stored member data from DB, 404 if not found."""
    member_number = _validate_member_number(member_number)
    member = _get_member_or_404(member_number, db)

    classifications = (
        db.query(CurrentClassification, Division)
        .join(Division, Division.id == CurrentClassification.division_id)
        .filter(CurrentClassification.member_id == member.id)
        .all()
    )

    return {
        "member_number": member.member_number,
        "last_scraped_at": member.last_scraped_at,
        "created_at": member.created_at,
        "current_classifications": [
            {
                "division": div.name,
                "division_abbr": div.abbreviation,
                "class": cc.classification_class,
                "percentage": float(cc.percentage) if cc.percentage is not None else None,
            }
            for cc, div in classifications
        ],
    }


@router.get("/member/{member_number}/dashboard")
@limiter.limit("10/minute")
async def get_member_dashboard(
    member_number: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return complete dashboard data, cached for 24 hours."""
    member_number = _validate_member_number(member_number)

    cache_key = f"dashboard:{member_number}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    member = _get_member_or_404(member_number, db)

    classifications = (
        db.query(CurrentClassification, Division)
        .join(Division, Division.id == CurrentClassification.division_id)
        .filter(CurrentClassification.member_id == member.id)
        .all()
    )

    classifier_results = (
        db.query(ClassifierResult, Division)
        .join(Division, Division.id == ClassifierResult.division_id)
        .filter(ClassifierResult.member_id == member.id)
        .order_by(ClassifierResult.match_date.desc())
        .all()
    )

    match_results = (
        db.query(MatchResult, Division)
        .join(Division, Division.id == MatchResult.division_id)
        .filter(MatchResult.member_id == member.id)
        .order_by(MatchResult.match_date.desc())
        .all()
    )

    overview = {
        "member_number": member.member_number,
        "last_scraped_at": member.last_scraped_at,
        "classifications": [
            {
                "division": div.name,
                "class": cc.classification_class,
                "percentage": float(cc.percentage) if cc.percentage is not None else None,
            }
            for cc, div in classifications
        ],
    }

    # Time series: classifier scores over time, grouped by division
    time_series: dict[str, list] = {}
    for cr, div in classifier_results:
        entries = time_series.setdefault(div.name, [])
        entries.append({
            "date": cr.match_date.isoformat() if cr.match_date else None,
            "percentage": float(cr.percentage) if cr.percentage is not None else None,
            "hit_factor": float(cr.hit_factor) if cr.hit_factor is not None else None,
            "classifier_number": cr.classifier_number,
        })

    division_stats = {
        div.name: {
            "class": cc.classification_class,
            "percentage": float(cc.percentage) if cc.percentage is not None else None,
        }
        for cc, div in classifications
    }

    classifier_breakdown = [
        {
            "classifier_number": cr.classifier_number,
            "classifier_name": cr.classifier_name,
            "match_date": cr.match_date.isoformat() if cr.match_date else None,
            "hit_factor": float(cr.hit_factor) if cr.hit_factor is not None else None,
            "percentage": float(cr.percentage) if cr.percentage is not None else None,
            "division": div.name,
        }
        for cr, div in classifier_results
    ]

    match_stats = [
        {
            "match_name": mr.match_name,
            "match_date": mr.match_date.isoformat() if mr.match_date else None,
            "division": div.name,
            "placement": mr.placement,
            "total_competitors": mr.total_competitors,
            "percent_finish": float(mr.percent_finish) if mr.percent_finish is not None else None,
            "match_level": mr.match_level,
        }
        for mr, div in match_results
    ]

    result = {
        "overview": overview,
        "time_series": time_series,
        "division_stats": division_stats,
        "classifier_breakdown": classifier_breakdown,
        "match_stats": match_stats,
    }

    _cache_set(cache_key, result)
    return result


@router.get("/member/{member_number}/status")
@limiter.limit("10/minute")
async def get_member_status(
    member_number: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return scraping status for a member: pending/complete/error/not_started."""
    member_number = _validate_member_number(member_number)

    # Find jobs for this member (most recent by job_id, which is uuid4 time-ordered)
    member_jobs = [
        (jid, job) for jid, job in _jobs.items()
        if job["member_number"] == member_number
    ]

    if member_jobs:
        jid, job = max(member_jobs, key=lambda x: x[0])
        resp: dict = {
            "member_number": member_number,
            "status": job["status"],
            "job_id": jid,
        }
        if job["status"] == "error":
            resp["error"] = job.get("error", "Unknown error")
        return resp

    # No jobs — check DB for prior scrape
    member = db.query(Member).filter(Member.member_number == member_number).first()
    if member and member.last_scraped_at:
        return {
            "member_number": member_number,
            "status": "complete",
            "last_scraped_at": member.last_scraped_at,
        }

    return {"member_number": member_number, "status": "not_started"}
