"""Schema integration tests — require a real Postgres (skip if Docker is unavailable).

Covers: the migration applies, a chunk round-trips through pgvector with a same-dim vector
and HNSW search returns it, and the audit_log RLS policies make the table append-only for a
non-superuser role (superusers bypass RLS, so we test as a dedicated NOSUPERUSER role).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_VEC = "[" + ",".join(["0.01"] * 1024) + "]"


async def test_chunk_round_trip_and_vector_search(migrated_pg_url: str) -> None:
    engine = create_async_engine(migrated_pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO workspaces (id, name) VALUES ('ws1', 'W')"))
            await conn.execute(
                text(
                    "INSERT INTO documents "
                    "(id, workspace_id, source, source_id, doc_type, content_hash) "
                    "VALUES ('d1', 'ws1', 'slack', 's1', 'thread', 'h1')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO chunks "
                    "(id, doc_id, workspace_id, source, doc_type, content, position, "
                    " total_chunks, embedding, embedding_model) "
                    "VALUES ('c1', 'd1', 'ws1', 'slack', 'thread', 'hello', 0, 1, "
                    f"'{_VEC}', 'voyage-3.5')"
                )
            )

        async with engine.connect() as conn:
            content = (
                await conn.execute(text("SELECT content FROM chunks WHERE id='c1'"))
            ).scalar()
            assert content == "hello"

            nearest = (
                await conn.execute(
                    text(f"SELECT id FROM chunks ORDER BY embedding <=> '{_VEC}' LIMIT 1")
                )
            ).scalar()
            assert nearest == "c1"
    finally:
        await engine.dispose()


async def test_audit_log_is_append_only_under_rls(migrated_pg_url: str) -> None:
    engine = create_async_engine(migrated_pg_url)
    try:
        async with engine.begin() as conn:
            # Superusers bypass RLS, so exercise the policies as a non-superuser role.
            await conn.execute(text("DROP ROLE IF EXISTS pragma_app"))
            await conn.execute(text("CREATE ROLE pragma_app NOLOGIN NOSUPERUSER"))
            await conn.execute(
                text("GRANT SELECT, INSERT, UPDATE, DELETE ON audit_log TO pragma_app")
            )
            await conn.execute(text("SET ROLE pragma_app"))

            await conn.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, request_id, workspace_id, agent_id, tool_name, params, context, "
                    " outcome, executed) "
                    "VALUES (gen_random_uuid(), 'r1', 'ws1', 'a1', 'issue_refund', "
                    "'{}'::jsonb, '{}'::jsonb, 'approved', true)"
                )
            )

            upd = await conn.execute(
                text("UPDATE audit_log SET outcome='rejected' WHERE request_id='r1'")
            )
            assert upd.rowcount == 0  # RLS deny_update -> zero rows affected

            dele = await conn.execute(text("DELETE FROM audit_log WHERE request_id='r1'"))
            assert dele.rowcount == 0  # RLS deny_delete -> zero rows affected

            await conn.execute(text("RESET ROLE"))
            outcome = (
                await conn.execute(text("SELECT outcome FROM audit_log WHERE request_id='r1'"))
            ).scalar()
            assert outcome == "approved"  # unchanged + still present
    finally:
        await engine.dispose()
