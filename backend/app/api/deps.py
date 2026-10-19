from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import resolve_session
from app.db.session import get_db
from app.models import User

DB = Annotated[Session, Depends(get_db)]

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def current_user(request: Request, db: DB) -> User:
    settings = get_settings()
    # CSRF defence for cookie auth: state-changing requests must carry a custom header, which a cross-site
    # HTML form cannot set and cross-origin scripts cannot send without passing CORS.
    if request.method not in SAFE_METHODS and request.headers.get(settings.csrf_header) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    user = resolve_session(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def not_found(what: str = "Resource") -> HTTPException:
    # Ownership failures return 404 (not 403) so IDs of other users' records are not confirmed.
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
