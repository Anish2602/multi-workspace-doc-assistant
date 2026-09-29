import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.auth.deps import CurrentUser, SessionDep
from app.models import Workspace
from app.workspaces.service import get_member_workspace


async def get_active_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Workspace:
    """Resolve `{workspace_id}` from the URL, but only if the user is a member.

    Every workspace-scoped route depends on this. Non-members get 404 (not 403)
    so workspace ids can't be probed for existence.
    """
    workspace = await get_member_workspace(session, user, workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    return workspace


ActiveWorkspace = Annotated[Workspace, Depends(get_active_workspace)]
