import json

from app.config import get_settings
from app.llm.http import ProviderError, post_json
from app.llm.types import ChatMessage, ChatResult, ToolCallRequest, ToolSpec

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiChat:
    def __init__(self, model: str) -> None:
        settings = get_settings()
        if settings.gemini_api_key is None:
            raise ProviderError("gemini", "GEMINI_API_KEY is not configured")
        self.name = f"gemini/{model}"
        self._model = model
        self._headers = {"x-goog-api-key": settings.gemini_api_key.get_secret_value()}
        self._timeout = settings.llm_timeout_seconds

    @staticmethod
    def _contents(messages: list[ChatMessage]) -> list[dict]:
        contents: list[dict] = []
        for m in messages:
            if m.role == "user":
                contents.append({"role": "user", "parts": [{"text": m.content}]})
            elif m.role == "assistant":
                if m.provider_raw and m.provider_raw.get("provider") == "gemini":
                    parts = m.provider_raw["parts"]  # verbatim, keeps thoughtSignature
                else:
                    parts = ([{"text": m.content}] if m.content else []) + [
                        {"functionCall": {"name": c.name, "args": c.arguments or {}}}
                        for c in m.tool_calls
                    ]
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            else:  # tool result
                response = {
                    "functionResponse": {
                        "name": m.tool_name,
                        "id": m.tool_call_id,
                        "response": json.loads(m.content),
                    }
                }
                # Consecutive tool results belong in one user turn.
                if contents and contents[-1].get("_tool_results"):
                    contents[-1]["parts"].append(response)
                else:
                    contents.append({"role": "user", "parts": [response], "_tool_results": True})
        for c in contents:
            c.pop("_tool_results", None)
        return contents

    async def complete(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec]
    ) -> ChatResult:
        body: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._contents(messages),
            "generationConfig": {"temperature": 0.2},
        }
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parametersJsonSchema": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]
        data = await post_json(
            "gemini",
            f"{_BASE}/{self._model}:generateContent",
            headers=self._headers,
            json=body,
            request_timeout=self._timeout,
        )
        candidates = data.get("candidates") or []
        if not candidates or "content" not in candidates[0]:
            reason = (candidates[0].get("finishReason") if candidates else None) or "no candidates"
            raise ProviderError("gemini", f"empty response ({reason})", retryable=True)
        parts = candidates[0]["content"].get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        calls = [
            ToolCallRequest(
                id=p["functionCall"].get("id") or f"call_{i}",
                name=p["functionCall"].get("name", ""),
                arguments=p["functionCall"].get("args") or {},
                raw_arguments=json.dumps(p["functionCall"].get("args") or {}),
            )
            for i, p in enumerate(parts)
            if "functionCall" in p
        ]
        usage = data.get("usageMetadata", {})
        return ChatResult(
            text=text,
            tool_calls=calls,
            model=self.name,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            provider_raw={"provider": "gemini", "parts": parts},
        )
