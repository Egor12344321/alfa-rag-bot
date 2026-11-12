"""Reviewed allowlist for the first public article import (10 records)."""

from __future__ import annotations

from .contracts import SourceKey
from .source import SourceMapping

_SOURCES: tuple[tuple[str, str], ...] = (
    ("f70c43ee-bc3d-48d0-bf24-abdeca927b48", "vosem-shagov-dlya-razvitiya-lichnogo-brenda"),
    (
        "4edc4abf-1f3c-41db-be25-25dfae8486b0",
        "issledovanie-pokupatelej-dlya-proizvoditelej-i-optovikov",
    ),
    ("5a5d6364-9ffc-465f-aa4f-3401aeb3e877", "kak-biznesu-menshe-zaviset-ot-kursa-valyut"),
    ("a0de1a97-1261-49bd-89e1-ac3bba5c530f", "kak-ispolzovat-teoriyu-pokolenij-v-marketinge"),
    ("f079c693-aafb-46f3-987b-6d071cc5c1f1", "kak-preodolet-spad-prodazh-posle-novogo-goda"),
    (
        "d1e67c81-f364-4724-bfac-d02293e87cb8",
        "kak-sobrat-celevuyu-auditoriyu-otkryvaem-sezon-ohoty-na-klientov",
    ),
    ("28c8ec93-d65b-4382-81f2-183915480f6b", "krizisy-rosta-kompanii-i-kak-ih-preodolet"),
    (
        "7ed5b9e2-d183-4440-b467-f6ce6fcea1be",
        "postoplata-tovarov-i-uslug-chto-ehto-i-kak-s-nej-rabotat",
    ),
    ("2cf5e7cc-6abb-47b5-afc9-ab672a641b99", "kak-markirovat-tovary-na-wildberries"),
    ("2586489a-b699-493c-911d-030e43fbc4f1", "kak-markirovat-tovary-na-ozon"),
)

_KEYS = frozenset(SourceKey("Articles", source_id) for source_id, _ in _SOURCES)
_URL_BY_KEY = {
    SourceKey("Articles", source_id): f"https://kurs.alfabank.ru/articles/{slug}/"
    for source_id, slug in _SOURCES
}
_STATUS_BY_KEY = {key: frozenset({"Опубликовано"}) for key in _KEYS}

_IDS = ", ".join(f"'{source_id}'::uuid" for source_id, _ in _SOURCES)
QUERY = f"""
SELECT
    'Articles'::text AS source_table,
    a."Id"::text AS source_id,
    s."Title"::text AS status_value,
    'article'::text AS kind,
    a."Title"::text AS title,
    ('https://kurs.alfabank.ru/articles/' || a."Url"::text || '/')::text AS url,
    'free'::text AS access_type,
    e."Id"::text AS block_id,
    e."Order" AS block_order,
    e."HtmlContent"::text AS block_content,
    a."CardDescription"::text AS summary,
    a."Modified" AS source_modified_at,
    jsonb_build_object('source_type', e."Type", 'is_public', a."IsPublic") AS provenance
FROM public."Articles" AS a
JOIN public."Statuses" AS s ON s."Id" = a."StatusId"
JOIN public."ArticleEmbedItems" AS e ON e."ArticleId" = a."Id"
WHERE a."Id" = ANY(ARRAY[{_IDS}])
  AND s."Title" = 'Опубликовано'
  AND a."IsPublic" = TRUE
  AND a."IsClosed" = FALSE
  AND a."IsLesson" = FALSE
  AND a."IsDeleted" = FALSE
  AND a."NoIndex" = FALSE
  AND a."Published" IS NOT NULL
  AND e."Type" = 0
  AND e."Order" IS NOT NULL
  AND e."HtmlContent" IS NOT NULL
  AND length(btrim(e."HtmlContent"::text)) > 0
ORDER BY a."Id", e."Order", e."Id"
""".strip()


def build_mapping() -> SourceMapping:
    """Return the checked first-pass mapping for a restored alfa_source DB."""
    return SourceMapping(
        query=QUERY,
        read_tables=frozenset({"Articles", "Statuses", "ArticleEmbedItems"}),
        approved_sources=_KEYS,
        verified_statuses=_STATUS_BY_KEY,
        access_by_source={key: "free" for key in _KEYS},
        verified_urls=_URL_BY_KEY,
        answer_sources=_KEYS,
        # Public URLs are verified, but transfer to an external LLM was not approved.
        external_approved=frozenset(),
        max_rows=2_000,
        mapping_version="articles-public-allowlist-2026-11-11-v1",
    )
