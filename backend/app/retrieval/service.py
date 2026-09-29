"""Workspace-scoped hybrid retrieval over the single shared `chunks` table.

Isolation: `workspace_id = :ws` is a predicate *inside* both the vector query and
the keyword query. Nothing is fetched and then filtered in Python, so another
workspace's chunks are never even read.

Vector leg: cosine distance. For a selective filter (a small workspace) Postgres
uses the btree index on workspace_id and sorts exactly. When the planner instead
picks the HNSW index (large / unselective workspaces), HNSW yields only the
`ef_search` nearest neighbours *globally*, and the workspace filter is applied to
those — so results can come back short or empty. pgvector 0.8's iterative scan
keeps walking the index until enough rows pass the filter. Both plans are proven
in tests/test_retrieval_isolation.py.

Keyword leg: Postgres full-text search (OR of the query's terms), which catches
exact names, codes and numbers that embeddings blur.

Fusion: Reciprocal Rank Fusion (score = Σ 1/(60 + rank)) — rank-based, so the two
legs' incomparable score scales don't need calibrating against each other.
"""

import time
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.embeddings import Embedder

RRF_K = 60
CANDIDATES_PER_LEG = 20
DEFAULT_TOP_K = 6
# Below this cosine similarity the best chunk isn't considered evidence at all,
# and the chat answers "I don't know" without calling the LLM.
MIN_SIMILARITY = 0.55


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    page: int | None
    section: str | None
    content: str
    similarity: float | None  # cosine similarity from the vector leg (None if keyword-only)
    vector_rank: int | None
    keyword_rank: int | None
    score: float  # fused RRF score

    def as_log(self) -> dict:
        data = asdict(self)
        data["content"] = self.content[:300]
        return data


@dataclass
class RetrievalResult:
    workspace_id: uuid.UUID
    query: str
    mode: str
    chunks: list[RetrievedChunk]
    top_similarity: float | None
    latency_ms: int

    @property
    def hit(self) -> bool:
        return self.top_similarity is not None and self.top_similarity >= MIN_SIMILARITY


_VECTOR_SQL = text(
    """
    WITH nearest AS MATERIALIZED (
        SELECT id, embedding <=> CAST(:qvec AS vector) AS distance
        FROM chunks
        WHERE workspace_id = :ws
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :n
    )
    SELECT n.id, 1 - n.distance AS similarity
    FROM nearest n
    ORDER BY n.distance
    """
)

_KEYWORD_SQL = text(
    """
    WITH q AS (
        SELECT to_tsquery('english',
                 replace(plainto_tsquery('english', :query)::text, ' & ', ' | ')) AS tsq
    )
    SELECT c.id
    FROM chunks c, q
    WHERE c.workspace_id = :ws
      AND q.tsq::text <> ''
      AND c.tsv @@ q.tsq
    ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC
    LIMIT :n
    """
)

_FETCH_SQL = text(
    """
    SELECT c.id, c.document_id, d.filename, c.page, c.section, c.content
    FROM chunks c
    JOIN documents d ON d.id = c.document_id AND d.workspace_id = c.workspace_id
    WHERE c.workspace_id = :ws AND c.id = ANY(:ids)
    """
)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


async def search(
    session: AsyncSession,
    embedder: Embedder,
    workspace_id: uuid.UUID,
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    hybrid: bool = True,
) -> RetrievalResult:
    started = time.perf_counter()
    qvec = _vector_literal(await embedder.embed_query(query))

    # Iterative index scan so the workspace filter can't starve the HNSW result set.
    await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))

    vector_rows = (
        await session.execute(
            _VECTOR_SQL, {"qvec": qvec, "ws": workspace_id, "n": CANDIDATES_PER_LEG}
        )
    ).all()
    keyword_ids: list[str] = []
    if hybrid:
        keyword_ids = list(
            (
                await session.execute(
                    _KEYWORD_SQL, {"query": query, "ws": workspace_id, "n": CANDIDATES_PER_LEG}
                )
            ).scalars()
        )

    similarity = {row.id: float(row.similarity) for row in vector_rows}
    vector_rank = {row.id: i for i, row in enumerate(vector_rows, start=1)}
    keyword_rank = {cid: i for i, cid in enumerate(keyword_ids, start=1)}
    fused = {
        cid: sum(1 / (RRF_K + r[cid]) for r in (vector_rank, keyword_rank) if cid in r)
        for cid in vector_rank.keys() | keyword_rank.keys()
    }
    top_ids = sorted(fused, key=fused.get, reverse=True)[:top_k]

    details = {}
    if top_ids:
        rows = await session.execute(_FETCH_SQL, {"ws": workspace_id, "ids": top_ids})
        details = {row.id: row for row in rows}

    chunks = [
        RetrievedChunk(
            chunk_id=cid,
            document_id=str(details[cid].document_id),
            filename=details[cid].filename,
            page=details[cid].page,
            section=details[cid].section,
            content=details[cid].content,
            similarity=similarity.get(cid),
            vector_rank=vector_rank.get(cid),
            keyword_rank=keyword_rank.get(cid),
            score=fused[cid],
        )
        for cid in top_ids
        if cid in details
    ]
    return RetrievalResult(
        workspace_id=workspace_id,
        query=query,
        mode="hybrid" if hybrid else "vector",
        chunks=chunks,
        top_similarity=max(similarity.values(), default=None),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
