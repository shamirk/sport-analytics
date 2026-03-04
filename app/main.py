import time

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.exceptions import (
    MemberNotFoundError,
    RateLimitError,
    ScrapingError,
    ValidationError,
)
from app.limiter import limiter
from app.logging_config import configure_logging
from app.routes import health, members

configure_logging()
log = structlog.get_logger()

app = FastAPI(title="USPSA Analytics", version="0.1.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    log.info("request.started", method=request.method, path=request.url.path)
    response = await call_next(request)
    duration = time.perf_counter() - start
    log.info(
        "request.finished",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_s=round(duration, 4),
    )
    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(MemberNotFoundError)
async def member_not_found_handler(request: Request, exc: MemberNotFoundError):
    log.warning("member.not_found", member_number=exc.member_number)
    return JSONResponse(
        status_code=404,
        content={"error": "MemberNotFound", "detail": str(exc), "code": "MEMBER_NOT_FOUND"},
    )


@app.exception_handler(ScrapingError)
async def scraping_error_handler(request: Request, exc: ScrapingError):
    log.error("scraping.error", detail=str(exc), status_code=exc.status_code)
    return JSONResponse(
        status_code=502,
        content={"error": "ScrapingError", "detail": str(exc), "code": "SCRAPING_ERROR"},
    )


@app.exception_handler(RateLimitError)
async def rate_limit_handler(request: Request, exc: RateLimitError):
    log.warning("scraping.rate_limited")
    return JSONResponse(
        status_code=429,
        content={"error": "RateLimitError", "detail": str(exc), "code": "RATE_LIMITED"},
    )


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    log.warning("validation.error", field=exc.field, detail=str(exc))
    return JSONResponse(
        status_code=422,
        content={"error": "ValidationError", "detail": str(exc), "code": "VALIDATION_ERROR"},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    log.exception("unhandled.error", detail=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "InternalError", "detail": "An unexpected error occurred", "code": "INTERNAL_ERROR"},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(members.router)
