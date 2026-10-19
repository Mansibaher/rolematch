from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.security import verify_password
from app.services.account import delete_user_data

router = APIRouter(prefix="/api/account", tags=["account"])


class DeleteBody(BaseModel):
    password: str
    confirm: str


@router.post("/delete")
def delete_account(body: DeleteBody, request: Request, response: Response, db: DB, user: CurrentUser) -> dict:
    if body.confirm != "DELETE":
        raise HTTPException(422, 'Type "DELETE" to confirm.')
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(403, "Password is incorrect.")
    result = delete_user_data(db, user)
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"ok": True} | result
