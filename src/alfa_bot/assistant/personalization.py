import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alfa_bot.assistant.schemas import AccessFilter, AnswerStyle, Decision, MaterialFilter
from alfa_bot.knowledge.models import SearchHit

_INTRODUCTORY = re.compile(
    r"\b(?:для\s+(?:новичк\w*|начинающ\w*)|с\s+нуля|основы|вводн\w*\s+курс)\b"
)
_ADVANCED = re.compile(
    r"\b(?:для\s+(?:опытн\w*|продвинут\w*)|продвинут\w*\s+(?:курс|уровень)|углубл[её]нн\w*)\b"
)
_TURN_FIELDS = {
    "response_style", "requested_kind", "requested_access",
    "learning_goal", "omit_recommendations", "omit_courses",
}


def update_profile(profile: dict, decision: Decision) -> bool:
    patch = decision.profile.model_dump(exclude_none=True)
    if timezone := patch.get("timezone"):
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            patch.pop("timezone")
    confirmed = {key: value for key, value in patch.items() if value}
    profile.update(confirmed)
    return bool(confirmed)


def pending_preferences(decision: Decision) -> dict:
    return decision.model_dump(include=_TURN_FIELDS, exclude_none=True)


def continue_preferences(decision: Decision, saved: dict) -> Decision:
    preferences = {**saved, **pending_preferences(decision)}
    return decision.model_copy(update=preferences)


def answer_style(profile: dict, decision: Decision) -> AnswerStyle:
    if decision.response_style:
        return decision.response_style
    preference = profile.get("response_style")
    if preference in ("balanced", "brief", "detailed", "checklist", "template"):
        return preference
    if decision.learning_goal:
        return "detailed"
    if profile.get("experience_level") == "experienced":
        return "brief"
    if profile.get("experience_level") == "beginner" or profile.get("business_stage") == "planning":
        return "checklist"
    return "balanced"


def recommendations_requested(decision: Decision) -> bool:
    return (
        decision.intent == "recommendation"
        or decision.requested_kind is not None
        or decision.requested_access is not None
    )


def recommendation_kind(
    profile: dict, decision: Decision, style: AnswerStyle,
) -> MaterialFilter | None:
    if decision.omit_recommendations:
        return None
    if recommendations_requested(decision):
        requested = decision.requested_kind or "any"
        return None if requested == "course" and decision.omit_courses else requested
    if style == "brief":
        return None
    if (
        profile.get("course_preference") != "avoid"
        and not decision.omit_courses
        and (decision.learning_goal or profile.get("course_preference") == "welcome")
    ):
        return "course"
    return "article"


def recommendation_access(profile: dict, decision: Decision) -> AccessFilter:
    if decision.requested_access:
        return decision.requested_access
    preferred = profile.get("preferred_access")
    return preferred if preferred in ("free", "paid") else "any"


def select_recommendations(
    candidates: list[SearchHit],
    profile: dict,
    decision: Decision,
    *,
    limit: int,
) -> list[SearchHit]:
    experience = profile.get("experience_level")
    if not experience and profile.get("business_stage") == "planning":
        experience = "beginner"
    allows_courses = not decision.omit_courses and (
        decision.requested_kind == "course" or profile.get("course_preference") != "avoid"
    )
    ordered = []
    for hit in candidates:
        material = hit.material
        if material.card() is None:
            continue
        audience = None
        if material.kind == "course":
            if not allows_courses:
                continue
            text = f"{material.title} {material.description}".casefold()
            intro = bool(_INTRODUCTORY.search(text))
            advanced = bool(_ADVANCED.search(text))
            if intro != advanced:
                audience = "beginner" if intro else "experienced"
        if experience and audience and experience != audience:
            continue
        # Уровень влияет только на уже отобранные по теме материалы.
        ordered.append((bool(experience and audience == experience), hit))
    ordered.sort(key=lambda item: item[0], reverse=True)
    return [hit for _, hit in ordered[:limit]]
