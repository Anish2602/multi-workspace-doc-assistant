"""Tool registry: the model proposes, this module disposes.

Every tool call from the model goes through `ToolExecutor.run`, which:
  1. rejects unknown tool names (there is no way to call anything not registered),
  2. rejects unparseable / schema-invalid arguments (Pydantic, extra fields forbidden),
  3. enforces per-turn budgets on side-effecting tools,
  4. runs the tool with the *server-side* workspace context, and
  5. logs every attempt (success or not) to `tool_calls`.
It never raises: failures become a structured error the model can read and react to.
"""

import logging
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.embeddings import Embedder
from app.llm.types import ToolCallRequest, ToolSpec
from app.models import ToolCall

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Server-controlled context. The model can't set or see any of this.

    Holds plain ids, not ORM objects: a rollback after a failed tool expires ORM
    instances, and touching an expired attribute in async SQLAlchemy raises.
    """

    session: AsyncSession
    embedder: Embedder
    workspace_id: uuid.UUID
    workspace_name: str
    user_id: uuid.UUID
    message_id: uuid.UUID | None
    sources: "SourceRegistry"
    tool_call_id: uuid.UUID | None = None


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[dict]]
    side_effect: bool = False
    max_calls_per_turn: int = 5

    def spec(self) -> ToolSpec:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return ToolSpec(self.name, self.description, schema)


@dataclass
class ToolOutcome:
    status: str  # success | unknown_tool | invalid_args | limit_exceeded | error
    payload: dict  # what the model sees
    tool_call_id: uuid.UUID | None = None


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._tools)


def _validation_summary(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()][:5]


@dataclass
class ToolExecutor:
    registry: ToolRegistry
    ctx: ToolContext
    calls_this_turn: Counter = field(default_factory=Counter)

    async def run(self, request: ToolCallRequest) -> ToolOutcome:
        started = time.perf_counter()
        tool = self.registry.get(request.name)
        args_for_log: dict | None = request.arguments
        if request.arguments is None:
            args_for_log = {"_raw": request.raw_arguments[:500]}

        rejection: ToolOutcome | None = None
        args: BaseModel | None = None
        if tool is None:
            rejection = ToolOutcome(
                "unknown_tool",
                {
                    "error": f"Unknown tool '{request.name[:80]}'. Nothing was executed.",
                    "available_tools": self.registry.names,
                },
            )
        elif request.arguments is None:
            rejection = ToolOutcome(
                "invalid_args", {"error": "Arguments were not valid JSON. Nothing was executed."}
            )
        elif self.calls_this_turn[tool.name] >= tool.max_calls_per_turn:
            rejection = ToolOutcome(
                "limit_exceeded",
                {
                    "error": f"'{tool.name}' may be called at most "
                    f"{tool.max_calls_per_turn}x per message."
                },
            )
        else:
            try:
                args = tool.args_model.model_validate(request.arguments)
            except ValidationError as exc:
                rejection = ToolOutcome(
                    "invalid_args",
                    {
                        "error": "Invalid arguments. Nothing was executed.",
                        "details": _validation_summary(exc),
                    },
                )

        # The log row is written *before* anything runs, so side effects can
        # reference it and a crash mid-tool still leaves a trace.
        record = ToolCall(
            workspace_id=self.ctx.workspace_id,
            message_id=self.ctx.message_id,
            tool_name=request.name[:100],
            arguments=args_for_log,
            status=rejection.status if rejection else "running",
            error=rejection.payload.get("error") if rejection else None,
        )
        self.ctx.session.add(record)
        await self.ctx.session.commit()
        record_id = record.id

        if rejection is not None:
            outcome = rejection
        else:
            assert tool is not None and args is not None
            self.calls_this_turn[tool.name] += 1
            outcome = await self._execute(tool, args, record_id)

        # Re-fetch: a rollback inside _execute expires ORM instances.
        record = await self.ctx.session.get(ToolCall, record_id)
        record.status = outcome.status
        record.result = outcome.payload if outcome.status == "success" else None
        record.error = outcome.payload.get("error") if outcome.status != "success" else None
        record.latency_ms = int((time.perf_counter() - started) * 1000)
        # Commits the tool's own writes (e.g. the new task) together with its log entry.
        await self.ctx.session.commit()
        outcome.tool_call_id = record_id
        return outcome

    async def _execute(self, tool: Tool, args: BaseModel, record_id: uuid.UUID) -> ToolOutcome:
        self.ctx.tool_call_id = record_id
        try:
            payload = await tool.handler(self.ctx, args)
            return ToolOutcome("success", payload)
        except ToolError as exc:
            await self.ctx.session.rollback()
            return ToolOutcome("error", {"error": str(exc)})
        except Exception:
            await self.ctx.session.rollback()
            # Full detail server-side; the model only gets a generic message.
            log.exception("tool %s raised", tool.name)
            return ToolOutcome("error", {"error": f"'{tool.name}' failed unexpectedly."})


class ToolError(RuntimeError):
    """An expected tool failure with a message that is safe to show the model."""


# --- Sources: the numbered chunks the model may cite -----------------------------------


@dataclass
class Source:
    number: int
    chunk_id: str
    document_id: str
    filename: str
    page: int | None
    section: str | None
    content: str
    similarity: float | None


class SourceRegistry:
    """Numbers every chunk shown to the model this turn ([1], [2], ...).

    Citations in the final answer are validated against this registry, so the model
    can only cite text it was actually given, from the active workspace.
    """

    def __init__(self) -> None:
        self._by_chunk: dict[str, Source] = {}

    def add(self, chunk) -> Source:
        if chunk.chunk_id not in self._by_chunk:
            self._by_chunk[chunk.chunk_id] = Source(
                number=len(self._by_chunk) + 1,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                filename=chunk.filename,
                page=chunk.page,
                section=chunk.section,
                content=chunk.content,
                similarity=chunk.similarity,
            )
        return self._by_chunk[chunk.chunk_id]

    def by_number(self, n: int) -> Source | None:
        return next((s for s in self._by_chunk.values() if s.number == n), None)

    def all(self) -> list[Source]:
        return list(self._by_chunk.values())
