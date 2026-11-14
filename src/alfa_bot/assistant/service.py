
from dataclasses import replace
from datetime import UTC
from uuid import UUID

from alfa_bot.assistant.context import (
    build_context,
    build_search_query,
    filter_relevant,
)
from alfa_bot.assistant.prompts import (
    ANSWER_SYSTEM,
    INTENT_SYSTEM,
    build_intent_user,
)
from alfa_bot.assistant.repository import (
    AssistantRepository,
    AssistantState,
)
from alfa_bot.assistant.schemas import AnswerDraft, IntentResult
from alfa_bot.assistant.validation import AnswerRejected, validate_answer
from alfa_bot.contracts import (
    Conflict,
    DependencyUnavailable,
    Evidence,
    HandledTurn,
    Knowledge,
    MaterialCard,
    ReminderIntent,
    Reminders,
    UserTurn,
)
from alfa_bot.infra.llm import StructuredLLM

NO_ANSWER = (
    "В моих материалах нет надёжной информации по этому вопросу. "
    "Попробуйте переформулировать."
)
ERROR = "Сейчас не получилось ответить. Попробуйте ещё раз чуть позже."
OUT_OF_SCOPE = "Я помогаю с вопросами предпринимателей и напоминаниями."
SMALLTALK = (
    "Привет! Я помогу с вопросами по предпринимательству "
    "и созданием напоминаний. Что хотите узнать?"
)
INVALID_REMINDER = (
    "Не удалось определить корректное время напоминания. "
    "Уточните дату и время."
)

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
    ):
        self.repository = repository
        self.knowledge = knowledge
        self.reminders = reminders
        self.llm = llm

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
                f"Исходный вопрос: {original_question}\n"
                f"Уточнение пользователя: {turn.text}"
            )
        else:
            intent_text = turn.text

        intent = await self.llm.structured(
            system=INTENT_SYSTEM,
            user=build_intent_user(
                intent_text,
                turn.received_at.astimezone(UTC).isoformat(),
                state.history,
            ),
            schema=IntentResult,
        )

        state = self._apply_profile_patch(state, intent)

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

        if (
            intent.needs_clarification
            and intent.clarification_question
            and not was_clarification
        ):
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
            original_question=original_question,
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
            limit=6,
            external_context=True,
        )
        evidence = filter_relevant(found)

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
            limit=6 if state.profile.free_only else 1,
        )

        if state.profile.free_only:
            cards = tuple(
                card
                for card in cards
                if card.access_type == "free"
            )

        if cards:
            answer += self._format_recommendation(cards[0])

        new_state = self._updated_state(
            state,
            turn,
            answer,
        )

        return await self._save(
            turn,
            state,
            answer,
            "answer",
            run_id=found.run_id,
            evidence=evidence,
            new_state=new_state,
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

    @staticmethod
    def _format_recommendation(card: MaterialCard) -> str:
        lines = [
            "\n\n**Что ещё может быть полезно:**",
            card.title,
            card.public_summary,
            f"Доступ: {card.access_type}",
        ]

        if card.price is not None:
            currency = card.currency or ""
            lines.append(f"Цена: {card.price} {currency}".strip())

        if card.price_note:
            lines.append(card.price_note)

        lines.append(f"Материал: {card.url}")

        return "\n".join(lines)

    async def _reminder(
        self,
        turn: UserTurn,
        state: AssistantState,
        intent: IntentResult,
    ) -> HandledTurn:
        reminder = intent.reminder
        original_question = state.pending_question or turn.text

        if reminder is None:
            text = (
                "Не удалось определить параметры напоминания. "
                "Уточните, что и когда напомнить."
            )
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

        if reminder.material_query:
            found = await self.knowledge.retrieve(
                reminder.material_query,
                limit=3,
                external_context=True,
            )
            evidence = filter_relevant(found)

            for item in evidence:
                card = await self.knowledge.get_card(
                    item.source,
                    run_id=found.run_id,
                )
                if card is not None:
                    material = card.source
                    break

        reminder_intent = ReminderIntent(
            text=reminder.text,
            local_start=local_start,
            timezone=reminder.timezone,
            repeat_kind=reminder.repeat_kind,
            interval_n=reminder.interval_n,
            material=material,
            date_origin="user",
            date_source_note=None,
        )

        draft = await self.reminders.prepare(
            user_id=turn.user_id,
            conversation_id=turn.conversation_id,
            intent=reminder_intent,
            now=turn.received_at,
        )

        if draft.missing_fields:
            fields = ", ".join(draft.missing_fields)
            text = (
                f"{draft.preview_text}\n"
                f"Для создания напоминания нужно уточнить: {fields}."
            )
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
    ) -> AssistantState:
        history = [
            *state.history,
            f"Пользователь: {turn.text}",
            f"Ассистент: {response}",
        ]

        return AssistantState(
            version=state.version + 1,
            history=history[-12:],
            profile=state.profile,
            pending_question=pending_question,
            pending_clarification=pending_clarification,
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
            )
        except Conflict:
            saved = await self.repository.get_handled(turn.update_id)

            if saved is not None:
                return saved

            raise
