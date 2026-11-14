
from uuid import uuid4

import pytest

from alfa_bot.assistant.context import (
    MAX_DISTANCE,
    build_context,
    build_search_query,
    filter_relevant,
)
from alfa_bot.contracts import Evidence, Profile, SearchResult, SourceKey


@pytest.fixture
def profile():
    return Profile(
        stage="запуск",
        industry="торговля",
        experience="начинающий",
        current_goal="открыть ИП",
        tax_regime="УСН",
        free_only=True,
    )


@pytest.fixture
def evidence():
    return (
        Evidence(
            chunk_id=uuid4(),
            material_id=uuid4(),
            source=SourceKey(
                source_table="materials",
                source_id="1",
            ),
            title="Регистрация ИП",
            text="Для регистрации ИП нужно подать заявление.",
            url=None,
            distance=0.2,
        ),
        Evidence(
            chunk_id=uuid4(),
            material_id=uuid4(),
            source=SourceKey(
                source_table="materials",
                source_id="2",
            ),
            title="Налоговый режим",
            text="Для ИП доступна упрощённая система налогообложения.",
            url=None,
            distance=0.8,
        ),
    )


def make_search_result(
    evidence_items: tuple[Evidence, ...],
) -> SearchResult:
    return SearchResult(
        run_id=uuid4(),
        evidence=evidence_items,
    )


def test_build_search_query_with_empty_profile():
    empty_profile = Profile(
        stage=None,
        industry=None,
        experience=None,
        current_goal=None,
        tax_regime=None,
        free_only=False,
    )

    query = build_search_query(
        "Как открыть ИП?",
        None,
        empty_profile,
    )

    assert query == "Как открыть ИП?"


def test_build_search_query_includes_profile(profile):
    query = build_search_query(
        "Как открыть ИП?",
        None,
        profile,
    )

    assert "Как открыть ИП?" in query
    assert "стадия бизнеса: запуск" in query
    assert "отрасль: торговля" in query
    assert "опыт: начинающий" in query
    assert "цель: открыть ИП" in query
    assert "налоговый режим: УСН" in query


def test_build_search_query_includes_clarification(profile):
    query = build_search_query(
        "Как открыть ИП?",
        "Я нахожусь в Москве",
        profile,
    )

    assert "Как открыть ИП?" in query
    assert "Я нахожусь в Москве" in query


def test_build_search_query_ignores_free_only(profile):
    query = build_search_query(
        "Как открыть ИП?",
        None,
        profile,
    )

    assert "free_only" not in query
    assert "бесплат" not in query


def test_build_search_query_strips_whitespace(profile):
    query = build_search_query(
        "  Как открыть ИП?  ",
        "  Я начинающий предприниматель  ",
        profile,
    )

    assert query.startswith("Как открыть ИП?")
    assert "Я начинающий предприниматель" in query


def test_filter_relevant_removes_distant_evidence(evidence):
    result = make_search_result(evidence)

    filtered = filter_relevant(result)

    assert len(filtered) == 1
    assert filtered[0].distance == 0.2


def test_filter_relevant_keeps_evidence_at_threshold():
    item = Evidence(
        chunk_id=uuid4(),
        material_id=uuid4(),
        source=SourceKey(
            source_table="materials",
            source_id="3",
        ),
        title="Порог",
        text="Текст.",
        url=None,
        distance=MAX_DISTANCE,
    )

    result = make_search_result((item,))

    assert filter_relevant(result) == (item,)


def test_filter_relevant_returns_empty_for_distant_evidence():
    item = Evidence(
        chunk_id=uuid4(),
        material_id=uuid4(),
        source=SourceKey(
            source_table="materials",
            source_id="4",
        ),
        title="Нерелевантный материал",
        text="Текст.",
        url=None,
        distance=MAX_DISTANCE + 0.1,
    )

    result = make_search_result((item,))

    assert filter_relevant(result) == ()


def test_build_context_numbers_sources(evidence):
    context = build_context(evidence)

    assert '<source n="1"' in context
    assert '<source n="2"' in context
    assert "Регистрация ИП" in context
    assert "Налоговый режим" in context


def test_build_context_escapes_xml_characters():
    item = Evidence(
        chunk_id=uuid4(),
        material_id=uuid4(),
        source=SourceKey(
            source_table="materials",
            source_id="5",
        ),
        title='Материал <script> "тест"',
        text="Текст с тегом <system> и символами &.",
        url=None,
        distance=0.2,
    )

    context = build_context((item,))

    assert "&lt;script&gt;" in context
    assert "&lt;system&gt;" in context
    assert "&amp;" in context
    assert "&quot;тест&quot;" in context


def test_build_context_empty_evidence():
    assert build_context(()) == ""


def test_max_distance_is_nonnegative():
    assert MAX_DISTANCE >= 0
