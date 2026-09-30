import re

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
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


# No "@": that's how login tells a username from an email address.
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


class SignupIn(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(max_length=128)

    @field_validator("username")
    @classmethod
    def _username_shape(cls, v: str) -> str:
        v = v.strip()
        if not USERNAME_PATTERN.fullmatch(v):
            raise ValueError(
                "Username must be 3-30 characters: letters, numbers, dot, dash or underscore"
            )
        return v

    @model_validator(mode="after")
    def _passwords_match(self) -> "SignupIn":
        # Checked server-side too: the API must not rely on the browser for this.
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class LoginIn(BaseModel):
    # "email" is still accepted as the field name for older clients.
    identifier: str = Field(
        min_length=1, max_length=320, validation_alias=AliasChoices("identifier", "email")
    )
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    username: str


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
    return UserOut(id=str(user.id), email=user.email, username=user.username)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, response: Response, session: SessionDep) -> UserOut:
    email = body.email.lower()
    clash = await session.scalar(
        select(User).where(
            or_(User.email == email, func.lower(User.username) == body.username.lower())
        )
    )
    if clash is not None:
        field = "email" if clash.email == email else "username"
        raise HTTPException(status.HTTP_409_CONFLICT, f"That {field} is already taken")
    user = User(email=email, username=body.username, password_hash=hash_password(body.password))
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:  # lost a race with a concurrent signup
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That email or username is taken") from None
    # Every user starts with one workspace so the dashboard is never empty.
    await create_workspace(session, user, "My Workspace")
    await session.commit()
    _set_session_cookie(response, user)
    return _out(user)


@router.post("/login")
async def login(body: LoginIn, response: Response, session: SessionDep) -> UserOut:
    identifier = body.identifier.strip().lower()
    # Usernames can't contain "@", so this routing is unambiguous.
    column = User.email if "@" in identifier else func.lower(User.username)
    user = await session.scalar(select(User).where(column == identifier))
    if user is None:
        burn_verify_time(body.password)
    if user is None or not verify_password(body.password, user.password_hash):
        # Same message either way: don't reveal which accounts exist.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username/email or password")
    _set_session_cookie(response, user)
    return _out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return _out(user)
