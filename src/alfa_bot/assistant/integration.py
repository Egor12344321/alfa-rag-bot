"""Connect the contributed assistant to persistent dialogs and Telegram DTOs.

One TurnRepository is created per request. No mutable per-turn result is shared
between users; PostgreSQL checks the dialog version when committing a reply.
"""

import asyncio
import re
from dataclasses import asdict
from datetime import UTC
from uuid import NAMESPACE_URL, uuid4, uuid5
from weakref import WeakValueDictionary

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from alfa_bot import contracts as ui
from alfa_bot import domain as core
from alfa_bot.assistant.llm import LLMError
from alfa_bot.assistant.repository import ConcurrentTurnError
from alfa_bot.assistant.service import AssistantService
from alfa_bot.assistant.state import AssistantState


def answer(text):
    return ui.Reply(type=ui.ReplyType.ANSWER, answer=ui.Answer(text=text))


def material(
    source,
    *,
    title,
    url,
    material_id=None,
    kind=None,
    summary="",
    access=None,
    price=None,
    currency=None,
    price_note=None,
):
    kinds = {
        "Articles": "article",
        "Documents": "document",
        "Glossary": "glossary",
        "Courses": "course",
        "Webinars": "course",
    }
    kind = {"term": "glossary", "webinar": "course"}.get(kind, kind)
    return ui.Material(
        id=f"{source.source_table}:{source.source_id}",
        source_type=kind or kinds.get(source.source_table, "article"),
        source_id=source.source_id,
        title=title,
        description=summary,
        url=url,
        is_paid=(
            False if access == "free" else True if access in {"paid", "subscription"} else None
        ),
        price=f"{price} {currency or ''}".strip() if price is not None else price_note,
    )


class StructuredLLM:
    def __init__(self, llm):
        self.llm = llm

    async def structured(self, *, system, user, schema):
        try:
            result = await self.llm.generate(
                [SystemMessage(content=system), HumanMessage(content=user)], schema=schema
            )
            return schema.model_validate_json(result)
        except (LLMError, ValueError) as error:
            raise core.DependencyUnavailable("Генератор временно недоступен") from error


class ReminderAdapter:
    def __init__(self, service, knowledge, request):
        self.service = service
        self.knowledge = knowledge
        self.request = request

    async def prepare(self, *, user_id, conversation_id, intent, now):
        missing = tuple(
            name
            for name, value in (
                ("текст", intent.text),
                ("дату и время", intent.local_start),
                ("часовой пояс", intent.timezone),
            )
            if not value
        )
        if missing:
            return core.ReminderDraft(
                uuid4(), 1, intent, missing, "Уточните параметры напоминания.", None
            )
        if intent.interval_n != 1:
            raise core.ValidationError(
                "Поддерживаются повторы каждый день, каждую неделю и каждый месяц. "
                "Выберите один из этих вариантов или разовое напоминание."
            )
        card = await self.knowledge.get_card(intent.material) if intent.material else None
        try:
            draft = await self.service.create_draft(
                user_id=self.request.user_id,
                chat_id=self.request.chat_id,
                intent=ui.ReminderIntent(
                    text=intent.text,
                    local_datetime=intent.local_start,
                    timezone=intent.timezone,
                    frequency=intent.repeat_kind or "once",
                    material_url=card.url if card else None,
                ),
                now=now,
            )
        except ValueError as error:
            raise core.ValidationError(str(error)) from None
        return core.ReminderDraft(
            draft.id,
            1,
            intent,
            (),
            "Подтвердите создание напоминания.",
            draft.starts_at,
            expires_at=draft.expires_at,
            material_url=draft.material_url,
        )


class TurnRepository:
    """Persist a core result as the Reply consumed by the user's Telegram router."""

    def __init__(self, repository, request, dialog, history, settings):
        self.repository = repository
        self.request = request
        self.dialog = dialog
        self.history = history
        self.settings = settings
        self.reply = None

    async def get_handled(self, update_id):
        if update_id == self.dialog.last_update_id and self.dialog.last_reply:
            self.reply = ui.Reply.model_validate(self.dialog.last_reply)
            return core.HandledTurn(uuid4(), (), self.reply.type.value)
        return None

    async def load_state(self, conversation_id):
        profile = self.dialog.profile
        saved = self.dialog.pending.get("core", {})
        values = dict(saved.get("profile", {}))
        values.update(
            industry=profile.industry,
            tax_regime=profile.tax_regime,
            current_goal=profile.goal,
            free_only=profile.preferred_access == "free",
            timezone=profile.timezone or self.settings.default_timezone,
        )
        return AssistantState(
            version=self.dialog.version,
            history=[
                ("Пользователь: " if isinstance(m, HumanMessage) else "Ассистент: ")
                + str(m.content)
                for m in self.history
            ],
            profile=core.Profile(**values),
            pending_question=saved.get("pending_question"),
            pending_clarification=saved.get("pending_clarification"),
            pending_reminder=saved.get("pending_reminder", {}),
            recent_materials=tuple(
                core.SourceKey(**item) for item in saved.get("recent_materials", [])
            ),
        )

    async def save_result(
        self,
        *,
        turn,
        expected_version,
        text,
        outcome,
        run_id=None,
        evidence=(),
        new_state=None,
        cards=(),
        draft=None,
    ):
        if outcome == "clarification":
            reply = ui.Reply(type=ui.ReplyType.CLARIFICATION, text=text)
        elif draft is not None:
            reply = ui.Reply(
                type=ui.ReplyType.REMINDER,
                draft=ui.ReminderDraft(
                    id=draft.draft_id,
                    text=draft.intent.text,
                    starts_at=draft.next_run_at,
                    timezone=draft.intent.timezone,
                    frequency=draft.intent.repeat_kind or "once",
                    expires_at=draft.expires_at,
                    material_url=draft.material_url,
                ),
            )
        else:
            # Core citations number chunks, while the UI numbers source cards.
            # Remap duplicate materials without changing the cited claim.
            sources = []
            numbering = {}
            by_source = {}
            for index, item in enumerate(evidence, start=1):
                key = (item.source.source_table, item.source.source_id)
                if key not in by_source:
                    by_source[key] = len(sources) + 1
                    sources.append(
                        material(item.source, title=item.title, url=item.url, access="free")
                    )
                numbering[index] = by_source[key]
            text = (
                re.sub(r"\[(\d+)\]", lambda m: f"[{numbering.get(int(m[1]), int(m[1]))}]", text)
                if evidence
                else text
            )
            recommendations = [
                ui.Recommendation(
                    material=material(
                        c.source,
                        title=c.title,
                        url=c.url,
                        kind=c.kind,
                        summary=c.public_summary,
                        access=c.access_type,
                        price=c.price,
                        currency=c.currency,
                        price_note=c.price_note,
                    ),
                    reason="; ".join(c.reason_features) or "Материал по теме вашего запроса.",
                    score=c.score if c.score is not None else 0.0,
                )
                for c in cards
            ]
            reply = ui.Reply(
                type=ui.ReplyType.ANSWER,
                answer=ui.Answer(
                    text=text,
                    sources=sources,
                    recommendations=recommendations,
                ),
            )
        if new_state is not None:
            values = asdict(new_state.profile)
            self.dialog.pending = {
                "core": {
                    "profile": values,
                    "pending_question": new_state.pending_question,
                    "pending_clarification": new_state.pending_clarification,
                    "pending_reminder": new_state.pending_reminder,
                    "recent_materials": [asdict(k) for k in new_state.recent_materials],
                }
            }
            self.dialog.profile.industry = new_state.profile.industry
            self.dialog.profile.tax_regime = new_state.profile.tax_regime
            self.dialog.profile.goal = new_state.profile.current_goal
            self.dialog.profile.timezone = new_state.profile.timezone
            self.dialog.profile.preferred_access = "free" if new_state.profile.free_only else "any"
            stage = (new_state.profile.stage or "").casefold()
            stage_map = {"beginner": "beginner", "owner": "owner", "solo_founder": "solo_founder"}
            if stage in stage_map:
                self.dialog.profile.business_stage = stage_map[stage]
        summary = reply.answer.text if reply.answer else reply.text or text
        if reply.answer and reply.answer.recommendations:
            summary += "\nРекомендованные материалы: " + "; ".join(
                item.material.title for item in reply.answer.recommendations
            )
        try:
            await self.repository.commit_turn(
                self.dialog,
                [HumanMessage(content=self.request.text), AIMessage(content=summary)],
                reply=reply,
                update_id=self.request.update_id,
                history_limit=self.settings.history_limit,
            )
        except ConcurrentTurnError as error:
            raise core.Conflict("Диалог уже обновлён") from error
        self.reply = reply
        return core.HandledTurn(uuid4(), (), outcome)


class IntegratedAssistant:
    def __init__(self, repository, knowledge, llm, reminders, settings):
        self.repository = repository
        self.knowledge = knowledge
        self.llm = StructuredLLM(llm)
        self.reminders = reminders
        self.settings = settings
        self._locks = WeakValueDictionary()

    async def reminder_action(self, request):
        try:
            return await self.reminders.action(request)
        except ValueError as error:
            return ui.ActionResult(text=str(error))

    async def handle(self, request):
        if request.user_id != request.chat_id:
            return answer("Напишите боту в личный чат.")
        lock = self._locks.get(request.user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[request.user_id] = lock
        async with lock:
            cached = await self.repository.get_reply(request.user_id, request.update_id)
            if cached is not None:
                return cached
            command = request.text.strip().casefold()
            if command in {"/new", "сбросить диалог"}:
                await self.reminders.cancel_pending(request.user_id, request.chat_id)
                await self.repository.reset(request.user_id)
                return answer("История, профиль и незавершённый запрос очищены.")
            dialog = await self.repository.load(request.user_id)
            if request.update_id is not None and dialog.last_update_id == request.update_id:
                if dialog.last_reply:
                    return ui.Reply.model_validate(dialog.last_reply)
            if command == "/cancel":
                dialog.pending = {
                    "core": {"profile": dialog.pending.get("core", {}).get("profile", {})}
                }
                await self.reminders.cancel_pending(request.user_id, request.chat_id)
                reply = answer("Текущий запрос отменён. Можно задать новый вопрос.")
            elif command == "/profile":
                values = dialog.pending.get("core", {}).get("profile", {})
                p = dialog.profile
                reply = answer(
                    f"Стадия: {values.get('stage') or p.business_stage}\n"
                    f"Сфера: {p.industry or 'не указана'}\n"
                    f"Опыт: {values.get('experience') or 'не указан'}\n"
                    f"Цель: {p.goal or 'не указана'}\n"
                    f"Налоговый режим: {p.tax_regime or 'не указан'}\n"
                    f"Часовой пояс: {p.timezone or self.settings.default_timezone}\n"
                    f"Только бесплатное: {'да' if p.preferred_access == 'free' else 'нет'}"
                )
            elif command in {"/start", "/help"}:
                reply = answer(
                    "Я помогаю с вопросами бизнеса по материалам «Альфа-Курса», "
                    "подбираю статьи и курсы и создаю напоминания после подтверждения.\n\n"
                    "Напишите, например: «Как привлечь клиентов в кофейню?»\n"
                    f"Для времени по умолчанию использую {self.settings.default_timezone}. "
                    "Другой часовой пояс можно указать в сообщении."
                )
            else:
                history = await self.repository.history(
                    request.user_id, self.settings.history_limit
                )
                turn_repo = TurnRepository(self.repository, request, dialog, history, self.settings)
                service = AssistantService(
                    turn_repo,
                    self.knowledge,
                    ReminderAdapter(self.reminders, self.knowledge, request),
                    self.llm,
                    max_distance=1 - self.settings.score_threshold,
                    recommendation_limit=self.settings.recommendation_limit,
                    retrieval_limit=self.settings.top_k,
                )
                turn = core.UserTurn(
                    update_id=request.update_id if request.update_id is not None else -1,
                    user_id=uuid5(NAMESPACE_URL, f"alfa:user:{request.user_id}"),
                    conversation_id=uuid5(NAMESPACE_URL, f"alfa:chat:{request.chat_id}"),
                    text=request.text,
                    received_at=request.now.astimezone(UTC),
                )
                try:
                    await service.handle_turn(turn)
                except core.Conflict:
                    cached = await self.repository.get_reply(request.user_id, request.update_id)
                    if cached is not None:
                        return cached
                    return answer("Диалог уже обновился. Повторите последнее сообщение.")
                if turn_repo.reply is None:
                    raise RuntimeError("Assistant did not produce a Reply")
                return turn_repo.reply
            await self.repository.commit_turn(
                dialog,
                [HumanMessage(content=request.text), AIMessage(content=reply.answer.text)],
                reply=reply,
                update_id=request.update_id,
                history_limit=self.settings.history_limit,
            )
            return reply
