import pytest

from app.ingestion.chunking import MAX_CHARS, TARGET_CHARS, chunk_blocks
from app.ingestion.parsing import Block, UnsupportedDocument, parse_document
from tests.docgen import make_docx, make_pdf


def test_pdf_keeps_page_numbers():
    data = make_pdf(["Intro page text.", "The office cat is named Pixel."])
    blocks = parse_document("handbook.pdf", data)
    pixel = next(b for b in blocks if "Pixel" in b.text)
    assert pixel.page == 2


def test_docx_tracks_headings():
    data = make_docx(
        {"Leave Policy": ["Employees get 24 days of leave."], "Travel": ["Book via portal."]}
    )
    blocks = parse_document("policy.docx", data)
    assert Block("Employees get 24 days of leave.", section="Leave Policy") in blocks
    assert Block("Book via portal.", section="Travel") in blocks


def test_markdown_tracks_headings():
    md = b"# Falcon\n\nIntro.\n\n## Deadlines\n\nLaunch is on 14 March.\n"
    blocks = parse_document("spec.md", md)
    assert Block("Launch is on 14 March.", section="Deadlines") in blocks


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("virus.exe", b"MZ..."),
        ("fake.pdf", b"not a pdf at all"),
        ("fake.docx", b"not a zip"),
        ("empty.txt", b"   \n\n  "),
        ("binary.txt", b"\x00\x01\x02binary"),
    ],
)
def test_rejects_bad_uploads(name, data):
    with pytest.raises(UnsupportedDocument):
        parse_document(name, data)


def test_chunks_never_span_pages_or_sections():
    blocks = [
        Block("a" * 300, page=1),
        Block("b" * 300, page=2),
        Block("c" * 300, page=2, section="X"),
    ]
    chunks = chunk_blocks(blocks)
    assert [(c.page, c.section) for c in chunks] == [(1, None), (2, None), (2, "X")]
    assert "a" not in chunks[1].content


def test_long_text_is_split_with_overlap_and_size_limits():
    sentences = [f"Sentence number {i} talks about topic {i}." for i in range(200)]
    chunks = chunk_blocks([Block(" ".join(sentences), page=1)])
    assert len(chunks) > 3
    assert all(len(c.content) <= TARGET_CHARS + MAX_CHARS for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # Consecutive chunks overlap so boundary facts stay retrievable.
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.content.split("\n\n")[0] in prev.content


def test_embedding_text_includes_section():
    [chunk] = chunk_blocks([Block("Employees get 24 days.", section="Leave Policy")])
    assert chunk.embedding_text().startswith("Leave Policy")
    assert chunk.content == "Employees get 24 days."
