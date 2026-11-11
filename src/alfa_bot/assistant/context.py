from alfa_bot.contracts import Profile


def build_search_query(question: str, clarification: str | None, profile: Profile) -> str:
    """Python 2: исходный вопрос + уточнение + нужные обезличенные сведения."""
    raise NotImplementedError("Собрать самостоятельный поисковый запрос")
