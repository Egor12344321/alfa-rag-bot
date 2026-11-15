"""Reviewed mapping for public articles, documents, terms, and course cards.

Documents and glossary entries are admitted only by a per-ID official public
page check because the dump does not encode their complete access policy.
"""

from __future__ import annotations

from .contracts import SourceKey
from .first_pass_sources import _SOURCES as FIRST_ARTICLES
from .source import SourceMapping

_COURSES: tuple[tuple[str, str, str], ...] = (
    (
        "39d56e76-2da6-4ac4-8c57-1e7ba2b856c7",
        "keysy-predprinimateley-kak-spravitsya-s-vyzovami-v-biznese",
        "https://kurs.alfabank.ru/courses/keysy-predprinimateley-kak-spravitsya-s-vyzovami-v-biznese/",
    ),
    (
        "3d17b03e-0e69-44cf-aa04-110e92437016",
        "prosto-o-slozhnom",
        "https://kurs.alfabank.ru/courses/prosto-o-slozhnom/",
    ),
    (
        "d0aa4598-3da2-478b-bbf8-bda81b231b58",
        "marketpleysy-s-chego-nachat-kak-preuspet",
        "https://kurs.alfabank.ru/courses/marketpleysy-s-chego-nachat-kak-preuspet/",
    ),
    (
        "b9045d6e-07ad-407f-a305-472e82fef797",
        "biznes-bez-trevog-zapusk-proekta-v-sfere-uslug",
        "https://kurs.alfabank.ru/courses/biznes-bez-trevog-zapusk-proekta-v-sfere-uslug/",
    ),
    (
        "b3f8439b-f5c0-4c97-b283-a62a524c29dc",
        "biznes-bez-skolov-manikyurnaya-studiya-mechty",
        "https://kurs.alfabank.ru/courses/biznes-bez-skolov-manikyurnaya-studiya-mechty/",
    ),
)
_EXTRA_ARTICLES: tuple[tuple[str, str], ...] = (
    ("fba0ba5a-45d3-40be-9d57-3872f5ff226a", "top-8-kursov-po-marketplejsam"),
    (
        "5197669c-e7c3-4465-9a16-e7eeb13ee37c",
        "pereraschyot-stoimosti-patenta-est-li-smysl-i-kakie-pravila",
    ),
)
_SOURCES = (*FIRST_ARTICLES, *_EXTRA_ARTICLES)
ARTICLE_KEYS = frozenset(SourceKey("Articles", source_id) for source_id, _ in _SOURCES)
DOCUMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "defc5abc-e692-4ae8-87f6-987d791705b0",
        "soglashenie-o-nerazglashenii",
        "https://kurs.alfabank.ru/documents/soglashenie-o-nerazglashenii/",
    ),
)
GLOSSARY: tuple[tuple[str, str, str], ...] = (
    (
        "58e6c342-30d9-4300-bf15-183e799402a2",
        "doverennost",
        "https://kurs.alfabank.ru/vocabulary/doverennost/",
    ),
)
_COURSE_KEYS = frozenset(SourceKey("Courses", source_id) for source_id, _, _ in _COURSES)
_DOCUMENT_KEYS = frozenset(SourceKey("Documents", source_id) for source_id, _, _ in DOCUMENTS)
_GLOSSARY_KEYS = frozenset(SourceKey("Glossary", source_id) for source_id, _, _ in GLOSSARY)
_ALL_KEYS = ARTICLE_KEYS | _COURSE_KEYS | _DOCUMENT_KEYS | _GLOSSARY_KEYS
_ARTICLE_URLS = {
    SourceKey("Articles", source_id): f"https://kurs.alfabank.ru/articles/{slug}/"
    for source_id, slug in _SOURCES
}
_URLS = {
    **_ARTICLE_URLS,
    **{SourceKey("Courses", source_id): url for source_id, _, url in _COURSES},
    **{SourceKey("Documents", source_id): url for source_id, _, url in DOCUMENTS},
    **{SourceKey("Glossary", source_id): url for source_id, _, url in GLOSSARY},
}
_STATUS = {key: frozenset({"Опубликовано"}) for key in _ALL_KEYS}

_ARTICLE_IDS = ", ".join(f"'{source_id}'::uuid" for source_id, _ in _SOURCES)
_COURSE_IDS = ", ".join(f"'{source_id}'::uuid" for source_id, _, _ in _COURSES)
_DOCUMENT_IDS = ", ".join(f"'{source_id}'::uuid" for source_id, _, _ in DOCUMENTS)
_GLOSSARY_IDS = ", ".join(f"'{source_id}'::uuid" for source_id, _, _ in GLOSSARY)
_TOPIC_ARTICLES = "8ede4179-0faf-407d-a4fc-1d94f9a63089"
_TOPIC_COURSES = "8b440b1a-00c3-423a-9bae-3376475eb203"
_TYPE_ARTICLES = "8ede4179-0faf-407d-a4fc-1d94f9a63089"
_TYPE_COURSES = "8b440b1a-00c3-423a-9bae-3376475eb203"
_TYPE_DOCUMENTS = "da1ad865-8605-4ab1-9f6c-4489a26fbc35"
_TYPE_WEBINARS = "ece4acf3-5dd3-47b9-9f08-99c0d6203cc7"


def _metadata(entity_id: str, entity_type_id: str, source_type: str, is_public: str) -> str:
    """Common SQL JSON carrying only reviewed topic and recommendation edges."""
    return f"""jsonb_build_object(
        'source_type', {source_type},
        'is_public', {is_public},
        'price', NULL,
        'currency', NULL,
        'topics', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('source_id', t.\"Id\"::text,
                                                'title', t.\"Title\"::text)
                             ORDER BY cst.\"Order\", t.\"Id\")
            FROM public.\"ContentSettings\" cs
            JOIN public.\"Statuses\" css ON css.\"Id\" = cs.\"StatusId\"
            JOIN public.\"ContentSettingsTopics\" cst
              ON cst.\"ContentSettingsId\" = cs.\"Id\"
            JOIN public.\"Topics\" t ON t.\"Id\" = cst.\"TopicId\"
            WHERE cs.\"EntityId\" = {entity_id}
              AND cs.\"EntityTypeId\" = '{entity_type_id}'::uuid
              AND css.\"Title\" = 'Опубликовано'
              AND cs.\"Published\" IS NOT NULL AND cs.\"IsDeleted\" = FALSE
              AND t.\"IsPublic\" = TRUE AND t.\"IsDeleted\" = FALSE
        ), '[]'::jsonb),
        'recommendations', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'source_table', CASE ri.\"ItemTypeId\"
                           WHEN '{_TYPE_ARTICLES}'::uuid THEN 'Articles'
                           WHEN '{_TYPE_COURSES}'::uuid THEN 'Courses'
                           WHEN '{_TYPE_DOCUMENTS}'::uuid THEN 'Documents'
                           WHEN '{_TYPE_WEBINARS}'::uuid THEN 'Webinars'
                       END,
                       'source_id', ri.\"ItemId\"::text)
                       ORDER BY ri.\"Order\" NULLS LAST, ri.\"ItemId\")
            FROM public.\"RecommendedItems\" ri
            WHERE ri.\"Id\" = {entity_id}
              AND ri.\"ItemTypeId\" IN (
                  '{_TYPE_ARTICLES}'::uuid, '{_TYPE_COURSES}'::uuid,
                  '{_TYPE_DOCUMENTS}'::uuid, '{_TYPE_WEBINARS}'::uuid)
              AND (
                  (ri.\"ItemTypeId\" = '{_TYPE_ARTICLES}'::uuid AND EXISTS
                    (SELECT 1 FROM public.\"Articles\" target WHERE target.\"Id\" = ri.\"ItemId\"))
                  OR (ri.\"ItemTypeId\" = '{_TYPE_COURSES}'::uuid AND EXISTS
                    (SELECT 1 FROM public.\"Courses\" target WHERE target.\"Id\" = ri.\"ItemId\"))
                  OR (ri.\"ItemTypeId\" = '{_TYPE_DOCUMENTS}'::uuid AND EXISTS
                    (SELECT 1 FROM public.\"Documents\" target WHERE target.\"Id\" = ri.\"ItemId\"))
                  OR (ri.\"ItemTypeId\" = '{_TYPE_WEBINARS}'::uuid AND EXISTS
                    (SELECT 1 FROM public.\"Webinars\" target WHERE target.\"Id\" = ri.\"ItemId\"))
              )
        ), '[]'::jsonb)
    )"""


_ARTICLE_PROVENANCE = _metadata('a."Id"', _TOPIC_ARTICLES, 'e."Type"', 'a."IsPublic"')
_COURSE_PROVENANCE = _metadata(
    'c."Id"',
    _TOPIC_COURSES,
    'c."TypeId"',
    "(ct.\"Title\" = 'Открытый')",
)

QUERY = f"""
SELECT
    'Articles'::text AS source_table,
    a.\"Id\"::text AS source_id,
    s.\"Title\"::text AS status_value,
    'article'::text AS kind,
    a.\"Title\"::text AS title,
    ('https://kurs.alfabank.ru/articles/' || a.\"Url\"::text || '/')::text AS url,
    'free'::text AS access_type,
    e.\"Id\"::text AS block_id,
    e.\"Order\" AS block_order,
    e.\"HtmlContent\"::text AS block_content,
    a.\"CardDescription\"::text AS summary,
    a.\"Modified\" AS source_modified_at,
    {_ARTICLE_PROVENANCE} AS provenance
FROM public.\"Articles\" a
JOIN public.\"Statuses\" s ON s.\"Id\" = a.\"StatusId\"
JOIN public.\"ArticleEmbedItems\" e ON e.\"ArticleId\" = a.\"Id\"
WHERE a.\"Id\" = ANY(ARRAY[{_ARTICLE_IDS}])
  AND s.\"Title\" = 'Опубликовано' AND a.\"IsPublic\" = TRUE
  AND a.\"IsClosed\" = FALSE AND a.\"IsLesson\" = FALSE
  AND a.\"IsDeleted\" = FALSE AND a.\"NoIndex\" = FALSE
  AND a.\"Published\" IS NOT NULL AND e.\"Type\" = 0
  AND e.\"Order\" IS NOT NULL AND e.\"HtmlContent\" IS NOT NULL
  AND length(btrim(e.\"HtmlContent\"::text)) > 0
UNION ALL
SELECT
    'Courses'::text AS source_table,
    c.\"Id\"::text AS source_id,
    s.\"Title\"::text AS status_value,
    'course'::text AS kind,
    c.\"Title\"::text AS title,
    ('https://kurs.alfabank.ru/courses/' || c.\"Url\"::text || '/')::text AS url,
    CASE ct.\"Title\"
        WHEN 'Открытый' THEN 'free'
        WHEN 'Платный' THEN 'paid'
        WHEN 'По подписке' THEN 'subscription'
        ELSE 'unknown'
    END::text AS access_type,
    'Description'::text AS block_id,
    0::integer AS block_order,
    c.\"Description\"::text AS block_content,
    c.\"Description\"::text AS summary,
    c.\"Modified\" AS source_modified_at,
    {_COURSE_PROVENANCE} || jsonb_build_object(
        'price', CASE
            WHEN ct.\"Title\" = 'Платный' AND c.\"Cost\" > 0
             AND (SELECT count(*) FROM public.\"CourseTariffBindings\" tb
                  WHERE tb.\"CourseId\" = c.\"Id\" AND tb.\"IsMain\") = 1
             AND (SELECT min(tb.\"Cost\") FROM public.\"CourseTariffBindings\" tb
                  WHERE tb.\"CourseId\" = c.\"Id\" AND tb.\"IsMain\") = c.\"Cost\"
            THEN c.\"Cost\"::numeric ELSE NULL END,
        'price_note', CASE
            WHEN ct.\"Title\" = 'Платный' AND c.\"Cost\" > 0
             AND (SELECT count(*) FROM public.\"CourseTariffBindings\" tb
                  WHERE tb.\"CourseId\" = c.\"Id\" AND tb.\"IsMain\") = 1
             AND (SELECT min(tb.\"Cost\") FROM public.\"CourseTariffBindings\" tb
                  WHERE tb.\"CourseId\" = c.\"Id\" AND tb.\"IsMain\") = c.\"Cost\"
            THEN 'Course.Cost подтверждён единственным основным тарифом'
            ELSE NULL END
    ) AS provenance
FROM public.\"Courses\" c
JOIN public.\"Statuses\" s ON s.\"Id\" = c.\"StatusId\"
JOIN public.\"CourseTypes\" ct ON ct.\"Id\" = c.\"TypeId\"
WHERE c.\"Id\" = ANY(ARRAY[{_COURSE_IDS}])
  AND s.\"Title\" = 'Опубликовано' AND NOT c.\"IsDeleted\"
  AND NOT c.\"NoIndex\" AND NOT c.\"IsClosed\"
  AND c.\"Published\" IS NOT NULL AND length(btrim(c.\"Url\"::text)) > 0
  AND length(btrim(c.\"Description\"::text)) > 0
UNION ALL
SELECT
    'Documents'::text AS source_table,
    d.\"Id\"::text AS source_id,
    s.\"Title\"::text AS status_value,
    'document'::text AS kind,
    d.\"Title\"::text AS title,
    ('https://kurs.alfabank.ru/documents/' || d.\"Url\"::text || '/')::text AS url,
    'free'::text AS access_type,
    e.\"Id\"::text AS block_id,
    e.\"Order\" AS block_order,
    e.\"HtmlContent\"::text AS block_content,
    COALESCE(d.\"Description\"::text, '') AS summary,
    d.\"Modified\" AS source_modified_at,
    jsonb_build_object(
        'access_confirmation', 'official public-site search/crawl',
        'access_confirmed_on', '2026-11-12',
        'public_access_confirmed', TRUE,
        'external_transfer_approved', FALSE
    ) AS provenance
FROM public.\"Documents\" d
JOIN public.\"Statuses\" s ON s.\"Id\" = d.\"StatusId\"
JOIN public.\"DocumentEmbedItems\" e ON e.\"DocumentId\" = d.\"Id\"
WHERE d.\"Id\" = ANY(ARRAY[{_DOCUMENT_IDS}])
  AND s.\"Title\" = 'Опубликовано' AND d.\"IsDeleted\" = FALSE
  AND d.\"NoIndex\" = FALSE AND e.\"Type\" = 0
  AND e.\"Order\" IS NOT NULL AND e.\"HtmlContent\" IS NOT NULL
  AND length(btrim(e.\"HtmlContent\"::text)) > 0
UNION ALL
SELECT
    'Glossary'::text AS source_table,
    g.\"Id\"::text AS source_id,
    s.\"Title\"::text AS status_value,
    'term'::text AS kind,
    g.\"Title\"::text AS title,
    ('https://kurs.alfabank.ru/vocabulary/' || g.\"Url\"::text || '/')::text AS url,
    'free'::text AS access_type,
    e.\"Id\"::text AS block_id,
    e.\"Order\" AS block_order,
    e.\"HtmlContent\"::text AS block_content,
    COALESCE(g.\"Description\"::text, '') AS summary,
    g.\"Modified\" AS source_modified_at,
    jsonb_build_object(
        'access_confirmation', 'official public-site search/crawl',
        'access_confirmed_on', '2026-11-12',
        'public_access_confirmed', TRUE,
        'external_transfer_approved', FALSE
    ) AS provenance
FROM public.\"Glossary\" g
JOIN public.\"Statuses\" s ON s.\"Id\" = g.\"StatusId\"
JOIN public.\"GlossaryEmbedItems\" e ON e.\"GlossaryId\" = g.\"Id\"
WHERE g.\"Id\" = ANY(ARRAY[{_GLOSSARY_IDS}])
  AND s.\"Title\" = 'Опубликовано' AND g.\"IsDeleted\" = FALSE
  AND g.\"NoIndex\" = FALSE AND g.\"Published\" IS NOT NULL
  AND e.\"Type\" = 0 AND e.\"Order\" IS NOT NULL
  AND e.\"HtmlContent\" IS NOT NULL
  AND length(btrim(e.\"HtmlContent\"::text)) > 0
ORDER BY source_table, source_id, block_order, block_id
""".strip()


def build_mapping() -> SourceMapping:
    return SourceMapping(
        query=QUERY,
        read_tables=frozenset(
            {
                "Articles",
                "Statuses",
                "ArticleEmbedItems",
                "Courses",
                "CourseTypes",
                "CourseTariffBindings",
                "ContentSettings",
                "ContentSettingsTopics",
                "Topics",
                "RecommendedItems",
                "Documents",
                "DocumentEmbedItems",
                "Glossary",
                "GlossaryEmbedItems",
                "Webinars",
            }
        ),
        approved_sources=_ALL_KEYS,
        verified_statuses=_STATUS,
        access_by_source={key: "free" for key in _ALL_KEYS},
        verified_urls=_URLS,
        answer_sources=ARTICLE_KEYS | _DOCUMENT_KEYS | _GLOSSARY_KEYS,
        recommend_sources=_COURSE_KEYS,
        external_approved=frozenset(),
        max_rows=3_000,
        mapping_version="articles-documents-glossary-and-course-catalog-reviewed-2026-11-12-v2",
    )
