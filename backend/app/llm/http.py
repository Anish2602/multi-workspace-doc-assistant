"""Shared HTTP plumbing for provider calls: one client, retries, safe errors."""

import asyncio
import logging
import random

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    return _client


class ProviderError(RuntimeError):
    """A provider call failed. The message is safe to log/show (no keys, no URLs)."""

    def __init__(self, provider: str, message: str, *, retryable: bool = False):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


def _describe(response: httpx.Response) -> str:
    try:
        detail = response.json().get("error", {})
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
    except ValueError:
        message = None
    return f"HTTP {response.status_code}" + (f" — {message[:160]}" if message else "")


async def post_json(
    provider: str,
    url: str,
    *,
    headers: dict[str, str],
    json: dict,
    request_timeout: float,
    attempts: int = 3,
) -> dict:
    """POST with retry + jittered exponential backoff on timeouts / 429 / 5xx."""
    last: ProviderError | None = None
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(min(8.0, 0.75 * 2**attempt) + random.uniform(0, 0.5))
        try:
            response = await get_http_client().post(
                url, headers=headers, json=json, timeout=request_timeout
            )
        except httpx.TimeoutException:
            last = ProviderError(
                provider, f"timed out after {request_timeout:.0f}s", retryable=True
            )
        except httpx.HTTPError as exc:
            last = ProviderError(provider, f"network error ({type(exc).__name__})", retryable=True)
        else:
            if response.status_code == 200:
                return response.json()
            retryable = response.status_code in RETRYABLE_STATUS
            last = ProviderError(provider, _describe(response), retryable=retryable)
            if not retryable:
                break
        log.warning("%s (attempt %d/%d)", last, attempt + 1, attempts)
    assert last is not None
    raise last
