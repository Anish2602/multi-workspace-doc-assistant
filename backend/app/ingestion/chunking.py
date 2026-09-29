"""Paragraph-aware chunking.

Chunks are built from whole paragraphs up to ~TARGET_CHARS and never span a
page or section change, so each chunk has one unambiguous citation location.
Consecutive chunks share a small overlap (the tail of the previous chunk) so a
fact split across a boundary is still retrievable.

Sizes are in characters (~4 chars per token): ~1,000 chars ≈ 250 tokens. Small
chunks make citations precise and keep retrieved context tight; for the short
policy/spec documents this app targets, that beats larger chunks.
"""

import re
from dataclasses import dataclass

from app.ingestion.parsing import Block

TARGET_CHARS = 1000
MAX_CHARS = 1400
OVERLAP_CHARS = 150

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class TextChunk:
    index: int
    content: str
    page: int | None
    section: str | None

    def embedding_text(self) -> str:
        # Prefix the section heading so a chunk like "Employees get 24 days" is
        # still found by "leave policy" even when the heading isn't in the body.
        return f"{self.section}\n\n{self.content}" if self.section else self.content


def chunk_blocks(blocks: list[Block]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current: list[str] = []
    location: tuple[int | None, str | None] | None = None

    def emit() -> None:
        if current:
            page, section = location or (None, None)
            content = "\n\n".join(current).strip()
            chunks.append(TextChunk(len(chunks), content, page, section))

    for block in blocks:
        block_location = (block.page, block.section)
        for piece in _split_long(block.text):
            size = sum(len(p) for p in current) + len(piece)
            if current and (block_location != location or size > TARGET_CHARS):
                same_location = block_location == location
                emit()
                # Overlap only within the same page/section; never leak across.
                current = [_tail(current[-1])] if same_location else []
            location = block_location
            current.append(piece)
    emit()
    return chunks


def _split_long(text: str) -> list[str]:
    """Split an over-long paragraph on sentence boundaries (hard-cut as a last resort)."""
    if len(text) <= MAX_CHARS:
        return [text]
    pieces: list[str] = []
    buf = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > MAX_CHARS:  # a single enormous "sentence"
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(sentence[:MAX_CHARS])
            sentence = sentence[MAX_CHARS:]
        if buf and len(buf) + 1 + len(sentence) > TARGET_CHARS:
            pieces.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        pieces.append(buf)
    return pieces


def _tail(text: str) -> str:
    """Last ~OVERLAP_CHARS of `text`, starting at a word boundary."""
    if len(text) <= OVERLAP_CHARS:
        return text
    tail = text[-OVERLAP_CHARS:]
    space = tail.find(" ")
    return tail[space + 1 :] if space != -1 else tail
