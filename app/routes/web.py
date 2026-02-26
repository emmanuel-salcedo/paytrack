from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.models import AppSettings, PaySchedule

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
web_router = APIRouter(tags=["web"])


@web_router.get("/")
def home(request: Request, db: Session = Depends(get_db_session)):
    schedule = db.query(PaySchedule).first()
    app_settings = db.query(AppSettings).first()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "schedule": schedule,
            "app_settings": app_settings,
        },
    )

