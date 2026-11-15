import re
from dataclasses import replace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from alfa_bot.assistant.context import (
    build_context,
    build_search_query,
    filter_relevant,
)
from alfa_bot.assistant.core_prompts import (
    ANSWER_SYSTEM,
    INTENT_SYSTEM,
    build_intent_user,
)
from alfa_bot.assistant.core_schemas import AnswerDraft, IntentResult, ReminderSlots
from alfa_bot.assistant.core_validation import AnswerRejected, validate_answer
from alfa_bot.assistant.state import (
    AssistantRepository,
    AssistantState,
)
from alfa_bot.domain import (
    Conflict,
    DependencyUnavailable,
    Evidence,
    HandledTurn,
    Knowledge,
    MaterialCard,
    ReminderIntent,
    Reminders,
    UserTurn,
    ValidationError,
)

StructuredLLM = Any

NO_ANSWER = (
    "В моих материалах нет надёжной информации по этому вопросу. Попробуйте переформулировать."
)
ERROR = "Сейчас не получилось ответить. Попробуйте ещё раз чуть позже."
OUT_OF_SCOPE = "Я помогаю с вопросами предпринимателей и напоминаниями."
SMALLTALK = (
    "Привет! Я помогу с вопросами по предпринимательству "
    "и созданием напоминаний. Что хотите узнать?"
)
INVALID_REMINDER = "Не удалось определить корректное время напоминания. Уточните дату и время."

DEPENDENCY_ERRORS = (
    DependencyUnavailable,
    TimeoutError,
    ConnectionError,
)


class AssistantService:
    def __init__(
        self,
        repository: AssistantRepository,
        knowledge: Knowledge,
        reminders: Reminders,
        llm: StructuredLLM,
        *,
        max_distance: float = 0.55,
        recommendation_limit: int = 1,
        retrieval_limit: int = 6,
    ):
        self.repository = repository
        self.knowledge = knowledge
        self.reminders = reminders
        self.llm = llm
        self.max_distance = max_distance
        self.recommendation_limit = recommendation_limit
        self.retrieval_limit = retrieval_limit

    async def handle_turn(self, turn: UserTurn) -> HandledTurn:
        saved = await self.repository.get_handled(turn.update_id)
        if saved is not None:
            return saved

        state = await self.repository.load_state(turn.conversation_id)

        try:
            return await self._handle_loaded_turn(turn, state)
        except DEPENDENCY_ERRORS:
            try:
                return await self._save(
                    turn,
                    state,
                    ERROR,
                    "error",
                )
            except DEPENDENCY_ERRORS:
                raise

    async def _handle_loaded_turn(
        self,
        turn: UserTurn,
        state: AssistantState,
    ) -> HandledTurn:
        was_clarification = state.pending_clarification is not None
        original_question = state.pending_question

        if was_clarification and original_question:
            intent_text = (
                f"Исходный вопрос: {original_question}\nУточнение пользователя: {turn.text}"
            )
        else:
            intent_text = turn.text

        intent = await self.llm.structured(
            system=INTENT_SYSTEM,
            user=build_intent_user(
                f"Профиль: {state.profile}\n{intent_text}",
                turn.received_at.astimezone(
                    ZoneInfo(state.profile.timezone or "Europe/Moscow")
                ).isoformat(),
                state.history,
            ),
            schema=IntentResult,
        )

        state = self._apply_profile_patch(state, intent)

        if intent.intent == "recommend":
            return await self._recommend(turn, state, intent)

        if intent.intent == "reminder":
            return await self._reminder(turn, state, intent)

        if intent.intent == "smalltalk":
            new_state = self._updated_state(
                state,
                turn,
                SMALLTALK,
            )
            return await self._save(
                turn,
                state,
                SMALLTALK,
                "answer",
                new_state=new_state,
            )

        if intent.intent != "consult":
            new_state = self._updated_state(
                state,
                turn,
                OUT_OF_SCOPE,
            )
            return await self._save(
                turn,
                state,
                OUT_OF_SCOPE,
                "no_answer",
                new_state=new_state,
            )

        if intent.needs_clarification and intent.clarification_question and not was_clarification:
            question = original_question or turn.text

            new_state = self._updated_state(
                state,
                turn,
                intent.clarification_question,
                pending_question=question,
                pending_clarification=intent.clarification_question,
            )

            return await self._save(
                turn,
                state,
                intent.clarification_question,
                "clarification",
                new_state=new_state,
            )

        return await self._consult(
            turn,
            state,
            original_question=original_question or intent.search_query,
            clarification_answer=turn.text if was_clarification else None,
        )

    @staticmethod
    def _apply_profile_patch(
        state: AssistantState,
        intent: IntentResult,
    ) -> AssistantState:
        patch = intent.profile_patch

        if patch is None:
            return state

        updates = patch.model_dump(exclude_none=True)

        if not updates:
            return state

        profile = replace(state.profile, **updates)

        return replace(state, profile=profile)

    async def _consult(
        self,
        turn: UserTurn,
        state: AssistantState,
        *,
        original_question: str | None = None,
        clarification_answer: str | None = None,
    ) -> HandledTurn:
        question = original_question or turn.text

        query = build_search_query(
            question,
            clarification_answer,
            state.profile,
        )

        found = await self.knowledge.retrieve(
            query,
            limit=self.retrieval_limit,
            external_context=True,
        )
        evidence = filter_relevant(found, self.max_distance)

        if not evidence:
            return await self._save_no_answer(
                turn,
                state,
                NO_ANSWER,
                found.run_id,
            )

        draft = await self.llm.structured(
            system=ANSWER_SYSTEM,
            user=(
                f"{build_context(evidence)}\n\n"
                f"Профиль пользователя: {state.profile}\n"
                f"История: {state.history[-6:]}\n"
                f"Исходный вопрос: {question}\n"
                f"Уточнение пользователя: {clarification_answer or 'нет'}"
            ),
            schema=AnswerDraft,
        )

        if not draft.enough_info:
            text = NO_ANSWER
            if draft.missing:
                text += "\nНе хватает: " + "; ".join(draft.missing)

            return await self._save_no_answer(
                turn,
                state,
                text,
                found.run_id,
            )

        try:
            validate_answer(draft.answer, evidence)
        except AnswerRejected:
            return await self._save_no_answer(
                turn,
                state,
                NO_ANSWER,
                found.run_id,
            )

        answer = draft.answer

        cards = await self.knowledge.recommend(
            query,
            state.profile,
            run_id=found.run_id,
            exclude=tuple(item.source for item in evidence),
            limit=6 if state.profile.free_only else max(1, self.recommendation_limit),
        )

        if state.profile.free_only:
            cards = tuple(card for card in cards if card.access_type == "free")

        cards = cards[: self.recommendation_limit]

        new_state = self._updated_state(
            state,
            turn,
            answer,
        )

        new_state = replace(
            new_state,
            recent_materials=tuple(
                dict.fromkeys([item.source for item in evidence] + [card.source for card in cards])
            ),
        )

        return await self._save(
            turn,
            state,
            answer,
            "answer",
            run_id=found.run_id,
            evidence=evidence,
            new_state=new_state,
            cards=cards,
        )

    async def _save_no_answer(
        self,
        turn: UserTurn,
        state: AssistantState,
        text: str,
        run_id: UUID,
    ) -> HandledTurn:
        new_state = self._updated_state(
            state,
            turn,
            text,
        )

        return await self._save(
            turn,
            state,
            text,
            "no_answer",
            run_id=run_id,
            new_state=new_state,
        )

    async def _recommend(self, turn, state, intent):
        query = build_search_query(intent.search_query or turn.text, None, state.profile)
        found = await self.knowledge.retrieve(query, limit=20, external_context=True)
        profile = state.profile
        if intent.requested_access == "paid":
            profile = replace(profile, free_only=False)
        candidates = list(
            await self.knowledge.recommend(
                query,
                profile,
                run_id=found.run_id,
                limit=20,
            )
        )
        if intent.requested_kind != "course":
            for item in filter_relevant(found, self.max_distance):
                card = await self.knowledge.get_card(item.source, run_id=found.run_id)
                if card is not None:
                    candidates.append(replace(card, score=1 - item.distance))
        cards = tuple(
            c
            for c in candidates
            if (
                (not profile.free_only or c.access_type == "free")
                and (intent.requested_access != "free" or c.access_type == "free")
                and (intent.requested_access != "paid" or c.access_type in {"paid", "subscription"})
                and (intent.requested_kind == "any" or c.kind == intent.requested_kind)
            )
        )[: max(1, self.recommendation_limit)]
        text = (
            "Подобрал материалы по вашему запросу."
            if cards
            else "В загруженном каталоге нет подходящих материалов с такими условиями."
        )
        new_state = self._updated_state(state, turn, text)
        if cards:
            new_state = replace(new_state, recent_materials=tuple(c.source for c in cards))
        return await self._save(
            turn, state, text, "answer", run_id=found.run_id, new_state=new_state, cards=cards
        )

    async def _reminder(
        self,
        turn: UserTurn,
        state: AssistantState,
        intent: IntentResult,
    ) -> HandledTurn:
        values = dict(state.pending_reminder)
        if intent.reminder is not None:
            values.update(
                intent.reminder.model_dump(mode="json", exclude_none=True, exclude_unset=True)
            )
        reminder = ReminderSlots.model_validate(values) if values else None
        state = replace(state, pending_reminder=values)
        original_question = state.pending_question or turn.text

        if reminder is None:
            text = "Не удалось определить параметры напоминания. Уточните, что и когда напомнить."
            return await self._save_reminder_clarification(
                turn,
                state,
                original_question,
                text,
            )

        local_start = reminder.local_start

        if local_start is not None and local_start.tzinfo is not None:
            return await self._save_reminder_clarification(
                turn,
                state,
                original_question,
                INVALID_REMINDER,
            )

        material = None

        reference = (reminder.material_query or "").casefold()
        refers_to_recent = bool(
            re.search(r"\b(этот|эта|это|эту|этого|данный|данную|последний)\b", reference)
        )
        if refers_to_recent and state.recent_materials:
            # The last recommended card has priority over another search.
            for key in reversed(state.recent_materials):
                if "стать" in reference and key.source_table != "Articles":
                    continue
                if "курс" in reference and key.source_table != "Courses":
                    continue
                card = await self.knowledge.get_card(key)
                if card is not None:
                    material = card.source
                    break

        if reminder.material_query and material is None:
            found = await self.knowledge.retrieve(
                reminder.material_query,
                limit=3,
                external_context=True,
            )
            evidence = filter_relevant(found, self.max_distance)

            for item in evidence:
                card = await self.knowledge.get_card(
                    item.source,
                    run_id=found.run_id,
                )
                if card is not None:
                    material = card.source
                    break
            if material is None:
                cards = await self.knowledge.recommend(
                    reminder.material_query,
                    state.profile,
                    run_id=found.run_id,
                    limit=1,
                )
                if cards:
                    material = cards[0].source

        reminder_intent = ReminderIntent(
            text=reminder.text,
            local_start=local_start,
            timezone=reminder.timezone or state.profile.timezone,
            repeat_kind=reminder.repeat_kind or "once",
            interval_n=reminder.interval_n,
            material=material,
            date_origin="user",
            date_source_note=None,
        )

        try:
            draft = await self.reminders.prepare(
                user_id=turn.user_id,
                conversation_id=turn.conversation_id,
                intent=reminder_intent,
                now=turn.received_at,
            )
        except ValidationError as error:
            return await self._save_reminder_clarification(
                turn,
                state,
                original_question,
                str(error),
            )

        if draft.missing_fields:
            fields = ", ".join(draft.missing_fields)
            text = f"{draft.preview_text}\nДля создания напоминания нужно уточнить: {fields}."
            return await self._save_reminder_clarification(
                turn,
                state,
                original_question,
                text,
            )

        new_state = self._updated_state(
            state,
            turn,
            draft.preview_text,
        )

        return await self._save(
            turn,
            state,
            draft.preview_text,
            "reminder",
            new_state=new_state,
            draft=draft,
        )

    async def _save_reminder_clarification(
        self,
        turn: UserTurn,
        state: AssistantState,
        original_question: str,
        text: str,
    ) -> HandledTurn:
        new_state = self._updated_state(
            state,
            turn,
            text,
            pending_question=original_question,
            pending_clarification=text,
            pending_reminder=state.pending_reminder,
        )

        return await self._save(
            turn,
            state,
            text,
            "clarification",
            new_state=new_state,
        )

    def _updated_state(
        self,
        state: AssistantState,
        turn: UserTurn,
        response: str,
        *,
        pending_question: str | None = None,
        pending_clarification: str | None = None,
        pending_reminder: dict | None = None,
    ) -> AssistantState:
        history = [
            *state.history,
            f"Пользователь: {turn.text}",
            f"Ассистент: {response}",
        ]

        return AssistantState(
            version=state.version + 1,
            recent_materials=state.recent_materials,
            history=history[-12:],
            profile=state.profile,
            pending_question=pending_question,
            pending_clarification=pending_clarification,
            pending_reminder=pending_reminder or {},
        )

    async def _save(
        self,
        turn: UserTurn,
        state: AssistantState,
        text: str,
        outcome: str,
        *,
        run_id: UUID | None = None,
        evidence: tuple[Evidence, ...] = (),
        new_state: AssistantState | None = None,
        cards: tuple[MaterialCard, ...] = (),
        draft=None,
    ) -> HandledTurn:
        try:
            return await self.repository.save_result(
                turn=turn,
                expected_version=state.version,
                text=text,
                outcome=outcome,
                run_id=run_id,
                evidence=evidence,
                new_state=new_state,
                **({"cards": cards} if cards else {}),
                **({"draft": draft} if draft is not None else {}),
            )
        except Conflict:
            saved = await self.repository.get_handled(turn.update_id)

            if saved is not None:
                return saved

            raise
