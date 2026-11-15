"""Fail-closed, read-only extraction from a restored PostgreSQL source dump.

The query is deliberately supplied only after the source DDL and joins have
been inspected. This module does not guess table or column names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from .contracts import MaterialInput, SourceBlock, SourceKey, TopicInput

_DENIED_SOURCE_TABLES = frozenset(
    {
        "Lessons",
        "LessonEmbedItems",
        "CourseUserBindings",
        "CourseUserLessonBindings",
        "UserRatings",
        "UserRatingTagsBindings",
    }
)
_REQUIRED_COLUMNS = frozenset(
    {
        "source_table",
        "source_id",
        "status_value",
        "kind",
        "title",
        "url",
        "access_type",
        "block_id",
        "block_order",
        "block_content",
    }
)
_WRITE_OR_CONTROL = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|copy|call|do|"
    r"grant|revoke|comment|refresh|vacuum|analyze|set|reset|prepare|execute)\b",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(
    r"\b(?:from|join)\s+(?:(?:\"?[\w]+\"?)\s*\.\s*)?\"?([\w]+)\"?",
    re.IGNORECASE,
)


class ExtractionConfigurationError(ValueError):
    """The source mapping does not contain enough evidence to admit records."""


class ExtractionLimitExceeded(RuntimeError):
    """A bounded read exceeded its configured row ceiling; no partial result."""


class CursorLike(Protocol):
    description: Sequence[Any]

    def execute(self, query: str, params: Any = ...) -> Any: ...
    def fetchmany(self, size: int = ...) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class ConnectionLike(Protocol):
    def cursor(self) -> CursorLike: ...
    def transaction(self) -> Any: ...


@dataclass(frozen=True)
class SourceMapping:
    """Explicit evidence for the source query and the materials it may return.

    `query` must be a single, inspected SELECT. It must return one row per
    ordered content block with the column names in `_REQUIRED_COLUMNS`, and
    may also return `summary`, `source_modified_at`, and `provenance`.

    Each source must be explicitly approved with its verified status value,
    access classification, and exact confirmed URL. A missing or mismatching
    value excludes that material. Answer and catalog permissions are separate.
    """

    query: str
    read_tables: frozenset[str]
    approved_sources: frozenset[SourceKey]
    verified_statuses: Mapping[SourceKey, frozenset[str]]
    access_by_source: Mapping[SourceKey, str]
    verified_urls: Mapping[SourceKey, str]
    external_approved: frozenset[SourceKey] = frozenset()
    answer_sources: frozenset[SourceKey] = frozenset()
    recommend_sources: frozenset[SourceKey] = frozenset()
    max_rows: int = 50_000
    mapping_version: str = "unverified"

    def validate(self) -> None:
        statement = self.query.strip()
        if not statement or ";" in statement:
            raise ExtractionConfigurationError("query must be one statement without semicolons")
        if not re.match(r"^select\b", statement, re.IGNORECASE):
            raise ExtractionConfigurationError("source query must be a plain SELECT (no CTEs)")
        if _WRITE_OR_CONTROL.search(statement):
            raise ExtractionConfigurationError("query contains a forbidden write/control keyword")
        if "--" in statement or "/*" in statement or "*/" in statement:
            raise ExtractionConfigurationError("SQL comments are not allowed in the reviewed query")
        if re.search(r"\bfrom\s*\(", statement, re.IGNORECASE):
            raise ExtractionConfigurationError(
                "derived tables are not allowed in the reviewed query"
            )
        if not self.read_tables:
            raise ExtractionConfigurationError(
                "read_tables must explicitly list every source table"
            )
        actual_tables = {name.lower() for name in _TABLE_REF.findall(statement)}
        declared_tables = {name.lower() for name in self.read_tables}
        if not actual_tables or actual_tables != declared_tables:
            raise ExtractionConfigurationError(
                "FROM/JOIN tables must exactly match the explicit read_tables allowlist"
            )
        denied = {name.lower() for name in _DENIED_SOURCE_TABLES}
        if declared_tables & denied:
            raise ExtractionConfigurationError(
                "read_tables contains a forbidden lesson/activity table"
            )
        if len(self.approved_sources) > 5_000:
            raise ExtractionConfigurationError("reviewed allowlist cannot exceed 5000 materials")
        if not self.approved_sources:
            raise ExtractionConfigurationError("approved_sources is empty; no material is admitted")
        if self.max_rows < 1 or self.max_rows > 250_000:
            raise ExtractionConfigurationError("max_rows must be between 1 and 250000")
        for key in self.approved_sources:
            if key.source_table in _DENIED_SOURCE_TABLES:
                raise ExtractionConfigurationError(
                    f"denied source table in allowlist: {key.source_table}"
                )
            if not self.verified_statuses.get(key):
                raise ExtractionConfigurationError(f"no verified public statuses for {key}")
            if self.access_by_source.get(key) not in {"free", "paid", "subscription", "unknown"}:
                raise ExtractionConfigurationError(f"no valid access classification for {key}")
            url = self.verified_urls.get(key)
            if not url or not re.match(r"^https://[^\s]+$", url, re.IGNORECASE):
                raise ExtractionConfigurationError(f"no manually verified HTTPS URL for {key}")
        if not self.external_approved.issubset(self.approved_sources):
            raise ExtractionConfigurationError("external_approved contains an unapproved source")
        if not self.answer_sources.issubset(self.approved_sources):
            raise ExtractionConfigurationError("answer_sources contains an unapproved source")
        if not self.recommend_sources.issubset(self.approved_sources):
            raise ExtractionConfigurationError("recommend_sources contains an unapproved source")
        if not (self.answer_sources | self.recommend_sources):
            raise ExtractionConfigurationError("at least one answer or catalog source is required")
        for key in self.answer_sources:
            if self.access_by_source[key] != "free":
                raise ExtractionConfigurationError(f"answer source is not confirmed free: {key}")


@dataclass(frozen=True)
class ExcludedSource:
    source: SourceKey | None
    reason: str


@dataclass(frozen=True)
class ExtractionReport:
    mapping_version: str
    included: tuple[SourceKey, ...] = ()
    excluded: tuple[ExcludedSource, ...] = ()
    rows_read: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return an audit-safe report; source text is never included."""
        exclusion_counts: dict[str, int] = {}
        for item in self.excluded:
            exclusion_counts[item.reason] = exclusion_counts.get(item.reason, 0) + 1
        included_counts: dict[str, int] = {}
        for key in self.included:
            included_counts[key.source_table] = included_counts.get(key.source_table, 0) + 1
        return {
            "mapping_version": self.mapping_version,
            "rows_read": self.rows_read,
            "included_counts": dict(sorted(included_counts.items())),
            "included": [
                {"source_table": key.source_table, "source_id": key.source_id}
                for key in self.included
            ],
            "excluded": [
                {
                    "source_table": item.source.source_table if item.source else None,
                    "source_id": item.source.source_id if item.source else None,
                    "reason": item.reason,
                }
                for item in self.excluded
            ],
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
        }


@dataclass(frozen=True)
class ExtractionResult:
    materials: tuple[MaterialInput, ...]
    report: ExtractionReport


@dataclass
class _GroupedRows:
    key: SourceKey
    status: set[str] = field(default_factory=set)
    kind: set[str] = field(default_factory=set)
    title: set[str] = field(default_factory=set)
    url: set[str | None] = field(default_factory=set)
    access_type: set[str] = field(default_factory=set)
    summary: set[str] = field(default_factory=set)
    modified: set[datetime | None] = field(default_factory=set)
    provenance: dict[str, Any] = field(default_factory=dict)
    provenance_values: set[str] = field(default_factory=set)
    blocks: dict[str, SourceBlock] = field(default_factory=dict)
    malformed_block: bool = False


def extract_materials(
    connection: ConnectionLike,
    mapping: SourceMapping,
    *,
    max_materials: int = 20,
    batch_size: int = 1000,
) -> ExtractionResult:
    """Run the reviewed mapping as a read-only query and return eligible rows.

    The query and all joins must be derived from inspected source DDL. The
    allowlist carries reviewed source IDs, status meanings, access decisions,
    answer/catalog permissions and exact URL checks. Closed or user data are
    never admitted.
    """
    mapping.validate()
    if not 1 <= max_materials <= 5_000:
        raise ValueError("max_materials must be between 1 and 5000")
    if batch_size < 1 or batch_size > 10_000:
        raise ValueError("batch_size must be between 1 and 10000")

    grouped: dict[SourceKey, _GroupedRows] = {}
    excluded: dict[SourceKey, str] = {}
    rows_read = 0
    with connection.transaction():
        cursor = connection.cursor()
        try:
            # The source connection should also use a SELECT-only DB role.
            # This transaction-level guard protects callers with broader roles.
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(mapping.query)
            columns = [col.name if hasattr(col, "name") else col[0] for col in cursor.description]
            if not _REQUIRED_COLUMNS.issubset(columns):
                missing = sorted(_REQUIRED_COLUMNS.difference(columns))
                raise ExtractionConfigurationError(
                    "source query lacks required columns: " + ", ".join(missing)
                )
            indexes = {name: columns.index(name) for name in columns}
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                rows_read += len(rows)
                if rows_read > mapping.max_rows:
                    raise ExtractionLimitExceeded(
                        f"query exceeded max_rows={mapping.max_rows}; batch discarded"
                    )
                for row in rows:
                    try:
                        key = SourceKey(
                            str(row[indexes["source_table"]]),
                            str(row[indexes["source_id"]]),
                        )
                        status = str(row[indexes["status_value"]])
                        kind = str(row[indexes["kind"]])
                        title_value = row[indexes["title"]]
                        title = str(title_value).strip() if title_value is not None else ""
                        url_value = row[indexes["url"]]
                        url = str(url_value).strip() if url_value is not None else None
                        access_type = str(row[indexes["access_type"]])
                        block_id = str(row[indexes["block_id"]])
                        block_order = int(row[indexes["block_order"]])
                        content_value = row[indexes["block_content"]]
                        content = "" if content_value is None else str(content_value)
                    except (TypeError, ValueError, IndexError):
                        excluded[SourceKey("<query-row>", str(rows_read))] = (
                            "malformed normalized row"
                        )
                        continue

                    if key not in mapping.approved_sources:
                        excluded[key] = "source ID is not in the reviewed allowlist"
                        continue
                    if key.source_table in _DENIED_SOURCE_TABLES:
                        excluded[key] = (
                            "source table is explicitly excluded (paid lessons or user activity)"
                        )
                        continue
                    item = grouped.setdefault(key, _GroupedRows(key))
                    item.status.add(status)
                    item.kind.add(kind)
                    item.title.add(title)
                    item.url.add(url)
                    item.access_type.add(access_type)
                    item.summary.add(
                        str(row[indexes["summary"]] or "").strip() if "summary" in indexes else ""
                    )
                    modified = (
                        row[indexes["source_modified_at"]]
                        if "source_modified_at" in indexes
                        else None
                    )
                    item.modified.add(modified if isinstance(modified, datetime) else None)
                    if "provenance" in indexes:
                        raw_provenance = row[indexes["provenance"]]
                        normalized = (
                            dict(raw_provenance) if isinstance(raw_provenance, Mapping) else {}
                        )
                        item.provenance_values.add(repr(normalized))
                        item.provenance = normalized
                    if not block_id or not content.strip():
                        item.malformed_block = True
                    elif block_id in item.blocks:
                        # The same source block twice often means a bad JOIN; fail closed.
                        item.malformed_block = True
                    else:
                        item.blocks[block_id] = SourceBlock(block_id, block_order, content)
        finally:
            cursor.close()

    result: list[MaterialInput] = []
    for key in sorted(mapping.approved_sources, key=lambda k: (k.source_table, k.source_id)):
        item = grouped.get(key)
        if item is None:
            excluded.setdefault(
                key, "approved source produced no rows; source ID/JOIN may be stale"
            )
            continue
        reason = _rejection_reason(item, mapping)
        if reason:
            excluded[key] = reason
            continue
        if len(result) >= max_materials:
            excluded[key] = f"material cap ({max_materials}) reached"
            continue
        status = next(iter(item.status))
        expected_url = mapping.verified_urls[key]
        ordered_blocks = tuple(
            sorted(item.blocks.values(), key=lambda block: (block.order, block.block_id))
        )
        answer_allowed = key in mapping.answer_sources
        recommend_allowed = key in mapping.recommend_sources
        query_provenance = item.provenance
        topics = tuple(
            TopicInput(str(topic["source_id"]), str(topic["title"]))
            for topic in query_provenance.get("topics", ())
            if isinstance(topic, Mapping) and topic.get("source_id") and topic.get("title")
        )
        recommendations = tuple(
            SourceKey(str(target["source_table"]), str(target["source_id"]))
            for target in query_provenance.get("recommendations", ())
            if isinstance(target, Mapping)
            and target.get("source_table")
            and target.get("source_id")
        )
        price_value = query_provenance.get("price")
        price = Decimal(str(price_value)) if price_value is not None else None
        currency = query_provenance.get("currency")
        price_note = query_provenance.get("price_note")
        reason_features = tuple(
            str(feature) for feature in query_provenance.get("reason_features", ())
        )
        external_allowed = key in mapping.external_approved
        result.append(
            MaterialInput(
                source_table=key.source_table,
                source_id=key.source_id,
                kind=next(iter(item.kind)),
                title=next(iter(item.title)),
                url=expected_url,
                blocks=ordered_blocks,
                access_type=next(iter(item.access_type)),
                summary=next(iter(item.summary), ""),
                answer_allowed=answer_allowed,
                recommend_allowed=recommend_allowed,
                external_allowed=external_allowed,
                source_modified_at=next(iter(item.modified), None),
                provenance={
                    "mapping_version": mapping.mapping_version,
                    "verified_status": status,
                    "url_exact_match_verified": True,
                    **query_provenance,
                },
                price=price,
                currency=str(currency) if currency else None,
                price_note=str(price_note) if price_note else None,
                reason_features=reason_features,
                topics=topics,
                recommendations=recommendations,
            )
        )

    report = ExtractionReport(
        mapping_version=mapping.mapping_version,
        included=tuple(SourceKey(m.source_table, m.source_id) for m in result),
        excluded=tuple(
            ExcludedSource(source, reason)
            for source, reason in sorted(
                excluded.items(), key=lambda item: (item[0].source_table, item[0].source_id)
            )
        ),
        rows_read=rows_read,
    )
    return ExtractionResult(tuple(result), report)


def _rejection_reason(item: _GroupedRows, mapping: SourceMapping) -> str | None:
    key = item.key
    if item.malformed_block:
        return "empty/duplicate content block or invalid block order"
    if len(item.provenance_values) > 1:
        return "inconsistent metadata across joined rows"
    for field_name, values in (
        ("status", item.status),
        ("kind", item.kind),
        ("title", item.title),
        ("URL", item.url),
        ("access type", item.access_type),
        ("summary", item.summary),
    ):
        if len(values) != 1:
            return f"inconsistent {field_name} across joined rows"
    status = next(iter(item.status))
    if status not in mapping.verified_statuses[key]:
        return "status value is not confirmed as public/active"
    if next(iter(item.kind)) not in {"article", "document", "term", "course", "webinar"}:
        return "material kind is outside the kb contract"
    observed_access = next(iter(item.access_type))
    if observed_access != mapping.access_by_source.get(key):
        return "access classification is missing or differs from reviewed data"
    if key not in mapping.answer_sources and key not in mapping.recommend_sources:
        return "source has no reviewed answer or catalog permission"
    url = next(iter(item.url))
    if not url or url != mapping.verified_urls.get(key):
        return "URL is missing or differs from the exact reviewed URL"
    if not re.match(r"^https://[^\s]+$", url, re.IGNORECASE):
        return "URL is not HTTPS"
    if not next(iter(item.title)).strip():
        return "title is empty"
    if not item.blocks:
        return "no content blocks"
    if key in mapping.answer_sources and observed_access != "free":
        return "answer evidence is not confirmed free"
    if key in mapping.recommend_sources and not next(iter(item.summary)).strip():
        return "catalog summary is empty"
    return None
