import asyncio
import logging
from datetime import UTC, datetime
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alfa_bot.assistant.prompts import ANSWER_SYSTEM, ROUTER_SYSTEM
from alfa_bot.assistant.repository import AssistantRepository, ConcurrentTurnError, Dialog
from alfa_bot.assistant.schemas import Decision, GroundedAnswer, ReminderFields
from alfa_bot.assistant.validation import validate_reminder
from alfa_bot.contracts import Answer, Reply, ReplyType
from alfa_bot.infra.llm import LLM, LLMError
from alfa_bot.knowledge.service import KnowledgeService

logger = logging.getLogger(__name__)
NO_KNOWLEDGE = (
    "В доступной базе знаний не нашлось достаточно информации для ответа. "
    "Попробуйте уточнить вопрос или описать вашу ситуацию."
)


def _answer(text: str) -> Reply:
    return Reply(type=ReplyType.ANSWER, answer=Answer(text=text))


class AssistantService:
    """Реализация AssistantPort. Не создаёт и не отправляет напоминания."""

    def __init__(self, repository: AssistantRepository, knowledge: KnowledgeService, llm: LLM):
        self.repository = repository
        self.knowledge = knowledge
        self.llm = llm
        self._locks = WeakValueDictionary()

    async def handle(self, *, telegram_user_id: int, text: str, now: datetime) -> Reply:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now должен содержать часовой пояс")
        text = text.strip()
        if not text:
            return Reply(
                type=ReplyType.CLARIFICATION,
                text="Напишите вопрос или задачу напоминания.",
            )
        if len(text) > 8000:
            return _answer("Сообщение слишком длинное. Сократите его до 8000 символов.")
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
                        "history": dialog.history[-12:],
                        "pending": dialog.pending,
                        "recent_materials": dialog.recent_materials,
                    },
                    schema=Decision,
                )
                self._update_profile(dialog, decision)
                reply = await self._dispatch(dialog, decision, text=text, now=now)
                self._remember(dialog, text, reply)
                await self.repository.save(dialog)
                return reply
            except ConcurrentTurnError:
                return _answer("Диалог обновился в другом обработчике. Повторите сообщение.")
            except LLMError:
                # Не сохраняем неудачный turn, чтобы не потерять ожидаемое уточнение.
                logger.warning("Assistant LLM request failed")
                return _answer(
                    "Сейчас не получилось подготовить ответ. Попробуйте ещё раз чуть позже.",
                )

    @staticmethod
    def _update_profile(dialog: Dialog, decision: Decision):
        patch = decision.profile.model_dump(exclude_none=True)
        if "timezone" in patch:
            try:
                ZoneInfo(patch["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                patch.pop("timezone")
        dialog.profile.update({key: value for key, value in patch.items() if value != ""})

    async def _dispatch(self, dialog: Dialog, decision: Decision, *, text: str, now: datetime):
        if decision.intent == "out_of_scope":
            return Reply(type=ReplyType.OUT_OF_SCOPE)
        if decision.intent == "reminder":
            return self._reminder(dialog, decision, now=now)
        previous = dialog.pending if decision.continue_pending else {}
        query = decision.query.strip() or text
        if previous.get("kind") == "knowledge":
            # Исходный вопрос сохраняется даже при кратком ответе «уже открылись».
            query = f"{previous['query']}\nУточнение: {text}\n{query}"
        if decision.clarification and previous.get("kind") != "knowledge":
            dialog.pending = {"kind": "knowledge", "query": query}
            return Reply(type=ReplyType.CLARIFICATION, text=decision.clarification)
        dialog.pending = {}
        profile_context = " ".join(
            str(dialog.profile.get(key, ""))
            for key in (
                "business_type",
                "stage",
                "goal",
            )
        )
        search_query = query + " " + profile_context
        access = decision.requested_access
        if access == "any":
            access = dialog.profile.get("preferred_access", "any")
        if decision.intent == "recommendation":
            hits = await self.knowledge.recommend(
                search_query,
                kind=decision.requested_kind,
                access=access,
            )
            cards = [hit.material.card() for hit in hits]
            self._set_recent(dialog, hits)
            if not cards:
                label = "курсов" if decision.requested_kind == "course" else "материалов"
                return _answer(f"В загруженном каталоге нет подходящих {label} с такими условиями.")
            return Reply(
                type=ReplyType.ANSWER,
                answer=Answer(
                    text="Подобрал материалы по вашему запросу. Карточки — ниже.",
                    recommendations=cards,
                ),
            )
        hits = await self.knowledge.retrieve(search_query)
        if not hits:
            return _answer(NO_KNOWLEDGE)
        generated = await self.llm.complete(
            system=ANSWER_SYSTEM,
            payload={
                "question": query,
                "profile": dialog.profile,
                "evidence": [
                    {
                        "chunk_id": hit.document.id,
                        "title": hit.material.title,
                        "text": hit.document.text[:4000],
                    }
                    for hit in hits
                ],
            },
            schema=GroundedAnswer,
        )
        if not generated.answerable:
            return _answer(NO_KNOWLEDGE)
        by_id = {hit.document.id: hit for hit in hits}
        used = {}
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
        recommended = await self.knowledge.recommend(search_query, exclude=set(used), access=access)
        self._set_recent(dialog, [*used.values(), *recommended])
        return Reply(
            type=ReplyType.ANSWER,
            answer=Answer(
                text="\n\n".join(paragraphs),
                sources=sources,
                recommendations=[hit.material.card() for hit in recommended],
            ),
        )

    def _reminder(self, dialog: Dialog, decision: Decision, *, now: datetime) -> Reply:
        previous = dialog.pending if decision.continue_pending else {}
        fields = dict(previous.get("reminder", {})) if previous.get("kind") == "reminder" else {}
        fields.update(decision.reminder.model_dump(mode="json", exclude_none=True))
        if not fields.get("timezone") and dialog.profile.get("timezone"):
            fields["timezone"] = dialog.profile["timezone"]
        reminder = ReminderFields.model_validate(fields)
        # Даже валидный ID LLM принимаем только среди показанных пользователю материалов.
        shown_ids = {item["id"] for item in dialog.recent_materials}
        card = (
            self.knowledge.get_card(reminder.material_id)
            if (reminder.material_id in shown_ids)
            else None
        )
        intent, question = validate_reminder(
            reminder,
            now=now,
            material_url=card.url if card else None,
        )
        if question:
            dialog.pending = {"kind": "reminder", "reminder": fields}
            return Reply(type=ReplyType.CLARIFICATION, text=question)
        dialog.pending = {}
        return Reply(type=ReplyType.REMINDER, reminder=intent)

    @staticmethod
    def _set_recent(dialog: Dialog, hits):
        unique = {hit.material.id: hit.material for hit in hits}
        dialog.recent_materials = [
            {"id": mat.id, "title": mat.title, "url": mat.url} for mat in unique.values()
        ][:8]

    @staticmethod
    def _remember(dialog: Dialog, text: str, reply: Reply):
        if reply.answer:
            response = reply.answer.text
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
            {"role": "user", "text": text[:4000]},
            {
                "role": "assistant",
                "text": response[:4000],
                "type": reply.type.value,
            },
        ][-12:]
