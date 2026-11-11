from dataclasses import dataclass


@dataclass(frozen=True)
class AccessDecision:
    answer_allowed: bool = False
    recommend_allowed: bool = False
    external_allowed: bool = False
    reason: str = "unverified_source_rules"


def decide_access(source_metadata: dict) -> AccessDecision:
    """Python 1 + продакт: заменить после проверки DDL и справочников.

    До подтверждения правил источник не допущен. Cost=0 или NULL сами по себе
    не доказывают бесплатность. Проверять правила также в retrieval.
    """
    return AccessDecision()
