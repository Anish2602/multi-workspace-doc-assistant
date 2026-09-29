from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.auth.deps import CurrentUser, SessionDep
from app.models import Workspace
from app.workspaces.deps import ActiveWorkspace
from app.workspaces.service import create_workspace, list_workspaces

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    created_at: datetime


def _out(ws: Workspace) -> WorkspaceOut:
    return WorkspaceOut(id=str(ws.id), name=ws.name, created_at=ws.created_at)


@router.get("")
async def list_mine(user: CurrentUser, session: SessionDep) -> list[WorkspaceOut]:
    return [_out(ws) for ws in await list_workspaces(session, user)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: WorkspaceIn, user: CurrentUser, session: SessionDep) -> WorkspaceOut:
    workspace = await create_workspace(session, user, body.name.strip())
    await session.commit()
    await session.refresh(workspace)
    return _out(workspace)


@router.get("/{workspace_id}")
async def get_one(workspace: ActiveWorkspace) -> WorkspaceOut:
    return _out(workspace)
