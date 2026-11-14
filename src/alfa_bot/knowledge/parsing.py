import html
import re
import threading
from functools import lru_cache

import snowballstemmer

_local = threading.local()
STOP_WORDS = set(
    """
а без бы был была были было быть в вам вас ваш ваша ваши во вот все всего вы
где да для до его ее если есть ещё и из или им их к как какая какие какой
ко когда кто ли мне мной могу может можно мой мы на над надо наш не него
нее нет ни но ну о об он она они оно от по под при про с со так такой там
те тем то того тоже той только тот ты у уже что чтобы это этот эту я
пожалуйста расскажи подскажи хочу нужно вопрос сделать узнать помоги меня
""".split()
)


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ").replace("\u202f", " ")
    value = value.replace("\\n", "\n")
    return re.sub(r"[ \t]+", " ", value).strip()


@lru_cache(maxsize=60000)
def _stem(word: str) -> str:
    if not hasattr(_local, "stemmer"):
        _local.stemmer = snowballstemmer.stemmer("russian")
    return _local.stemmer.stemWord(word)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zа-яё0-9]+", text.casefold().replace("ё", "е"))
    return [_stem(word) for word in words if len(word) > 1 and word not in STOP_WORDS]
