"""Chat model with provider fallback: Gemini (pinned models, in order), then Groq."""

import logging
from collections.abc import Callable

from app.config import get_settings
from app.llm.gemini import GeminiChat
from app.llm.groq import GroqChat
from app.llm.http import ProviderError
from app.llm.types import ChatMessage, ChatModel, ChatResult, ToolSpec

log = logging.getLogger(__name__)


class AllProvidersFailed(RuntimeError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class FallbackChat:
    """Tries each configured model in order; the first success wins.

    Switching mid-conversation is safe in one direction only: Gemini -> Groq works
    (OpenAI-format history doesn't need Gemini's thought signatures). Groq is last
    in the chain, so we never replay Groq-made tool calls into Gemini.
    """

    name = "fallback"

    def __init__(self, models: list[ChatModel]) -> None:
        if not models:
            raise ProviderError("llm", "no chat model is configured")
        self.models = models

    async def complete(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec]
    ) -> ChatResult:
        errors: list[str] = []
        for model in self.models:
            try:
                return await model.complete(system, messages, tools)
            except ProviderError as exc:
                log.warning("chat model %s failed: %s", model.name, exc)
                errors.append(str(exc))
        raise AllProvidersFailed(errors)

    async def stream(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        on_token: Callable[[str], None],
        on_reset: Callable[[], None],
    ) -> ChatResult:
        """Stream from the first model that supports it; fall back like `complete`.

        If a model fails after emitting tokens, `on_reset` tells the client to
        discard the partial text before the next model starts.
        """
        errors: list[str] = []
        for model in self.models:
            emitted = False

            def forward(text: str) -> None:
                nonlocal emitted
                emitted = True
                on_token(text)

            try:
                if hasattr(model, "stream"):
                    return await model.stream(system, messages, tools, forward)
                return await model.complete(system, messages, tools)
            except ProviderError as exc:
                log.warning("chat model %s failed: %s", model.name, exc)
                errors.append(str(exc))
                if emitted:
                    on_reset()
        raise AllProvidersFailed(errors)


def build_default_chat() -> FallbackChat:
    settings = get_settings()
    models: list[ChatModel] = []
    if settings.gemini_api_key:
        models += [GeminiChat(m) for m in settings.gemini_chat_models]
    if settings.groq_api_key:
        models.append(GroqChat(settings.groq_chat_model))
    return FallbackChat(models)


_chat: FallbackChat | None = None


def get_chat_model() -> ChatModel:
    """FastAPI dependency; tests override it with a scripted fake."""
    global _chat
    if _chat is None:
        _chat = build_default_chat()
    return _chat
