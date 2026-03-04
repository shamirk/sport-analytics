"""Member API routes."""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.limiter import limiter
from app.models import ClassifierResult, CurrentClassification, Division, MatchResult, Member
from app.services.cache import CACHE_TTL, cache
from app.services.task_manager import create_job, get_pending_job, job_status, scrape_and_store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")

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
    """Thin wrapper used by BackgroundTasks to invoke the task manager."""
    db = SessionLocal()
    try:
        # Patch job_id into status before delegating (create_job already inserted it)
        await scrape_and_store(member_number, db)
    finally:
        db.close()


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

    cached = cache.get(f"analyze:{member_number}")
    if cached:
        response.status_code = 200
        return {"status": "complete", "data": cached}

    pending = get_pending_job(member_number)
    if pending:
        response.status_code = 202
        return {"status": "pending", "job_id": pending}

    job_id = create_job(member_number)
    background_tasks.add_task(_run_scrape, job_id, member_number)

    response.status_code = 202
    return {"status": "accepted", "job_id": job_id}


@router.get("/member/{member_number}")
@limiter.limit("60/minute")
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
@limiter.limit("60/minute")
async def get_member_dashboard(
    member_number: str,
    request: Request,
    refresh: bool = Query(False, description="Force cache refresh"),
    db: Session = Depends(get_db),
):
    """Return complete dashboard data, cached for CACHE_TTL seconds.

    Pass ?refresh=true to bypass and repopulate the cache.
    """
    member_number = _validate_member_number(member_number)

    cache_key = f"dashboard:{member_number}"
    if not refresh:
        cached = cache.get(cache_key)
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

    cache.set(cache_key, result, ttl=CACHE_TTL)
    return result


@router.get("/member/{member_number}/status")
@limiter.limit("60/minute")
async def get_member_status(
    member_number: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return scraping status for a member: pending/in_progress/complete/error/not_started."""
    member_number = _validate_member_number(member_number)

    member_jobs = [
        (jid, job) for jid, job in job_status.items()
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

    member = db.query(Member).filter(Member.member_number == member_number).first()
    if member and member.last_scraped_at:
        return {
            "member_number": member_number,
            "status": "complete",
            "last_scraped_at": member.last_scraped_at,
        }

    return {"member_number": member_number, "status": "not_started"}
