"""Читает разрешённые поля из восстановленной БД организаторов."""

import re
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from alfa_bot.contracts import MaterialAccess
from alfa_bot.knowledge.corpus import PublishedSource, SourceType

# Имена SQL-идентификаторов берутся только из этого списка.
CONTENT_TABLES = (
    ("article", "Articles", "ArticleEmbedItems", "ArticleId"),
    ("document", "Documents", "DocumentEmbedItems", "DocumentId"),
    ("glossary", "Glossary", "GlossaryEmbedItems", "GlossaryId"),
)
PUBLISHED = """
    status."Title" = 'Опубликовано'
    AND NOT item."IsDeleted"
    AND (item."Published" IS NULL OR item."Published" <= now())
"""


def published_url(raw_value: str, template: str) -> str | None:
    value = raw_value.strip()
    if value.startswith(("https://", "http://")):
        return value
    if re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        return template.format(slug=value)
    return None


async def read_source_database(
    connection: AsyncConnection, templates: Mapping[SourceType, str]
) -> list[PublishedSource]:
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    sources = []
    for kind, table, embed_items, foreign_key in CONTENT_TABLES:
        article_policy = (
            'AND item."IsPublic" AND NOT item."IsClosed" AND NOT item."IsLesson"'
            if kind == "article"
            else ""
        )
        query = f"""
            SELECT item."Id" AS id, item."Title" AS title, item."Description" AS description,
                item."Url" AS url,
                string_agg(block."HtmlContent", chr(10) ORDER BY block."Order", block."Id") AS body
            FROM public."{table}" item
            JOIN public."Statuses" status ON item."StatusId" = status."Id"
            LEFT JOIN public."{embed_items}" block ON block."{foreign_key}" = item."Id"
            WHERE {PUBLISHED} {article_policy}
            GROUP BY item."Id"
            ORDER BY item."Id"
        """
        for row in (await connection.execute(text(query))).mappings():
            sources.append(
                PublishedSource(
                    source_id=str(row["id"]),
                    source_type=kind,
                    title=row["title"],
                    description=row["description"] or "",
                    body=row["body"] or "",
                    url=published_url(row["url"], templates[kind]),
                    is_active=True,
                    is_public=True,
                    access=MaterialAccess.FREE,
                )
            )
    # Читаем только опубликованную витрину. Уроки и таблицы пользователей не запрашиваем.
    courses = await connection.execute(
        text(f"""
        SELECT item."Id" AS id, item."Title" AS title, item."Description" AS description,
               item."Url" AS url, item."Cost" AS cost
        FROM public."Courses" item
        JOIN public."Statuses" status ON item."StatusId" = status."Id"
        WHERE {PUBLISHED} AND NOT item."IsClosed"
        ORDER BY item."Id"
    """)
    )
    for row in courses.mappings():
        cost = row["cost"]
        if cost is None or cost == 0:
            access = MaterialAccess.FREE
        else:
            access = MaterialAccess.PAID if cost > 0 else MaterialAccess.UNKNOWN
        sources.append(
            PublishedSource(
                source_id=str(row["id"]),
                source_type="course",
                title=row["title"],
                description=row["description"] or "",
                url=published_url(
                    row["url"],
                    templates["course"],
                ),
                access=access,
                price=f"{cost} ₽" if access == MaterialAccess.PAID else None,
                is_active=True,
                is_public=True,
            )
        )
    return sources
