from alfa_bot.contracts import SearchResult


def validate_answer(answer: str, sources: SearchResult) -> None:
    """Python 2: номера citation, соответствие утверждений, отсутствие закрытого текста.

    URL прикрепляет renderer из каталога. Существование номера citation
    само по себе не доказывает обоснованность утверждения.
    """
    raise NotImplementedError("Реализовать проверки перед сохранением ответа")
