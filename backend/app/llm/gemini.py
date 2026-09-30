import json
from collections.abc import Callable

import httpx

from app.config import get_settings
from app.llm.http import RETRYABLE_STATUS, ProviderError, _describe, get_http_client, post_json
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

    def _body(self, system: str, messages: list[ChatMessage], tools: list[ToolSpec]) -> dict:
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
        return body

    def _result(self, parts: list[dict], usage: dict) -> ChatResult:
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
        return ChatResult(
            text=text,
            tool_calls=calls,
            model=self.name,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            provider_raw={"provider": "gemini", "parts": parts},
        )

    async def complete(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec]
    ) -> ChatResult:
        data = await post_json(
            "gemini",
            f"{_BASE}/{self._model}:generateContent",
            headers=self._headers,
            json=self._body(system, messages, tools),
            request_timeout=self._timeout,
        )
        candidates = data.get("candidates") or []
        if not candidates or "content" not in candidates[0]:
            reason = (candidates[0].get("finishReason") if candidates else None) or "no candidates"
            raise ProviderError("gemini", f"empty response ({reason})", retryable=True)
        return self._result(
            candidates[0]["content"].get("parts", []), data.get("usageMetadata", {})
        )

    async def stream(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        on_token: Callable[[str], None],
    ) -> ChatResult:
        """Token streaming via SSE. Text deltas go to `on_token` as they arrive.

        Every part carrying a thoughtSignature is kept: when streaming, Gemini sends
        the signature on a trailing *empty* text part, and dropping it breaks the
        next tool-calling round with a 400.
        """
        parts: list[dict] = []
        usage: dict = {}
        try:
            async with get_http_client().stream(
                "POST",
                f"{_BASE}/{self._model}:streamGenerateContent",
                params={"alt": "sse"},
                headers=self._headers,
                json=self._body(system, messages, tools),
                timeout=self._timeout,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise ProviderError(
                        "gemini",
                        _describe(response),
                        retryable=response.status_code in RETRYABLE_STATUS,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = json.loads(line[6:])
                    usage = chunk.get("usageMetadata", usage)
                    candidates = chunk.get("candidates") or []
                    if not candidates:
                        continue
                    for part in candidates[0].get("content", {}).get("parts", []):
                        text = part.get("text", "")
                        if text and not part.get("thought"):
                            on_token(text)
                        if text or "thoughtSignature" in part or "functionCall" in part:
                            parts.append(part)
        except httpx.TimeoutException:
            raise ProviderError("gemini", "stream timed out", retryable=True) from None
        except httpx.HTTPError as exc:
            raise ProviderError("gemini", f"stream error ({type(exc).__name__})", True) from None
        if not parts:
            raise ProviderError("gemini", "empty streamed response", retryable=True)
        return self._result(parts, usage)
