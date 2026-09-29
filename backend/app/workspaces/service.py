import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Workspace, WorkspaceMember


async def create_workspace(session: AsyncSession, owner: User, name: str) -> Workspace:
    workspace = Workspace(name=name, owner_id=owner.id)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
    await session.flush()
    return workspace


async def list_workspaces(session: AsyncSession, user: User) -> list[Workspace]:
    result = await session.scalars(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at)
    )
    return list(result)


async def get_member_workspace(
    session: AsyncSession, user: User, workspace_id: uuid.UUID
) -> Workspace | None:
    """The workspace, only if `user` is a member. The single gate for tenancy."""
    return await session.scalar(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user.id)
    )
