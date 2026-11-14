
import math
import os
from html import escape

from alfa_bot.contracts import Evidence, Profile, SearchResult


def _load_max_distance() -> float:
    raw_value = os.getenv("RAG_MAX_DISTANCE", "0.55")

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(
            "RAG_MAX_DISTANCE должен быть числом"
        ) from exc

    if not math.isfinite(value) or value < 0:
        raise ValueError(
            "RAG_MAX_DISTANCE должен быть конечным неотрицательным числом"
        )

    return value


MAX_DISTANCE = _load_max_distance()


def build_search_query(
    question: str,
    clarification: str | None,
    profile: Profile,
) -> str:
    """Формирует поисковый запрос из вопроса, уточнения и профиля."""
    parts = [question.strip()]

    if clarification and clarification.strip():
        parts.append(clarification.strip())

    for label, value in (
        ("стадия бизнеса", profile.stage),
        ("отрасль", profile.industry),
        ("опыт", profile.experience),
        ("цель", profile.current_goal),
        ("налоговый режим", profile.tax_regime),
    ):
        if value and value.strip():
            parts.append(f"{label}: {value.strip()}")

    return ". ".join(parts)


def filter_relevant(
    result: SearchResult,
) -> tuple[Evidence, ...]:
    """Оставляет фрагменты с допустимым distance."""
    return tuple(
        evidence
        for evidence in result.evidence
        if evidence.distance <= MAX_DISTANCE
    )


def build_context(
    evidence: tuple[Evidence, ...],
) -> str:
    """Формирует нумерованный контекст источников для LLM."""
    blocks = []

    for index, item in enumerate(evidence, start=1):
        title = escape(item.title, quote=True)
        text = escape(item.text)

        blocks.append(
            f'<source n="{index}" title="{title}">\n'
            f"{text}\n"
            "</source>"
        )

    return "\n\n".join(blocks)
