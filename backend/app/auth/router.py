from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.deps import CurrentUser, SessionDep
from app.auth.security import (
    burn_verify_time,
    create_session_token,
    hash_password,
    verify_password,
)
from app.config import get_settings
from app.models import User
from app.workspaces.service import create_workspace

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str


def _set_session_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.cookie_name,
        create_session_token(user.id),
        max_age=settings.jwt_ttl_hours * 3600,
        httponly=True,  # not readable from JS
        secure=settings.is_production,  # HTTPS-only in prod
        samesite="lax",  # not sent on cross-site POSTs (CSRF)
        path="/",
    )


def _out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: Credentials, response: Response, session: SessionDep) -> UserOut:
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email exists") from None
    # Every user starts with one workspace so the dashboard is never empty.
    await create_workspace(session, user, "My Workspace")
    await session.commit()
    _set_session_cookie(response, user)
    return _out(user)


@router.post("/login")
async def login(body: Credentials, response: Response, session: SessionDep) -> UserOut:
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        burn_verify_time(body.password)
    if user is None or not verify_password(body.password, user.password_hash):
        # Same message either way: don't reveal which emails are registered.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    _set_session_cookie(response, user)
    return _out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return _out(user)
