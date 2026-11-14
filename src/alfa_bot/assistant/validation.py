
import re
from decimal import Decimal, InvalidOperation

from alfa_bot.contracts import Evidence, SearchResult


class AnswerRejected(Exception):
    """Ответ не прошёл проверку."""


_CITE = re.compile(r"\[(\d+)\]")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)

_NUMBER = re.compile(
    r"""
    (?P<number>
        \d+(?:[ \u00a0]\d{3})*(?:[,.]\d+)?
    )
    \s*
    (?P<magnitude>
        миллиард(?:а|ов)?
        |млрд\.?
        |миллион(?:а|ов)?
        |млн\.?
        |тысяч(?:а|и)?
        |тыс\.?
    )?
    \s*
    (?P<currency>
        ₽
        |руб(?:ль|ля|лей)?
        |р\.
        |\$
        |€
        |USD
        |EUR
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WORD_MONEY = re.compile(
    r"""
    (?P<number>
        одна|один|одно|одну
        |две|два
        |три|четыре|пять|шесть|семь|восемь|девять|десять
    )
    \s+
    (?P<magnitude>
        миллиард(?:а|ов)?
        |миллион(?:а|ов)?
        |тысяч(?:а|и)?
    )
    \s*
    (?P<currency>
        ₽
        |руб(?:ль|ля|лей)?
        |р\.
        |\$
        |€
        |USD
        |EUR
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WORD_NUMBERS = {
    "одна": Decimal("1"),
    "один": Decimal("1"),
    "одно": Decimal("1"),
    "одну": Decimal("1"),
    "две": Decimal("2"),
    "два": Decimal("2"),
    "три": Decimal("3"),
    "четыре": Decimal("4"),
    "пять": Decimal("5"),
    "шесть": Decimal("6"),
    "семь": Decimal("7"),
    "восемь": Decimal("8"),
    "девять": Decimal("9"),
    "десять": Decimal("10"),
}

_MAGNITUDES = {
    "тыс": Decimal("1000"),
    "тысяча": Decimal("1000"),
    "тысячи": Decimal("1000"),
    "тысяч": Decimal("1000"),
    "млн": Decimal("1000000"),
    "миллион": Decimal("1000000"),
    "миллиона": Decimal("1000000"),
    "миллионов": Decimal("1000000"),
    "млрд": Decimal("1000000000"),
    "миллиард": Decimal("1000000000"),
    "миллиарда": Decimal("1000000000"),
    "миллиардов": Decimal("1000000000"),
}


def norm(value: str) -> str:
    """Нормализует текст для простого сравнения."""
    return re.sub(r"\s+", "", value).casefold()


def _normalize_magnitude(value: str | None) -> Decimal:
    if value is None:
        return Decimal("1")

    key = value.casefold().rstrip(".")
    return _MAGNITUDES.get(key, Decimal("1"))


def _normalize_currency(value: str | None) -> str | None:
    if value is None:
        return None

    key = value.casefold()

    if key in {"₽", "руб", "рубль", "рубля", "рублей", "р"}:
        return "RUB"

    if key in {"$", "usd"}:
        return "USD"

    if key in {"€", "eur"}:
        return "EUR"

    return None


def _extract_money(text: str) -> set[tuple[Decimal, str | None]]:
    """Извлекает суммы и валюты из текста."""
    result: set[tuple[Decimal, str | None]] = set()

    for match in _NUMBER.finditer(text):
        number = match.group("number")
        magnitude = match.group("magnitude")
        currency = match.group("currency")

        if magnitude is None and currency is None:
            continue

        normalized_number = number.replace(" ", "").replace("\u00a0", "")
        normalized_number = normalized_number.replace(",", ".")

        try:
            amount = Decimal(normalized_number)
        except InvalidOperation:
            continue

        amount *= _normalize_magnitude(magnitude)

        result.add(
            (
                amount,
                _normalize_currency(currency),
            )
        )

    for match in _WORD_MONEY.finditer(text):
        number = match.group("number").casefold()
        magnitude = match.group("magnitude")
        currency = match.group("currency")

        amount = (
            _WORD_NUMBERS[number]
            * _normalize_magnitude(magnitude)
        )

        result.add(
            (
                amount,
                _normalize_currency(currency),
            )
        )

    return result


def _money_is_supported(
    amount: Decimal,
    currency: str | None,
    source_money: set[tuple[Decimal, str | None]],
) -> bool:
    for source_amount, source_currency in source_money:
        if amount != source_amount:
            continue

        if currency is None or currency == source_currency:
            return True

    return False


def validate_answer(
    answer: str,
    sources: SearchResult | tuple[Evidence, ...],
) -> None:
    """Проверяет цитаты, URL и цены в ответе."""
    evidence = (
        sources.evidence
        if isinstance(sources, SearchResult)
        else sources
    )

    if _URL.search(answer):
        raise AnswerRejected(
            "URL в тексте: ссылки добавляет renderer"
        )

    cited = {
        int(number)
        for number in _CITE.findall(answer)
    }

    if not cited:
        raise AnswerRejected("нет ссылок на источники")

    bad = [
        number
        for number in cited
        if not 1 <= number <= len(evidence)
    ]

    if bad:
        raise AnswerRejected(
            f"несуществующие источники: {bad}"
        )

    corpus = " ".join(
        evidence[number - 1].text
        for number in cited
    )

    answer_money = _extract_money(answer)
    source_money = _extract_money(corpus)

    for amount, currency in answer_money:
        if not _money_is_supported(
            amount,
            currency,
            source_money,
        ):
            raise AnswerRejected(
                f"цена не найдена в источниках: {amount} {currency or ''}".strip()
            )
