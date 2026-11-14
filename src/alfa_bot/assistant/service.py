from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from alfa_bot.assistant.personalization import (
    answer_style,
    continue_preferences,
    pending_preferences,
    recommendation_access,
    recommendation_kind,
    recommendations_requested,
    select_recommendations,
    update_profile,
)
from alfa_bot.assistant.prompts import ANSWER_SYSTEM, ROUTER_SYSTEM
from alfa_bot.assistant.repository import AssistantRepository, ConcurrentTurnError, Dialog
from alfa_bot.assistant.schemas import AnswerStyle, Decision, GroundedAnswer, ReminderFields
from alfa_bot.assistant.validation import (
    relative_minutes,
    resolve_relative_minutes,
    validate_reminder,
)
from alfa_bot.contracts import Answer, Reply, ReplyType
from alfa_bot.infra.llm import LLM, LLMError
from alfa_bot.knowledge.models import KnowledgeUnavailable

if TYPE_CHECKING:
    from alfa_bot.contracts import MaterialCard
    from alfa_bot.knowledge.models import SearchHit
    from alfa_bot.knowledge.service import KnowledgePort

logger = logging.getLogger(__name__)
NO_KNOWLEDGE = (
    "В базе знаний нет точного ответа. "
    "Попробуйте уточнить вопрос или описать вашу ситуацию."
)


def _answer(text: str) -> Reply:
    return Reply(type=ReplyType.ANSWER, answer=Answer(text=text))


class AssistantService:
    """Реализация AssistantPort. Не создаёт и не отправляет напоминания."""

    def __init__(self, repository: AssistantRepository, knowledge: KnowledgePort, llm: LLM):
        self.repository = repository
        self.knowledge = knowledge
        self.llm = llm
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    async def handle(self, *, telegram_user_id: int, text: str, now: datetime) -> Reply:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now должен содержать часовой пояс")
        text = text.strip()
        if not text:
            return Reply(
                type=ReplyType.CLARIFICATION,
                text="Напишите вопрос или задачу напоминания.",
            )
        if len(text) > 3000:
            return _answer("Сообщение слишком длинное. Сократите его до 3000 символов.")
        lock = self._locks.get(telegram_user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[telegram_user_id] = lock
        async with lock:
            dialog = await self.repository.load(telegram_user_id)
            try:
                if text.casefold() in {"/new", "сбросить диалог", "начать новый диалог"}:
                    dialog.history = []
                    dialog.pending = {}
                    dialog.profile = {}
                    dialog.recent_materials = []
                    await self.repository.save(dialog)
                    return _answer(
                        "Начали новый диалог. История и профиль очищены. Задайте вопрос.",
                    )
                if text == "/cancel":
                    dialog.pending = {}
                    reply = _answer("Текущий запрос отменён. Можно задать новый вопрос.")
                    self._remember(dialog, text, reply)
                    await self.repository.save(dialog)
                    return reply
                decision = await self.llm.complete(
                    system=ROUTER_SYSTEM,
                    payload={
                        "text": text,
                        "now_utc": now.astimezone(UTC).isoformat(),
                        "profile": dialog.profile,
                        "history": [
                            {"role": item["role"], "text": item["text"][:600]}
                            for item in dialog.history[-8:]
                        ],
                        "pending": dialog.pending,
                        "recent_materials": [
                            {"id": item["id"], "title": item["title"][:180]}
                            for item in dialog.recent_materials
                        ],
                    },
                    schema=Decision,
                )
                updated = update_profile(dialog.profile, decision)
                logger.info("Assistant routed message intent=%s", decision.intent)
                if decision.intent == "profile":
                    reply = _answer(
                        "Учту это в следующих ответах. Какой у вас вопрос?"
                        if updated
                        else "Какое предпочтение или информацию о бизнесе учесть?"
                    )
                else:
                    reply = await self._dispatch(dialog, decision, text=text, now=now)
                self._remember(dialog, text, reply)
                await self.repository.save(dialog)
                return reply
            except ConcurrentTurnError:
                logger.info("Assistant dialog version conflict")
                return _answer("Диалог обновился в другом обработчике. Повторите сообщение.")
            except KnowledgeUnavailable:
                logger.warning("Assistant knowledge search unavailable")
                return _answer("Поиск по базе знаний сейчас недоступен. Попробуйте позже.")
            except LLMError:
                # Не сохраняем неудачный turn, чтобы не потерять ожидаемое уточнение.
                logger.warning("Assistant LLM request failed")
                return _answer(
                    "Сейчас не получилось подготовить ответ. Попробуйте ещё раз чуть позже.",
                )

    async def _dispatch(
        self, dialog: Dialog, decision: Decision, *, text: str, now: datetime,
    ) -> Reply:
        if decision.intent == "out_of_scope":
            return Reply(type=ReplyType.OUT_OF_SCOPE)
        if decision.intent == "reminder":
            return await self._reminder(dialog, decision, text=text, now=now)
        previous = dialog.pending if decision.continue_pending else {}
        query = decision.query or text[:1200]
        question = query
        if previous.get("kind") == "knowledge":
            # Ответ на уточнение может содержать только одно слово.
            question = f"{previous['query']}\nУточнение: {text[:600]}"
            query = decision.query or question[:1200]
            decision = continue_preferences(decision, previous.get("preferences", {}))
        elif decision.clarification:
            dialog.pending = {
                "kind": "knowledge",
                "query": query,
                "preferences": pending_preferences(decision),
            }
            return Reply(type=ReplyType.CLARIFICATION, text=decision.clarification)
        dialog.pending = {}
        style = answer_style(dialog.profile, decision)
        if decision.intent == "recommendation":
            hits = await self._recommend(dialog, decision, query, style=style)
            cards = self._cards(hits)
            self._set_recent(dialog, hits)
            if not cards:
                label = "курсов" if decision.requested_kind == "course" else "материалов"
                return _answer(f"В загруженном каталоге нет подходящих {label} с такими условиями.")
            return Reply(
                type=ReplyType.ANSWER,
                answer=Answer(text="Подборка по вашему запросу.", recommendations=cards),
            )
        hits = (await self.knowledge.retrieve(query))[:5]
        if not hits:
            return await self._no_evidence(dialog, decision, query, style)
        generated = await self.llm.complete(
            system=ANSWER_SYSTEM,
            payload={
                "question": question,
                "user_message": text[:600],
                "profile": dialog.profile,
                "response_style": style,
                "evidence": [
                    {
                        "chunk_id": hit.document.id,
                        "title": hit.material.title,
                        "text": hit.document.text[:2200],
                    }
                    for hit in hits
                ],
            },
            schema=GroundedAnswer,
        )
        if not generated.answerable:
            return await self._no_evidence(dialog, decision, query, style)
        by_id = {hit.document.id: hit for hit in hits}
        used: dict[str, SearchHit] = {}
        for paragraph in generated.paragraphs:
            if any(key not in by_id for key in paragraph.chunk_ids):
                raise LLMError("LLM cited unknown chunk")
            for chunk_id in paragraph.chunk_ids:
                hit = by_id[chunk_id]
                if hit.material.id not in used:
                    used[hit.material.id] = hit
        sources = []
        numbers = {}
        for hit in used.values():
            card = hit.material.card()
            if card:
                sources.append(card)
                numbers[hit.material.id] = len(sources)
        paragraphs = []
        for paragraph in generated.paragraphs:
            markers = []
            for chunk_id in paragraph.chunk_ids:
                mat = by_id[chunk_id].material
                marker = f"[{numbers[mat.id]}]" if mat.id in numbers else f"(источник: {mat.title})"
                if marker not in markers:
                    markers.append(marker)
            paragraphs.append(paragraph.text + " " + " ".join(markers))
        try:
            recommended = await self._recommend(
                dialog, decision, query, style=style, exclude=set(used),
            )
        except KnowledgeUnavailable:
            logger.warning("Assistant catalog lookup unavailable; returning grounded answer")
            recommended = []
        self._set_recent(dialog, [*used.values(), *recommended])
        return Reply(
            type=ReplyType.ANSWER,
            answer=Answer(
                text="\n\n".join(paragraphs),
                sources=sources,
                recommendations=self._cards(recommended),
            ),
        )

    async def _recommend(
        self,
        dialog: Dialog,
        decision: Decision,
        query: str,
        *,
        style: AnswerStyle,
        exclude: set[str] | None = None,
    ) -> list[SearchHit]:
        kind = recommendation_kind(dialog.profile, decision, style)
        if kind is None:
            return []
        hits = await self.knowledge.recommend(
            query,
            exclude=exclude,
            kind=kind,
            access=recommendation_access(dialog.profile, decision),
            limit=6,
        )
        return select_recommendations(hits, dialog.profile, decision, limit=2)

    async def _no_evidence(
        self, dialog: Dialog, decision: Decision, query: str, style: AnswerStyle,
    ) -> Reply:
        recommendations = []
        if recommendations_requested(decision) or decision.learning_goal:
            recommendations = await self._recommend(dialog, decision, query, style=style)
            self._set_recent(dialog, recommendations)
        logger.info("Assistant found insufficient evidence")
        return Reply(
            type=ReplyType.ANSWER,
            answer=Answer(text=NO_KNOWLEDGE, recommendations=self._cards(recommendations)),
        )

    async def _reminder(
        self, dialog: Dialog, decision: Decision, *, text: str, now: datetime,
    ) -> Reply:
        previous = dialog.pending if decision.continue_pending else {}
        previous = previous if previous.get("kind") == "reminder" else {}
        fields = dict(previous.get("reminder", {}))
        fields.update(decision.reminder.model_dump(mode="json", exclude_none=True))
        if not fields.get("timezone") and dialog.profile.get("timezone"):
            fields["timezone"] = dialog.profile["timezone"]
        minutes = relative_minutes(text)
        if minutes is None and decision.reminder.local_datetime is None:
            minutes = previous.get("relative_minutes")
        if minutes:
            # Интервал отсчитывается от сообщения, в котором собраны все данные.
            fields["local_datetime"] = resolve_relative_minutes(
                minutes, fields.get("timezone"), now=now,
            )
        reminder = ReminderFields.model_validate(fields)
        # Даже валидный ID LLM принимаем только среди показанных пользователю материалов.
        shown_ids = {item["id"] for item in dialog.recent_materials}
        card = (
            await self.knowledge.get_card(reminder.material_id)
            if reminder.material_id is not None and reminder.material_id in shown_ids
            else None
        )
        intent, question = validate_reminder(
            reminder,
            now=now,
            material_url=card.url if card else None,
        )
        if question:
            dialog.pending = {
                "kind": "reminder",
                "reminder": reminder.model_dump(mode="json", exclude_none=True),
                "relative_minutes": minutes,
            }
            return Reply(type=ReplyType.CLARIFICATION, text=question)
        dialog.pending = {}
        return Reply(type=ReplyType.REMINDER, reminder=intent)

    @staticmethod
    def _cards(hits: list[SearchHit]) -> list[MaterialCard]:
        return [card for hit in hits if (card := hit.material.card()) is not None]

    @staticmethod
    def _set_recent(dialog: Dialog, hits: list[SearchHit]) -> None:
        shown = {
            hit.material.id: {"id": hit.material.id, "title": hit.material.title}
            for hit in hits
            if hit.material.url
        }
        for item in dialog.recent_materials:
            shown.setdefault(item["id"], item)
        dialog.recent_materials = list(shown.values())[:8]

    @staticmethod
    def _remember(dialog: Dialog, text: str, reply: Reply) -> None:
        if reply.answer:
            materials = " / ".join(card.title[:160] for card in reply.answer.recommendations)
            response = reply.answer.text
            if materials:
                response += f"\nПодборка: {materials}"
        elif reply.reminder:
            reminder = reply.reminder
            response = (
                f"Черновик для подтверждения: {reminder.text}; "
                f"{reminder.local_datetime.isoformat()} {reminder.timezone}; {reminder.frequency}."
            )
        else:
            response = reply.text or "Вопрос вне тематики бизнес-помощника."
        dialog.history = [
            *dialog.history,
            {"role": "user", "text": text[:600]},
            {
                "role": "assistant",
                "text": response[:600],
                "type": reply.type.value,
            },
        ][-8:]
