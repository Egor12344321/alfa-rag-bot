"""Run the local PostgreSQL/pgvector acceptance checks against an imported run."""
from __future__ import annotations

import asyncio
import json
import os
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from alfa_bot.knowledge.contracts import Profile, SourceKey
from alfa_bot.knowledge.embeddings import E5Embedder, MODEL_NAME
from alfa_bot.knowledge.repository import (
    EmbeddingCompatibilityError,
    IndexedMaterial,
    KnowledgeRepository,
)
from alfa_bot.knowledge.service import KnowledgeService


EXPECTED_TOP1 = {
    "Как собрать и проанализировать целевую аудиторию?": "d1e67c81-f364-4724-bfac-d02293e87cb8",
    "Что такое постоплата и чем она отличается от предоплаты?": "7ed5b9e2-d183-4440-b467-f6ce6fcea1be",
    "Как маркировать товары на Wildberries?": "2cf5e7cc-6abb-47b5-afc9-ab672a641b99",
    "Что помогает развивать личный бренд предпринимателя?": "f70c43ee-bc3d-48d0-bf24-abdeca927b48",
    "Как производителю изучить покупателей и понять их выбор?": "4edc4abf-1f3c-41db-be25-25dfae8486b0",
}
COURSE_KEYS = (
    SourceKey("Courses", "39d56e76-2da6-4ac4-8c57-1e7ba2b856c7"),
    SourceKey("Courses", "3d17b03e-0e69-44cf-aa04-110e92437016"),
    SourceKey("Courses", "d0aa4598-3da2-478b-bbf8-bda81b231b58"),
    SourceKey("Courses", "b9045d6e-07ad-407f-a305-472e82fef797"),
    SourceKey("Courses", "b3f8439b-f5c0-4c97-b283-a62a524c29dc"),
)
RELATED_SOURCE = SourceKey("Articles", "5197669c-e7c3-4465-9a16-e7eeb13ee37c")
RELATED_COURSES = (
    SourceKey("Courses", "b9045d6e-07ad-407f-a305-472e82fef797"),
    SourceKey("Courses", "b3f8439b-f5c0-4c97-b283-a62a524c29dc"),
)


class WrongRevisionEmbedder:
    model_name = MODEL_NAME
    resolved_revision = "0" * 40

    @staticmethod
    def encode_query(_text: str) -> list[float]:
        return [0.0] * 384


async def main() -> None:
    dsn = os.environ["ALFA_DATABASE_URL"]
    embedder = E5Embedder()
    async with AsyncConnectionPool(dsn, min_size=1, max_size=4) as pool:
        repository = KnowledgeRepository(pool)
        knowledge = KnowledgeService(repository, embedder)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT active_run_id FROM kb.index_state WHERE singleton=TRUE"
            )
            active_row = await cursor.fetchone()
            if active_row is None:
                raise AssertionError("No active run")
            initial_active_run_id = active_row[0]
            expected_active_run_id = UUID(
                os.getenv("ALFA_EXPECT_ACTIVE_RUN_ID", str(initial_active_run_id))
            )
            if initial_active_run_id != expected_active_run_id:
                raise AssertionError("Test DB active run differs from ALFA_EXPECT_ACTIVE_RUN_ID")
            run_id = UUID(os.getenv("ALFA_ACCEPTANCE_RUN_ID", str(initial_active_run_id)))
            cursor = await conn.execute(
                """SELECT id, embedding_model, embedding_revision, status, metrics
                   FROM kb.ingestion_runs WHERE id=%s""",
                (run_id,),
            )
            run_row = await cursor.fetchone()
            if run_row is None:
                raise AssertionError(f"Acceptance run {run_id} does not exist")
            metrics = run_row[4]
            cursor = await conn.execute(
                """SELECT m.source_table, m.kind, count(DISTINCT m.id),
                          count(c.id) FILTER (WHERE c.purpose='knowledge'),
                          count(c.id) FILTER (WHERE c.purpose='catalog')
                   FROM kb.materials m LEFT JOIN kb.chunks c
                     ON c.run_id=m.run_id AND c.material_id=m.id
                   WHERE m.run_id=%s GROUP BY m.source_table,m.kind ORDER BY 1,2""",
                (run_id,),
            )
            counts = await cursor.fetchall()
            cursor = await conn.execute(
                """SELECT m.source_table, m.source_id, c.chunk_no, c.source_blocks
                   FROM kb.materials m JOIN kb.chunks c
                     ON c.run_id=m.run_id AND c.material_id=m.id
                   WHERE m.run_id=%s AND m.source_table IN ('Documents','Glossary')
                   ORDER BY m.source_table,m.source_id,c.chunk_no""",
                (run_id,),
            )
            lineage_rows = await cursor.fetchall()
        if run_row[3] != "ready":
            raise AssertionError(f"Active run status is {run_row[3]!r}")
        if run_row[1] != embedder.model_name or run_row[2] != embedder.resolved_revision:
            raise AssertionError("Persisted run model/revision differs from the loaded E5 model")
        if metrics.get("embedding_revision") != embedder.resolved_revision:
            raise AssertionError("Run metrics do not contain the loaded immutable revision")
        expected_material_counts = {
            "Articles": 12,
            "Courses": 5,
            "Documents": 1,
            "Glossary": 1,
        }
        actual_material_counts = {row[0]: row[2] for row in counts}
        if actual_material_counts != expected_material_counts:
            raise AssertionError(
                f"Expected material counts {expected_material_counts}, got {actual_material_counts}"
            )
        if sum(row[2] for row in counts) != 19:
            raise AssertionError(f"Expected 19 materials; got {counts!r}")
        lineage = {}
        for table, source_id, _chunk_no, block_ids in lineage_rows:
            lineage.setdefault((table, source_id), []).extend(block_ids or [])
        expected_lineage = {
            ("Documents", "defc5abc-e692-4ae8-87f6-987d791705b0"): (
                "7b6d8668-bae4-480a-b3e7-fcac3509d153",
            ),
            ("Glossary", "58e6c342-30d9-4300-bf15-183e799402a2"): (
                "bf10017e-0545-48b2-abb2-cfc295be4bfc",
                "10c9898e-c427-48d3-b339-330345c8c1ab",
                "0ec87270-162d-477f-abe1-374c4ee9848b",
                "f6debec0-5775-48e3-93a9-5e934e4af100",
                "4b8030fe-5df3-406e-b372-c8123b0ea390",
                "c5968aaf-8b40-4a84-a187-70d14ed677b4",
            ),
        }
        for key, expected_blocks in expected_lineage.items():
            if tuple(lineage.get(key, ())) != expected_blocks:
                raise AssertionError(
                    f"Source block IDs/order were not retained for {key}: {lineage.get(key)}"
                )

        retrieval = {}
        for query, source_id in EXPECTED_TOP1.items():
            result = await knowledge.retrieve(
                query, limit=3, run_id=run_id, external_context=False
            )
            actual = result.evidence[0].source.source_id if result.evidence else None
            if actual != source_id:
                raise AssertionError(f"Expected {source_id} for {query!r}, got {actual}")
            retrieval[query] = actual

        extra_retrieval = {}
        for query, expected_source in (
            (
                "Нужен образец соглашения о неразглашении для бизнеса",
                "defc5abc-e692-4ae8-87f6-987d791705b0",
            ),
            (
                "Что такое доверенность и зачем она нужна компании?",
                "58e6c342-30d9-4300-bf15-183e799402a2",
            ),
        ):
            result = await knowledge.retrieve(
                query, limit=3, run_id=run_id, external_context=False
            )
            actual = result.evidence[0].source.source_id if result.evidence else None
            if actual != expected_source:
                raise AssertionError(
                    f"Expected added source {expected_source} for {query!r}, got {actual}"
                )
            extra_retrieval[query] = actual

        previous_run_id = os.getenv("ALFA_PREVIOUS_RUN_ID")
        previous_run_retrievable = None
        if previous_run_id:
            previous_query = next(iter(EXPECTED_TOP1))
            previous = await knowledge.retrieve(
                previous_query, limit=1,
                run_id=UUID(previous_run_id), external_context=False,
            )
            if (
                not previous.evidence
                or previous.evidence[0].source.source_id != EXPECTED_TOP1[previous_query]
            ):
                raise AssertionError("Previous compatible ready run lost the known top-1")
            previous_run_retrievable = previous.run_id == UUID(previous_run_id)

        one_free = await knowledge.recommend(
            "Как начать бизнес с нуля и с чего начать",
            Profile(free_only=True), run_id=run_id, limit=2,
        )
        if not one_free or any(card.access_type != "free" for card in one_free):
            raise AssertionError("free_only returned no confirmed-free course or an ineligible card")

        linked = await knowledge.recommend(
            "Как пересчитать стоимость патента в бизнесе?",
            Profile(free_only=True), run_id=run_id, source=RELATED_SOURCE, limit=1,
        )
        if not linked or linked[0].source != RELATED_COURSES[0]:
            raise AssertionError("recommend() did not prioritize the first eligible linked course")
        excluded = await knowledge.recommend(
            "Как пересчитать стоимость патента в бизнесе?",
            Profile(free_only=True), run_id=run_id,
            exclude=(RELATED_COURSES[0],), source=RELATED_SOURCE, limit=2,
        )
        if not excluded or excluded[0].source != RELATED_COURSES[1]:
            raise AssertionError("recommend() ignored exclude")

        query = "Как пересчитать стоимость патента в бизнесе?"
        no_source = await knowledge.recommend(
            query, Profile(free_only=True), run_id=run_id, limit=2,
        )
        no_edge = await knowledge.recommend(
            query, Profile(free_only=True), run_id=run_id,
            source=SourceKey("Articles", "f70c43ee-bc3d-48d0-bf24-abdeca927b48"), limit=2,
        )
        if [card.source for card in no_edge] != [card.source for card in no_source]:
            raise AssertionError("recommend() without a relation changed vector ranking")

        # Disable all linked targets in this test run. The source-aware call
        # must then match the ordinary vector fallback.
        async with pool.connection() as conn:
            await conn.execute(
                """UPDATE kb.materials SET is_active=FALSE, recommend_allowed=FALSE
                   WHERE run_id=%s AND source_table='Courses' AND source_id=ANY(%s)""",
                (run_id, [key.source_id for key in RELATED_COURSES]),
            )
        try:
            unavailable = await knowledge.recommend(
                query, Profile(free_only=True), run_id=run_id,
                source=RELATED_SOURCE, limit=2,
            )
            vector_fallback = await knowledge.recommend(
                query, Profile(free_only=True), run_id=run_id, limit=2,
            )
            if [card.source for card in unavailable] != [
                card.source for card in vector_fallback
            ]:
                raise AssertionError("Unavailable linked cards did not fall back to vector ranking")
            if any(card.source in RELATED_COURSES for card in unavailable):
                raise AssertionError("recommend() returned an ineligible linked card")
        finally:
            async with pool.connection() as conn:
                await conn.execute(
                    """UPDATE kb.materials SET is_active=TRUE, recommend_allowed=TRUE
                       WHERE run_id=%s AND source_table='Courses' AND source_id=ANY(%s)""",
                    (run_id, [key.source_id for key in RELATED_COURSES]),
                )
        none = await knowledge.recommend(
            "Как начать бизнес с нуля и с чего начать",
            Profile(free_only=True), run_id=run_id,
            exclude=COURSE_KEYS, limit=2,
        )
        if none:
            raise AssertionError("recommend() should return empty when every eligible card is excluded")

        existing = await knowledge.get_card(RELATED_COURSES[0], run_id=run_id)
        missing = await knowledge.get_card(SourceKey("Courses", "missing"), run_id=run_id)
        unapproved_article = await knowledge.get_card(
            SourceKey("Articles", "f70c43ee-bc3d-48d0-bf24-abdeca927b48"), run_id=run_id
        )
        if existing is None or missing is not None or unapproved_article is not None:
            raise AssertionError("get_card() access/existence checks failed")
        isolated = await knowledge.retrieve(
            "Хотите начать бизнес, но не знаете, как к нему подступиться",
            limit=6, run_id=run_id, external_context=False,
        )
        if any(e.source.source_table == "Courses" for e in isolated.evidence):
            raise AssertionError("retrieve() leaked catalog-only content")
        external_blocked = await knowledge.retrieve(
            "Как собрать целевую аудиторию?", limit=3, run_id=run_id
        )
        if external_blocked.evidence:
            raise AssertionError("Public page status incorrectly granted external_allowed")

        incompatible = KnowledgeService(repository, WrongRevisionEmbedder())
        try:
            await incompatible.retrieve("same 384 dimensions, wrong revision", run_id=run_id)
        except EmbeddingCompatibilityError:
            mismatch_rejected = True
        else:
            raise AssertionError("Search did not reject an incompatible model revision")

        # Force a database CHECK violation after the detached run is created.
        # The active pointer must remain on the successfully imported run.
        from alfa_bot.knowledge.contracts import ChunkInput, MaterialInput, SourceBlock

        bad_material = MaterialInput(
            source_table="Synthetic", source_id="failing-run-check", kind="bad",  # type: ignore[arg-type]
            title="Invalid", url="https://example.invalid/", blocks=(SourceBlock("b", 0, "text"),),
            access_type="free", answer_allowed=True,
        )
        bad_chunk = ChunkInput(0, "text", None, ("b",), 1)
        try:
            await repository.publish_run(
                (IndexedMaterial(bad_material, (bad_chunk,), ([0.0] * 384,)),),
                source_fingerprint="synthetic-failing-run-check",
                embedding_model=run_row[1], embedding_revision=run_row[2],
                pipeline_version="acceptance-check", mapping_version="acceptance-check",
                policy_version="acceptance-check",
            )
        except Exception:
            pass
        else:
            raise AssertionError("Synthetic invalid material unexpectedly published")
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT active_run_id FROM kb.index_state WHERE singleton=TRUE"
            )
            active_after_failure = (await cursor.fetchone())[0]
            cursor = await conn.execute(
                "SELECT status FROM kb.ingestion_runs WHERE source_fingerprint=%s",
                ("synthetic-failing-run-check",),
            )
            failed_run = await cursor.fetchone()
        if (
            active_after_failure != expected_active_run_id
            or failed_run is None
            or failed_run[0] != "failed"
        ):
            raise AssertionError("Failed publication changed the active run or was not marked failed")

        print(json.dumps({
            "run_id": str(run_id),
            "initial_active_run_id": str(initial_active_run_id),
            "embedding_model": run_row[1],
            "embedding_revision": run_row[2],
            "counts": counts,
            "metrics": metrics,
            "retrieve_top1": retrieval,
            "retrieve_added_document_and_term": extra_retrieval,
            "document_and_term_block_lineage_checked": True,
            "recommend_free_only": [card.source.source_id for card in one_free],
            "recommend_related_order": [card.source.source_id for card in linked],
            "recommend_exclude_remaining": [card.source.source_id for card in excluded],
            "recommend_all_excluded_empty": True,
            "recommend_no_relation_vector_fallback": True,
            "recommend_unavailable_relation_vector_fallback": True,
            "get_card_existing_missing_and_unapproved": True,
            "catalog_isolated_from_retrieve": True,
            "public_page_did_not_enable_external_context": True,
            "embedding_revision_mismatch_rejected": mismatch_rejected,
            "failed_run_preserved_active": True,
            "previous_compatible_run_retrievable": previous_run_retrievable,
        }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
