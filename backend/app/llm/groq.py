import json

from app.config import get_settings
from app.llm.http import ProviderError, post_json
from app.llm.types import ChatMessage, ChatResult, ToolCallRequest, ToolSpec

_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqChat:
    """OpenAI-compatible chat completions on Groq's free tier (fallback provider)."""

    def __init__(self, model: str) -> None:
        settings = get_settings()
        if settings.groq_api_key is None:
            raise ProviderError("groq", "GROQ_API_KEY is not configured")
        self.name = f"groq/{model}"
        self._model = model
        self._headers = {"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"}
        self._timeout = settings.llm_timeout_seconds

    @staticmethod
    def _messages(system: str, messages: list[ChatMessage]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                msg: dict = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.raw_arguments or "{}"},
                        }
                        for c in m.tool_calls
                    ]
                out.append(msg)
            else:
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        return out

    async def complete(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec]
    ) -> ChatResult:
        body: dict = {
            "model": self._model,
            "messages": self._messages(system, messages),
            "temperature": 0.2,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        data = await post_json(
            "groq", _URL, headers=self._headers, json=body, request_timeout=self._timeout
        )
        message = data["choices"][0]["message"]
        calls = []
        for c in message.get("tool_calls") or []:
            raw = c["function"].get("arguments") or ""
            try:
                args = json.loads(raw) if raw else {}
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                args = None  # malformed JSON from the model -> rejected by the registry
            calls.append(
                ToolCallRequest(
                    id=c["id"], name=c["function"]["name"], arguments=args, raw_arguments=raw
                )
            )
        usage = data.get("usage", {})
        return ChatResult(
            text=message.get("content") or "",
            tool_calls=calls,
            model=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
