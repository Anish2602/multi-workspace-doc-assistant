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
