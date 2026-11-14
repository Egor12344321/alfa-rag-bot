
from uuid import uuid4

import pytest

from alfa_bot.assistant.validation import AnswerRejected, validate_answer
from alfa_bot.contracts import Evidence, SourceKey


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
            title="Налоговый материал",
            text="Стоимость услуги составляет 5000 рублей.",
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
            title="Регистрация ИП",
            text="Для регистрации ИП нужно подать заявление.",
            url=None,
            distance=0.3,
        ),
    )


def test_answer_without_citation_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Для регистрации нужно заявление.",
            evidence,
        )


def test_invalid_citation_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Нужно подать заявление [9].",
            evidence,
        )


def test_url_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Подробности: https://example.com [2].",
            evidence,
        )


def test_unknown_price_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Стоимость составляет 10000 рублей [1].",
            evidence,
        )


def test_price_from_source_is_accepted(evidence):
    validate_answer(
        "Стоимость услуги составляет 5000 рублей [1].",
        evidence,
    )


def test_answer_with_valid_citation_is_accepted(evidence):
    validate_answer(
        "Для регистрации ИП нужно подать заявление [2].",
        evidence,
    )


def test_thousand_without_currency_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Стоимость составляет 7 тыс. [1].",
            evidence,
        )


def test_thousand_without_currency_is_accepted(evidence):
    validate_answer(
        "Стоимость составляет 5 тыс. [1].",
        evidence,
    )


def test_word_price_is_accepted(evidence):
    validate_answer(
        "Стоимость составляет пять тысяч рублей [1].",
        evidence,
    )


def test_grouped_number_is_accepted(evidence):
    validate_answer(
        "Стоимость составляет 5 000 рублей [1].",
        evidence,
    )


def test_price_without_currency_is_checked(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Стоимость составляет 10 тыс. [1].",
            evidence,
        )


def test_price_from_uncited_source_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Стоимость составляет 5000 рублей [2].",
            evidence,
        )


def test_wrong_currency_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Стоимость составляет 5000 USD [1].",
            evidence,
        )


def test_multiple_valid_citations_are_accepted(evidence):
    validate_answer(
        "Стоимость услуги составляет 5000 рублей [1]. "
        "Для регистрации ИП нужно подать заявление [2].",
        evidence,
    )


def test_zero_citation_is_rejected(evidence):
    with pytest.raises(AnswerRejected):
        validate_answer(
            "Нужно подать заявление [0].",
            evidence,
        )
