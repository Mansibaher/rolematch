from __future__ import annotations

import time
from collections import defaultdict, deque
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.security import create_session, hash_password, revoke_session, verify_password
from app.models import Preferences, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Simple in-process login throttling (per client IP + email). Use a shared store if running many replicas.
_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
MAX_ATTEMPTS, WINDOW_S = 8, 300


def _throttle(key: str) -> None:
    now = time.monotonic()
    q = _ATTEMPTS[key]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-in attempts. Try again in a few minutes.")
    q.append(now)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class RegisterBody(Credentials):
    display_name: str = Field(default="", max_length=120)
    timezone: str = "UTC"


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name, token, httponly=True, secure=s.cookie_secure, samesite="lax",
        max_age=s.session_ttl_hours * 3600, path="/",
    )


def user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "timezone": user.timezone,
        "is_demo": user.is_demo,
        "llm_opt_in": user.llm_opt_in,
    }


@router.post("/register", status_code=201)
def register(body: RegisterBody, request: Request, response: Response, db: DB) -> dict:
    if not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    if request.headers.get(get_settings().csrf_header) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    if body.timezone not in available_timezones():
        raise HTTPException(422, "Unknown timezone")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name, timezone=body.timezone)
    db.add(user)
    db.flush()
    db.add(Preferences(user_id=user.id, hard_filters={}, feedback_adjustments={}))
    db.commit()
    _set_cookie(response, create_session(db, user))
    return user_payload(user)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response, db: DB) -> dict:
    if request.headers.get(get_settings().csrf_header) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    _throttle(f"{request.client.host if request.client else '?'}:{body.email.lower()}")
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    _set_cookie(response, create_session(db, user))
    return user_payload(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: DB, user: CurrentUser) -> dict:
    s = get_settings()
    revoke_session(db, request.cookies.get(s.session_cookie_name))
    response.delete_cookie(s.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return user_payload(user)


class SettingsBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    timezone: str | None = None
    llm_opt_in: bool | None = None


@router.patch("/me")
def update_me(body: SettingsBody, db: DB, user: CurrentUser) -> dict:
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except Exception as exc:
            raise HTTPException(422, "Unknown timezone") from exc
        user.timezone = body.timezone
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.llm_opt_in is not None:
        if body.llm_opt_in and get_settings().llm_provider == "none":
            raise HTTPException(409, "No external AI provider is configured on this server.")
        user.llm_opt_in = body.llm_opt_in
    db.commit()
    return user_payload(user)
