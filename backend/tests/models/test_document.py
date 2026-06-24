from datetime import UTC, datetime

from app.models.document import (
    Chunk,
    Document,
    EmbeddedChunk,
    compute_content_hash,
    compute_document_id,
)


def test_document_id_is_stable_and_source_scoped() -> None:
    assert compute_document_id("slack", "123") == compute_document_id("slack", "123")
    assert compute_document_id("slack", "123") != compute_document_id("github", "123")


def test_content_hash_changes_with_content() -> None:
    assert compute_content_hash("a") != compute_content_hash("b")


def test_document_has_sensible_defaults() -> None:
    now = datetime.now(UTC)
    doc = Document(
        id="x",
        source="slack",
        doc_type="thread",
        title="t",
        content="hello",
        author="me",
        created_at=now,
        updated_at=now,
        url="https://example.com",
        content_hash=compute_content_hash("hello"),
        trace_id="tr-1",
    )
    assert doc.participants == []
    assert doc.metadata == {}


def test_embedded_chunk_wraps_chunk() -> None:
    chunk = Chunk(
        id="d1_0",
        doc_id="d1",
        source="slack",
        doc_type="thread",
        content="hi",
        position=0,
        total_chunks=1,
        trace_id="tr-1",
    )
    embedded = EmbeddedChunk(
        chunk=chunk, embedding=[0.1, 0.2], model="voyage-3.5", embedded_at=datetime.now(UTC)
    )
    assert embedded.chunk.id == "d1_0"
    assert embedded.model == "voyage-3.5"
