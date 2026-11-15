"""Проверки Telegram HTML без сети и реального токена."""

import ast
from html.parser import HTMLParser
from pathlib import Path

import pytest

from alfa_bot.bot.rendering import (
    Part,
    answer_parts,
    html_chunks,
    recommendation_parts,
    utf16_length,
)
from alfa_bot.contracts import Answer, Material, Recommendation


class ParsedHTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.visible = []
        self.links = []
        self.depth = 0
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        assert tag == "a"
        assert self.depth == 0
        self.depth += 1
        self.links.append(dict(attrs)["href"])

    def handle_endtag(self, tag):
        assert tag == "a" and self.depth == 1
        self.depth -= 1

    def handle_data(self, data):
        self.visible.append(data)


def material(**changes):
    data = {
        "id": "article:a",
        "source_type": "article",
        "source_id": "a",
        "title": "Статья",
        "url": "https://example.org/article?a=1&b=2",
        "is_paid": False,
    }
    return Material(**(data | changes))


def test_citations_are_inline_links_and_untrusted_text_is_escaped():
    answer = Answer(
        text='Начните с аудитории [1]. <script>alert("x")</script> [99]',
        sources=[material()],
    )
    chunks = html_chunks(answer_parts(answer))
    assert len(chunks) == 1
    parsed = ParsedHTML(chunks[0])
    assert parsed.depth == 0
    assert parsed.links == [answer.sources[0].url]
    assert "".join(parsed.visible) == answer.text
    assert "<script>" not in chunks[0]
    assert "[99]" in chunks[0]


def test_recommendation_is_a_separate_card_and_preserves_catalog_access():
    answer = Answer(
        text="Ответ [1].",
        sources=[material()],
        recommendations=[
            Recommendation(
                material=material(
                    id="course:c",
                    source_id="c",
                    source_type="course",
                    title="Продажи <с нуля>",
                    url="https://example.org/course",
                    is_paid=True,
                    price="1000 ₽",
                ),
                reason="Подходит для вашей задачи продаж.",
                score=0.82,
            )
        ],
    )
    chunks = html_chunks(answer_parts(answer))
    assert len(chunks) == 1
    parsed = ParsedHTML(chunks[0])
    assert len(parsed.links) == 1
    assert "Продажи" not in chunks[0]
    card = html_chunks(recommendation_parts(answer.recommendations[0]))[0]
    assert "Платно · 1000 ₽" in "".join(ParsedHTML(card).visible)
    assert "Продажи &lt;с нуля&gt;" in card


def test_unknown_access_is_not_rendered_as_free():
    answer = Answer(
        text="Материалы",
        recommendations=[
            Recommendation(
                material=material(is_paid=None),
                reason="Описание",
                score=0.8,
            )
        ],
    )
    html = "".join(html_chunks(recommendation_parts(answer.recommendations[0])))
    assert "Условия доступа не указаны" in html
    assert "Бесплатно" not in html


@pytest.mark.parametrize("limit", [2, 9, 100, 3900])
def test_html_splitting_preserves_text_emoji_entities_and_balanced_links(limit):
    parts = [
        Part("😀 & <начало> " * 700),
        Part("Очень длинное название ссылки 😀 " * 300, "https://example.org/a?x=1&y=2"),
        Part("\nКонец ответа."),
    ]
    chunks = html_chunks(parts, limit=limit)
    restored = []
    for chunk in chunks:
        parsed = ParsedHTML(chunk)
        visible = "".join(parsed.visible)
        assert parsed.depth == 0
        assert 0 < utf16_length(visible) <= limit
        assert all(url == "https://example.org/a?x=1&y=2" for url in parsed.links)
        restored.append(visible)
    assert "".join(restored) == "".join(part.text for part in parts)


def test_transport_does_not_import_storage_rag_or_llm():
    folder = Path(__file__).resolve().parents[1] / "src/alfa_bot/bot"
    for file in folder.glob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(
                    ("sqlalchemy", "langchain", "alfa_bot.kb", "alfa_bot.assistant")
                )
            elif isinstance(node, ast.Import):
                assert all(
                    not name.name.startswith(("sqlalchemy", "langchain")) for name in node.names
                )
