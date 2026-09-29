"""Deterministic stand-ins for external providers."""

import hashlib
import math
import re

from app.llm.http import ProviderError
from app.models import EMBEDDING_DIM

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "is", "of", "to", "and", "in", "on", "what", "who", "for", "are"}


class FakeEmbedder:
    """Hashed bag-of-words: texts sharing words get similar vectors. No network."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIM
        for word in _WORD.findall(text.lower()):
            if word not in _STOP:
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v] if any(v) else [1.0 / math.sqrt(EMBEDDING_DIM)] * EMBEDDING_DIM

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise ProviderError("fake", "simulated outage", retryable=True)
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise ProviderError("fake", "simulated outage", retryable=True)
        return self._vec(text)


# --- Chat -------------------------------------------------------------------------------

from collections.abc import Callable  # noqa: E402

from app.llm.chat import AllProvidersFailed  # noqa: E402
from app.llm.types import ChatMessage, ChatResult, ToolCallRequest, ToolSpec  # noqa: E402

Step = ChatResult | Callable[[list[ChatMessage]], ChatResult] | Exception


def reply(text: str = "", *calls: tuple[str, dict | None]) -> ChatResult:
    """Scripted model output. `calls` are (tool_name, arguments-or-None-for-bad-JSON)."""
    return ChatResult(
        text=text,
        tool_calls=[
            ToolCallRequest(
                id=f"c{i}", name=n, arguments=a, raw_arguments="{not json" if a is None else ""
            )
            for i, (n, a) in enumerate(calls)
        ],
        model="fake/scripted",
        prompt_tokens=10,
        completion_tokens=5,
    )


class FakeChat:
    """Plays back a script of model turns and records what it was sent."""

    name = "fake"

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.calls: list[dict] = []

    def script(self, *steps: Step) -> None:
        self.steps = list(steps)

    async def complete(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec]
    ) -> ChatResult:
        self.calls.append(
            {"system": system, "messages": list(messages), "tools": [t.name for t in tools]}
        )
        if not self.steps:
            return reply("(script exhausted)")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step

    def last_user_turn(self) -> str:
        return next(m.content for m in reversed(self.calls[-1]["messages"]) if m.role == "user")


def outage() -> AllProvidersFailed:
    return AllProvidersFailed(["gemini: HTTP 503", "groq: timed out after 30s"])
