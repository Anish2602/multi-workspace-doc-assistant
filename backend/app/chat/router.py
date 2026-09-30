import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import CurrentUser, SessionDep
from app.chat.service import TurnEvent, run_turn, start_turn
from app.db import get_sessionmaker
from app.llm.chat import get_chat_model
from app.llm.embeddings import Embedder, get_embedder
from app.llm.http import ProviderError
from app.llm.types import ChatModel
from app.models import Message, RetrievalLog, Task, ToolCall
from app.tools.builtin import default_registry
from app.workspaces.deps import ActiveWorkspace

router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["chat"])

# A turn still `pending` after this long was interrupted (e.g. a host restart).
STALE_PENDING = timedelta(minutes=3)


def _chat_model() -> ChatModel:
    try:
        return get_chat_model()
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No AI model is configured."
        ) from None


ChatDep = Annotated[ChatModel, Depends(_chat_model)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
MakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    status: str
    citations: list | None
    model: str | None
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reply_to_id: str | None
    created_at: datetime


def _message_out(m: Message) -> MessageOut:
    status_ = m.status
    if status_ == "pending" and datetime.now(UTC) - m.created_at > STALE_PENDING:
        status_ = "failed"  # interrupted turn: surface it as retryable
    return MessageOut(
        id=str(m.id),
        role=m.role,
        content=m.content,
        status=status_,
        citations=m.citations,
        model=m.model,
        latency_ms=m.latency_ms,
        prompt_tokens=m.prompt_tokens,
        completion_tokens=m.completion_tokens,
        reply_to_id=str(m.reply_to_id) if m.reply_to_id else None,
        created_at=m.created_at,
    )


def _sse(event: TurnEvent | dict) -> str:
    data = event if isinstance(event, dict) else {"type": event.type, **event.data}
    return f"data: {json.dumps(data, default=str)}\n\n"


async def _turn_events(
    maker: async_sessionmaker[AsyncSession],
    chat: ChatModel,
    embedder: Embedder,
    workspace_id: uuid.UUID,
    workspace_name: str,
    user_id: uuid.UUID,
    user_msg_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> AsyncIterator[TurnEvent]:
    # The stream owns its session: request-scoped dependencies may be torn down
    # before a streaming response finishes.
    async with maker() as session:
        user_msg = await session.get(Message, user_msg_id)
        assistant = await session.get(Message, assistant_id)
        async for event in run_turn(
            session=session,
            chat=chat,
            embedder=embedder,
            registry=default_registry(),
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            user_id=user_id,
            user_msg=user_msg,
            assistant=assistant,
        ):
            yield event


async def _begin(session: AsyncSession, workspace, user, text: str):
    user_msg, assistant = await start_turn(session, workspace.id, user.id, text.strip())
    return user_msg.id, assistant.id


@router.post("/chat")
async def chat(
    body: ChatIn,
    workspace: ActiveWorkspace,
    user: CurrentUser,
    session: SessionDep,
    maker: MakerDep,
    chat_model: ChatDep,
    embedder: EmbedderDep,
) -> dict:
    """Non-streaming turn: returns the final answer plus every tool call made."""
    user_msg_id, assistant_id = await _begin(session, workspace, user, body.message)
    events = [
        e
        async for e in _turn_events(
            maker,
            chat_model,
            embedder,
            workspace.id,
            workspace.name,
            user.id,
            user_msg_id,
            assistant_id,
        )
    ]
    async with maker() as s:
        final = await s.get(Message, assistant_id)
        return {
            "user_message_id": str(user_msg_id),
            "message": _message_out(final),
            "tool_calls": [e.data for e in events if e.type == "tool_call"],
        }


@router.post("/chat/stream")
async def chat_stream(
    body: ChatIn,
    workspace: ActiveWorkspace,
    user: CurrentUser,
    session: SessionDep,
    maker: MakerDep,
    chat_model: ChatDep,
    embedder: EmbedderDep,
) -> StreamingResponse:
    """Server-Sent Events: retrieval -> tool_call* -> answer | error."""
    user_msg_id, assistant_id = await _begin(session, workspace, user, body.message)
    ws_id, ws_name, user_id = workspace.id, workspace.name, user.id

    async def stream() -> AsyncIterator[str]:
        yield _sse(
            {"type": "start", "user_message_id": str(user_msg_id), "message_id": str(assistant_id)}
        )
        async for event in _turn_events(
            maker, chat_model, embedder, ws_id, ws_name, user_id, user_msg_id, assistant_id
        ):
            yield _sse(event)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/messages/{message_id}/retry")
async def retry(
    message_id: uuid.UUID,
    workspace: ActiveWorkspace,
    user: CurrentUser,
    session: SessionDep,
    maker: MakerDep,
    chat_model: ChatDep,
    embedder: EmbedderDep,
) -> dict:
    assistant = await session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.workspace_id == workspace.id,
            Message.role == "assistant",
        )
    )
    if assistant is None or assistant.reply_to_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    if _message_out(assistant).status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed messages can be retried")
    assistant.status, assistant.content, assistant.error = "pending", "", None
    await session.commit()
    events = [
        e
        async for e in _turn_events(
            maker,
            chat_model,
            embedder,
            workspace.id,
            workspace.name,
            user.id,
            assistant.reply_to_id,
            assistant.id,
        )
    ]
    async with maker() as s:
        return {
            "message": _message_out(await s.get(Message, message_id)),
            "tool_calls": [e.data for e in events if e.type == "tool_call"],
        }


@router.get("/messages")
async def list_messages(workspace: ActiveWorkspace, session: SessionDep) -> list[MessageOut]:
    rows = await session.scalars(
        select(Message)
        .where(Message.workspace_id == workspace.id)
        .order_by(Message.created_at, Message.role.desc())
        .limit(500)
    )
    return [_message_out(m) for m in rows]


@router.get("/messages/{message_id}/retrieval")
async def message_retrieval(
    message_id: uuid.UUID, workspace: ActiveWorkspace, session: SessionDep
) -> dict:
    """Retrieval-debug: which workspace and which chunks an answer drew from."""
    log = await session.scalar(
        select(RetrievalLog).where(
            RetrievalLog.message_id == message_id, RetrievalLog.workspace_id == workspace.id
        )
    )
    if log is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No retrieval recorded for this message")
    return {
        "workspace_id": str(log.workspace_id),
        "workspace_name": workspace.name,
        "query": log.query,
        "mode": log.mode,
        "hit": log.hit,
        "top_similarity": log.top_score,
        "latency_ms": log.latency_ms,
        "chunks": log.results,
    }


@router.get("/tool-calls")
async def list_tool_calls(workspace: ActiveWorkspace, session: SessionDep) -> list[dict]:
    rows = await session.scalars(
        select(ToolCall)
        .where(ToolCall.workspace_id == workspace.id)
        .order_by(ToolCall.created_at.desc())
        .limit(200)
    )
    return [
        {
            "id": str(t.id),
            "message_id": str(t.message_id) if t.message_id else None,
            "tool_name": t.tool_name,
            "arguments": t.arguments,
            "status": t.status,
            "result": t.result,
            "error": t.error,
            "latency_ms": t.latency_ms,
            "created_at": t.created_at,
        }
        for t in rows
    ]


@router.get("/tasks")
async def list_tasks(workspace: ActiveWorkspace, session: SessionDep) -> list[dict]:
    rows = await session.scalars(
        select(Task).where(Task.workspace_id == workspace.id).order_by(Task.created_at.desc())
    )
    return [
        {
            "id": str(t.id),
            "title": t.title,
            "description": t.description,
            "due_date": t.due_date,
            "priority": t.priority,
            "status": t.status,
            "created_by_tool_call_id": str(t.created_by_tool_call_id)
            if t.created_by_tool_call_id
            else None,
            "created_at": t.created_at,
        }
        for t in rows
    ]


@router.get("/stats")
async def workspace_stats(workspace: ActiveWorkspace, session: SessionDep) -> dict:
    """Observability for the active workspace: volume, latency, tokens, hits, tools."""
    ws = workspace.id
    msg = (
        await session.execute(
            select(
                func.count().filter(Message.status == "done"),
                func.count().filter(Message.status == "failed"),
                func.avg(Message.latency_ms).filter(Message.status == "done"),
                func.percentile_cont(0.95)
                .within_group(Message.latency_ms)
                .filter(Message.status == "done"),
                func.coalesce(func.sum(Message.prompt_tokens), 0),
                func.coalesce(func.sum(Message.completion_tokens), 0),
            ).where(Message.workspace_id == ws, Message.role == "assistant")
        )
    ).one()
    hits = (
        await session.execute(
            select(func.count(), func.count().filter(RetrievalLog.hit)).where(
                RetrievalLog.workspace_id == ws
            )
        )
    ).one()
    tools: dict[str, dict[str, int]] = {}
    for name, status_, n in await session.execute(
        select(ToolCall.tool_name, ToolCall.status, func.count())
        .where(ToolCall.workspace_id == ws)
        .group_by(ToolCall.tool_name, ToolCall.status)
    ):
        tools.setdefault(name, {})[status_] = n
    models = dict(
        (
            await session.execute(
                select(Message.model, func.count())
                .where(Message.workspace_id == ws, Message.model.is_not(None))
                .group_by(Message.model)
            )
        ).all()
    )
    return {
        "messages": msg[0] + msg[1],
        "answered": msg[0],
        "failed": msg[1],
        "avg_latency_ms": round(msg[2]) if msg[2] is not None else None,
        "p95_latency_ms": round(msg[3]) if msg[3] is not None else None,
        "prompt_tokens": int(msg[4]),
        "completion_tokens": int(msg[5]),
        "retrieval_hit_rate": (hits[1] / hits[0]) if hits[0] else None,
        "tool_calls": tools,
        "models": models,
    }
