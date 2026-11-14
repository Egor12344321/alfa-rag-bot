import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from alfa_bot.adapters.telegram import _send_answer
from alfa_bot.contracts import Answer, MaterialAccess, MaterialCard, MaterialKind


def _source():
    return MaterialCard("Статья", "https://example.org/source", MaterialKind.ARTICLE)


def test_telegram_links_follow_the_paragraph_and_keep_literal_markup():
    course = MaterialCard(
        "Курс <для новичков>",
        "https://example.org/course",
        MaterialKind.COURSE,
        MaterialAccess.PAID,
        "Практика по найму",
        "1 000 ₽",
    )
    answer = Answer("🚀 <шаг> [1]", sources=[_source()], recommendations=[course])
    message = SimpleNamespace(answer=AsyncMock())
    asyncio.run(_send_answer(message, answer))
    message.answer.assert_awaited_once()
    call = message.answer.await_args
    assert "<шаг> [1]" in call.args[0]
    assert "Практика по найму" in call.args[0]
    assert "1 000 ₽" in call.args[0]
    assert call.kwargs["parse_mode"] is None
    citation, recommendation = call.kwargs["entities"]
    assert (citation.type, citation.offset, citation.length, citation.url) == (
        "text_link", 9, 3, "https://example.org/source",
    )
    encoded = call.args[0].encode("utf-16-le")
    start, end = recommendation.offset, recommendation.offset + recommendation.length
    assert encoded[start * 2:end * 2].decode("utf-16-le") == "Курс <для новичков>"
    assert recommendation.url == "https://example.org/course"


def test_telegram_never_splits_citation_entity_at_message_limit():
    answer = Answer("а" * 3999 + "[1] конец", sources=[_source()])
    message = SimpleNamespace(answer=AsyncMock())
    asyncio.run(_send_answer(message, answer))
    first, second = message.answer.await_args_list
    assert len(first.args[0].encode("utf-16-le")) <= 8000
    assert second.args[0].startswith("[1]")
    link, = second.kwargs["entities"]
    assert (link.offset, link.length, link.url) == (0, 3, "https://example.org/source")
