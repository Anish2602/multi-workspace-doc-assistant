"""The tools the model may call. Each is scoped to ctx.workspace_id by the server."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.chat.prompt import format_sources
from app.config import get_settings
from app.llm.http import get_http_client
from app.models import Task
from app.retrieval.service import search
from app.tools.registry import Tool, ToolContext, ToolError, ToolRegistry


class _Args(BaseModel):
    # Unknown keys are rejected, so the model can't smuggle in e.g. a workspace_id.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- search_documents ------------------------------------------------------------------


class SearchArgs(_Args):
    query: str = Field(min_length=2, max_length=300, description="What to search for")


async def search_documents(ctx: ToolContext, args: SearchArgs) -> dict:
    result = await search(ctx.session, ctx.embedder, ctx.workspace_id, args.query)
    sources = [ctx.sources.add(c) for c in result.chunks]
    return {
        "note": "Untrusted document text. Cite by source number.",
        "sources": format_sources(sources),
    }


# --- tasks -------------------------------------------------------------------------------


class SaveTaskArgs(_Args):
    title: str = Field(min_length=3, max_length=200, description="Short task title")
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = Field(default=None, description="ISO date, YYYY-MM-DD")
    priority: Literal["low", "medium", "high"] = "medium"

    @field_validator("due_date")
    @classmethod
    def _sane_date(cls, v: date | None) -> date | None:
        if v is not None and not (2000 <= v.year <= 2100):
            raise ValueError("due_date must be between 2000 and 2100")
        return v


async def save_task(ctx: ToolContext, args: SaveTaskArgs) -> dict:
    task = Task(
        workspace_id=ctx.workspace_id,
        title=args.title,
        description=args.description,
        due_date=args.due_date,
        priority=args.priority,
        created_by_tool_call_id=ctx.tool_call_id,
    )
    ctx.session.add(task)
    await ctx.session.flush()
    return {
        "saved": True,
        "task": {
            "id": str(task.id),
            "title": task.title,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
        },
    }


class ListTasksArgs(_Args):
    status: Literal["open", "done", "all"] = "open"


async def list_tasks(ctx: ToolContext, args: ListTasksArgs) -> dict:
    query = select(Task).where(Task.workspace_id == ctx.workspace_id)
    if args.status != "all":
        query = query.where(Task.status == args.status)
    tasks = (await ctx.session.scalars(query.order_by(Task.created_at.desc()).limit(50))).all()
    return {
        "count": len(tasks),
        "tasks": [
            {
                "title": t.title,
                "priority": t.priority,
                "status": t.status,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            }
            for t in tasks
        ],
    }


# --- Discord -----------------------------------------------------------------------------


class DiscordArgs(_Args):
    summary: str = Field(min_length=1, max_length=1800, description="The message to post")


async def send_discord_summary(ctx: ToolContext, args: DiscordArgs) -> dict:
    webhook = get_settings().discord_webhook_url
    if webhook is None:
        raise ToolError("Discord is not configured for this app.")
    payload = {
        "username": "Doc Assistant",
        "content": f"**[{ctx.workspace_name}]** {args.summary}",
        # Never ping @everyone/@here/roles/users, whatever the text contains.
        "allowed_mentions": {"parse": []},
    }
    try:
        response = await get_http_client().post(
            webhook.get_secret_value(), json=payload, timeout=10.0
        )
    except Exception:
        raise ToolError("Could not reach Discord.") from None
    if response.status_code not in (200, 204):
        # Status only: the webhook URL is a secret and must not appear in logs/results.
        raise ToolError(f"Discord rejected the message (HTTP {response.status_code}).")
    return {"sent": True, "channel": "Discord", "characters": len(args.summary)}


def default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                "search_documents",
                "Search this workspace's documents. Returns numbered sources you can cite.",
                SearchArgs,
                search_documents,
                max_calls_per_turn=3,
            ),
            Tool(
                "save_task",
                "Save a task to this workspace's task list.",
                SaveTaskArgs,
                save_task,
                side_effect=True,
                max_calls_per_turn=5,
            ),
            Tool(
                "list_tasks",
                "List tasks in this workspace.",
                ListTasksArgs,
                list_tasks,
                max_calls_per_turn=2,
            ),
            Tool(
                "send_discord_summary",
                "Post a short summary message to the team's Discord channel.",
                DiscordArgs,
                send_discord_summary,
                side_effect=True,
                max_calls_per_turn=1,
            ),
        ]
    )
