"""Provider-neutral chat types. Adapters translate these to Gemini / OpenAI shapes."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)


@dataclass
class ToolCallRequest:
    """What the model asked for. Nothing here is trusted until the registry validates it."""

    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the model sent unparseable JSON
    raw_arguments: str = ""


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    # role == "tool"
    tool_call_id: str | None = None
    tool_name: str | None = None
    # Gemini 3 requires its original response parts (incl. thoughtSignature) to be
    # replayed verbatim in later turns; other providers ignore this.
    provider_raw: dict[str, Any] | None = None


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCallRequest]
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    provider_raw: dict[str, Any] | None = None

    def as_message(self) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=self.text,
            tool_calls=self.tool_calls,
            provider_raw=self.provider_raw,
        )


class ChatModel(Protocol):
    name: str

    async def complete(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec]
    ) -> ChatResult: ...
