"""One chat turn: persist -> retrieve (workspace-scoped) -> tool loop -> validate -> persist.

Ordering guarantees:
- The user's message and a `pending` assistant row are committed *before* any
  provider call, so a slow/failed LLM never loses the question.
- On failure the assistant row becomes `failed` with a safe message; the turn can
  be retried and reuses the same rows.
"""

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.prompt import SYSTEM_PROMPT, build_user_turn
from app.llm.chat import AllProvidersFailed
from app.llm.embeddings import Embedder
from app.llm.http import ProviderError
from app.llm.types import ChatMessage, ChatModel
from app.models import Message, RetrievalLog
from app.retrieval.service import relevant_chunks, search
from app.tools.registry import SourceRegistry, ToolContext, ToolExecutor, ToolRegistry

MAX_TOOL_ROUNDS = 5
HISTORY_MESSAGES = 6
UNAVAILABLE = (
    "The AI service is temporarily unavailable. Your question was saved — press Retry in a moment."
)

_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass
class TurnEvent:
    type: str  # retrieval | token | reset | tool_call | answer | error
    data: dict[str, Any]


def clean_citations(text: str, sources: SourceRegistry) -> tuple[str, list[dict]]:
    """Keep only citations that point at sources we actually supplied."""
    used: dict[int, dict] = {}

    def replace(match: re.Match) -> str:
        valid = []
        for raw in match.group(1).split(","):
            n = int(raw.strip())
            source = sources.by_number(n)
            if source is not None:
                valid.append(n)
                used.setdefault(
                    n,
                    {
                        "n": n,
                        "chunk_id": source.chunk_id,
                        "document_id": source.document_id,
                        "filename": source.filename,
                        "page": source.page,
                        "section": source.section,
                        "snippet": source.content[:280],
                    },
                )
        return "".join(f"[{n}]" for n in valid)

    cleaned = _CITATION.sub(replace, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned).strip()
    return cleaned, [used[n] for n in sorted(used)]


class _ModelCall:
    """Run one model round, surfacing streamed tokens as events while it runs.

    Models with `stream()` push text deltas into a queue from a background task;
    `events()` drains the queue until the task finishes. Models without it (e.g.
    test fakes) just complete normally.
    """

    _RESET = object()

    def __init__(self, chat: ChatModel, system: str, messages: list, tools: list) -> None:
        self.chat, self.system, self.messages, self.tools = chat, system, messages, tools
        self.result = None
        self.error: Exception | None = None

    async def events(self) -> AsyncIterator[TurnEvent]:
        if not hasattr(self.chat, "stream"):
            try:
                self.result = await self.chat.complete(self.system, self.messages, self.tools)
            except (AllProvidersFailed, ProviderError) as exc:
                self.error = exc
            return

        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(
            self.chat.stream(
                self.system,
                self.messages,
                self.tools,
                on_token=queue.put_nowait,
                on_reset=lambda: queue.put_nowait(self._RESET),
            )
        )
        try:
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    item = getter.result()
                    yield (
                        TurnEvent("reset", {})
                        if item is self._RESET
                        else TurnEvent("token", {"text": item})
                    )
                    continue
                getter.cancel()
                while not queue.empty():  # tokens that raced the task's completion
                    item = queue.get_nowait()
                    yield (
                        TurnEvent("reset", {})
                        if item is self._RESET
                        else TurnEvent("token", {"text": item})
                    )
                break
            try:
                self.result = task.result()
            except (AllProvidersFailed, ProviderError) as exc:
                self.error = exc
        finally:
            if not task.done():  # client disconnected mid-stream
                task.cancel()


async def start_turn(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, question: str
) -> tuple[Message, Message]:
    user_msg = Message(workspace_id=workspace_id, user_id=user_id, role="user", content=question)
    session.add(user_msg)
    await session.flush()
    assistant = Message(
        workspace_id=workspace_id,
        user_id=user_id,
        role="assistant",
        status="pending",
        reply_to_id=user_msg.id,
    )
    session.add(assistant)
    await session.commit()
    return user_msg, assistant


async def _history(
    session: AsyncSession, workspace_id: uuid.UUID, before: Message
) -> list[ChatMessage]:
    rows = (
        await session.scalars(
            select(Message)
            .where(
                Message.workspace_id == workspace_id,
                Message.status == "done",
                Message.created_at < before.created_at,
                Message.id != before.id,
            )
            .order_by(Message.created_at.desc())
            .limit(HISTORY_MESSAGES)
        )
    ).all()
    return [ChatMessage(role=m.role, content=m.content) for m in reversed(rows) if m.content]


async def run_turn(
    *,
    session: AsyncSession,
    chat: ChatModel,
    embedder: Embedder,
    registry: ToolRegistry,
    workspace_id: uuid.UUID,
    workspace_name: str,
    user_id: uuid.UUID,
    user_msg: Message,
    assistant: Message,
) -> AsyncIterator[TurnEvent]:
    started = time.perf_counter()
    assistant_id, question = assistant.id, user_msg.content
    sources = SourceRegistry()
    prompt_tokens = completion_tokens = 0
    model_used: str | None = None

    async def fail(detail: str) -> TurnEvent:
        await session.rollback()
        row = await session.get(Message, assistant_id)
        row.status, row.content, row.error = "failed", UNAVAILABLE, detail[:1000]
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        await session.commit()
        return TurnEvent("error", {"message": UNAVAILABLE, "message_id": str(assistant_id)})

    try:
        # 1. Retrieval, scoped to the active workspace inside the SQL.
        try:
            result = await search(session, embedder, workspace_id, question)
        except ProviderError as exc:
            yield await fail(f"retrieval: {exc}")
            return
        context = [sources.add(c) for c in relevant_chunks(result)]
        session.add(
            RetrievalLog(
                workspace_id=workspace_id,
                message_id=assistant_id,
                query=question,
                mode=result.mode,
                results=[
                    {**c.as_log(), "used": c.chunk_id in {s.chunk_id for s in context}}
                    for c in result.chunks
                ],
                top_score=result.top_similarity,
                hit=bool(context),
                latency_ms=result.latency_ms,
            )
        )
        await session.commit()
        yield TurnEvent(
            "retrieval",
            {
                "hit": bool(context),
                "sources": len(context),
                "top_similarity": result.top_similarity,
            },
        )

        # 2. Tool loop.
        system = SYSTEM_PROMPT.format(
            workspace_name=workspace_name, tool_names=", ".join(registry.names)
        )
        history = await _history(session, workspace_id, user_msg)
        messages = [*history, ChatMessage(role="user", content=build_user_turn(question, context))]
        executor = ToolExecutor(
            registry,
            ToolContext(
                session=session,
                embedder=embedder,
                workspace_id=workspace_id,
                workspace_name=workspace_name,
                user_id=user_id,
                message_id=assistant_id,
                sources=sources,
            ),
        )
        tools = registry.specs()
        final_text = ""
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            # Last round: no tools offered, forcing a final answer.
            offer = tools if round_no < MAX_TOOL_ROUNDS else []
            call = _ModelCall(chat, system, messages, offer)
            async for event in call.events():
                yield event
            if call.error is not None:
                yield await fail(f"llm: {call.error}")
                return
            reply = call.result
            model_used = reply.model
            prompt_tokens += reply.prompt_tokens
            completion_tokens += reply.completion_tokens
            messages.append(reply.as_message())
            if not reply.tool_calls:
                final_text = reply.text
                break
            for call in reply.tool_calls:
                outcome = await executor.run(call)
                yield TurnEvent(
                    "tool_call",
                    {
                        "id": str(outcome.tool_call_id),
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": outcome.status,
                        "result": outcome.payload,
                    },
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(outcome.payload, default=str),
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )

        # 3. Validate citations and persist.
        text, citations = clean_citations(final_text, sources)
        if not text:
            text = "I couldn't produce an answer for that. Please try rephrasing."
        row = await session.get(Message, assistant_id)
        row.status, row.content, row.citations, row.error = "done", text, citations, None
        row.model, row.prompt_tokens, row.completion_tokens = (
            model_used,
            prompt_tokens,
            completion_tokens,
        )
        row.latency_ms = int((time.perf_counter() - started) * 1000)
        await session.commit()
        yield TurnEvent(
            "answer",
            {
                "message_id": str(assistant_id),
                "content": text,
                "citations": citations,
                "model": model_used,
                "latency_ms": row.latency_ms,
            },
        )
    except Exception as exc:  # never leave a turn stuck in `pending`
        yield await fail(f"internal: {type(exc).__name__}")
