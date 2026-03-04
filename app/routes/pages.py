"""HTML page routes using Jinja2 templates."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/dashboard/{member_number}", response_class=HTMLResponse)
async def dashboard(request: Request, member_number: str):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "member_number": member_number.upper()},
    )
