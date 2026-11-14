from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from alfa_bot.assistant.repository import AssistantState
from alfa_bot.assistant.schemas import (
    AnswerDraft,
    IntentResult,
    ProfilePatch,
    ReminderSlots,
)
from alfa_bot.assistant.service import AssistantService
from alfa_bot.contracts import (
    Conflict,
    DependencyUnavailable,
    Evidence,
    HandledTurn,
    Profile,
    SearchResult,
    SourceKey,
    UserTurn,
)


class FakeRepository:
    def __init__(self):
        self.saved = {}
        self.states = {}
        self.saved_texts = {}
        self.save_calls = 0
        self.conflict_next_save = False

    async def get_handled(self, update_id):
        return self.saved.get(update_id)

    async def load_state(self, conversation_id):
        if conversation_id not in self.states:
            self.states[conversation_id] = AssistantState(
                version=0,
                history=[],
                profile=Profile(),
            )

        return self.states[conversation_id]

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
    ):
        self.save_calls += 1

        if self.conflict_next_save:
            self.conflict_next_save = False
            raise Conflict("Version conflict")

        result = HandledTurn(
            message_id=uuid4(),
            delivery_ids=(),
            outcome=outcome,
        )

        self.saved[turn.update_id] = result
        self.saved_texts[turn.update_id] = text

        if new_state is not None:
            self.states[turn.conversation_id] = new_state

        return result


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.users = []

    async def structured(self, *, system, user, schema):
        self.calls += 1
        self.users.append(user)

        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


class FakeKnowledge:
    def __init__(self, result, cards=()):
        self.result = result
        self.cards = tuple(cards)
        self.calls = 0
        self.recommend_calls = 0
        self.recommended_profiles = []

    async def recommend(
        self,
        query,
        profile,
        *,
        run_id,
        exclude=(),
        limit=1,
    ):
        self.recommend_calls += 1
        self.recommended_profiles.append(profile)
        return self.cards[:limit]

    async def get_card(self, source, *, run_id=None):
        return None

    async def retrieve(
        self,
        query,
        *,
        limit=6,
        run_id=None,
        external_context=True,
    ):
        self.calls += 1
        return self.result


class FakeReminders:
    def __init__(self, missing_fields=()):
        self.calls = 0
        self.last_intent = None
        self.missing_fields = missing_fields

    async def prepare(self, *, user_id, conversation_id, intent, now):
        self.calls += 1
        self.last_intent = intent

        return type(
            "FakeReminderDraft",
            (),
            {
                "preview_text": "Напоминание подготовлено.",
                "missing_fields": self.missing_fields,
                "next_run_at": now,
            },
        )()


def make_turn(
    update_id=1,
    *,
    text="Как открыть ИП?",
    user_id=None,
    conversation_id=None,
):
    return UserTurn(
        update_id=update_id,
        user_id=user_id or uuid4(),
        conversation_id=conversation_id or uuid4(),
        text=text,
        received_at=datetime.now(UTC),
    )


def make_evidence():
    return Evidence(
        chunk_id=uuid4(),
        material_id=uuid4(),
        source=SourceKey(
            source_table="materials",
            source_id="1",
        ),
        title="Регистрация ИП",
        text="Для регистрации ИП нужно подать заявление.",
        url=None,
        distance=0.2,
    )


def make_card(
    *,
    title="Бесплатный курс",
    access_type="free",
):
    return SimpleNamespace(
        id=uuid4(),
        run_id=uuid4(),
        source=SourceKey(
            source_table="materials",
            source_id=str(uuid4()),
        ),
        kind="course",
        title=title,
        public_summary="Описание материала.",
        url="https://example.com/course",
        access_type=access_type,
        price=None,
        currency=None,
        price_note=None,
        reason_features=(),
    )


def make_service(
    llm,
    knowledge=None,
    reminders=None,
    repository=None,
):
    return AssistantService(
        repository=repository or FakeRepository(),
        knowledge=knowledge or FakeKnowledge(
            SearchResult(run_id=uuid4(), evidence=()),
        ),
        reminders=reminders or FakeReminders(),
        llm=llm,
    )


@pytest.mark.asyncio
async def test_empty_evidence_does_not_call_answer_llm():
    llm = FakeLLM(
        [
            IntentResult(intent="consult"),
        ]
    )

    knowledge = FakeKnowledge(
        SearchResult(run_id=uuid4(), evidence=()),
    )
    repository = FakeRepository()

    service = make_service(
        llm,
        knowledge=knowledge,
        repository=repository,
    )

    result = await service.handle_turn(make_turn())

    assert result.outcome == "no_answer"
    assert llm.calls == 1
    assert knowledge.calls == 1
    assert repository.save_calls == 1


@pytest.mark.asyncio
async def test_not_enough_information_returns_no_answer():
    llm = FakeLLM(
        [
            IntentResult(intent="consult"),
            AnswerDraft(
                enough_info=False,
                answer="",
                missing=["Неизвестен налоговый режим"],
            ),
        ]
    )

    knowledge = FakeKnowledge(
        SearchResult(
            run_id=uuid4(),
            evidence=(make_evidence(),),
        )
    )
    repository = FakeRepository()

    service = make_service(
        llm,
        knowledge=knowledge,
        repository=repository,
    )

    result = await service.handle_turn(make_turn())

    assert result.outcome == "no_answer"
    assert repository.save_calls == 1


@pytest.mark.asyncio
async def test_invalid_source_citation_is_not_saved_as_answer():
    llm = FakeLLM(
        [
            IntentResult(intent="consult"),
            AnswerDraft(
                enough_info=True,
                answer="Для регистрации нужно заявление [9].",
            ),
        ]
    )

    knowledge = FakeKnowledge(
        SearchResult(
            run_id=uuid4(),
            evidence=(make_evidence(),),
        )
    )
    repository = FakeRepository()

    service = make_service(
        llm,
        knowledge=knowledge,
        repository=repository,
    )

    result = await service.handle_turn(make_turn())

    assert result.outcome == "no_answer"
    assert result.outcome != "answer"


@pytest.mark.asyncio
async def test_duplicate_update_id_returns_saved_result():
    repository = FakeRepository()
    turn = make_turn(update_id=123)

    saved = HandledTurn(
        message_id=uuid4(),
        delivery_ids=(),
        outcome="answer",
    )
    repository.saved[123] = saved

    llm = FakeLLM([])
    knowledge = FakeKnowledge(
        SearchResult(run_id=uuid4(), evidence=()),
    )

    service = make_service(
        llm,
        knowledge=knowledge,
        repository=repository,
    )

    result = await service.handle_turn(turn)

    assert result == saved
    assert llm.calls == 0
    assert knowledge.calls == 0
    assert repository.save_calls == 0


@pytest.mark.asyncio
async def test_dependency_unavailable_returns_error():
    llm = FakeLLM(
        [
            DependencyUnavailable("LLM unavailable"),
        ]
    )
    repository = FakeRepository()

    service = make_service(
        llm,
        repository=repository,
    )

    result = await service.handle_turn(make_turn())

    assert result.outcome == "error"
    assert repository.save_calls == 1


@pytest.mark.asyncio
async def test_reminder_intent_calls_prepare():
    slots = ReminderSlots(
        text="Проверить налоги",
        local_start=None,
        timezone="Europe/Moscow",
        repeat_kind=None,
        interval_n=1,
        material_query=None,
    )

    llm = FakeLLM(
        [
            IntentResult(
                intent="reminder",
                reminder=slots,
            ),
        ]
    )
    reminders = FakeReminders()

    service = make_service(
        llm,
        reminders=reminders,
    )

    result = await service.handle_turn(make_turn())

    assert result.outcome == "reminder"
    assert reminders.calls == 1
    assert reminders.last_intent.text == "Проверить налоги"
    assert reminders.last_intent.timezone == "Europe/Moscow"


@pytest.mark.asyncio
async def test_reminder_with_missing_fields_returns_clarification():
    slots = ReminderSlots(
        text="Проверить налоги",
        local_start=None,
        timezone=None,
        repeat_kind=None,
        interval_n=1,
        material_query=None,
    )

    llm = FakeLLM(
        [
            IntentResult(
                intent="reminder",
                reminder=slots,
            ),
        ]
    )
    reminders = FakeReminders(missing_fields=("timezone",))

    service = make_service(
        llm,
        reminders=reminders,
    )

    result = await service.handle_turn(make_turn())

    assert result.outcome == "clarification"
    assert reminders.calls == 1


@pytest.mark.asyncio
async def test_clarification_saves_pending_question():
    llm = FakeLLM(
        [
            IntentResult(
                intent="consult",
                needs_clarification=True,
                clarification_question="Какой у вас налоговый режим?",
            ),
        ]
    )
    repository = FakeRepository()
    turn = make_turn()

    service = make_service(
        llm,
        repository=repository,
    )

    result = await service.handle_turn(turn)
    state = await repository.load_state(turn.conversation_id)

    assert result.outcome == "clarification"
    assert state.pending_question == turn.text
    assert state.pending_clarification == "Какой у вас налоговый режим?"
    assert state.version == 1


@pytest.mark.asyncio
async def test_clarification_answer_keeps_original_question():
    original_turn = make_turn(
        update_id=1,
        text="Как открыть ИП?",
    )
    repository = FakeRepository()

    llm = FakeLLM(
        [
            IntentResult(
                intent="consult",
                needs_clarification=True,
                clarification_question="Какой у вас налоговый режим?",
            ),
            IntentResult(intent="consult"),
            AnswerDraft(
                enough_info=True,
                answer="Для регистрации ИП нужно подать заявление [1].",
            ),
        ]
    )

    knowledge = FakeKnowledge(
        SearchResult(
            run_id=uuid4(),
            evidence=(make_evidence(),),
        )
    )

    service = make_service(
        llm,
        knowledge=knowledge,
        repository=repository,
    )

    first_result = await service.handle_turn(original_turn)

    second_turn = make_turn(
        update_id=2,
        text="УСН",
        user_id=original_turn.user_id,
        conversation_id=original_turn.conversation_id,
    )

    second_result = await service.handle_turn(second_turn)

    assert first_result.outcome == "clarification"
    assert second_result.outcome == "answer"
    assert knowledge.calls == 1
    assert "Как открыть ИП?" in llm.users[1]
    assert "УСН" in llm.users[1]

    state = await repository.load_state(original_turn.conversation_id)

    assert state.pending_question is None
    assert state.pending_clarification is None


@pytest.mark.asyncio
async def test_conflict_returns_already_saved_result():
    repository = FakeRepository()
    turn = make_turn()

    saved = HandledTurn(
        message_id=uuid4(),
        delivery_ids=(),
        outcome="answer",
    )

    repository.saved[turn.update_id] = saved
    repository.conflict_next_save = True

    llm = FakeLLM(
        [
            IntentResult(intent="out_of_scope"),
        ]
    )

    service = make_service(
        llm,
        repository=repository,
    )

    result = await service.handle_turn(turn)

    assert result == saved


@pytest.mark.asyncio
async def test_conflict_without_saved_result_is_propagated():
    repository = FakeRepository()
    repository.conflict_next_save = True

    llm = FakeLLM(
        [
            IntentResult(intent="out_of_scope"),
        ]
    )

    service = make_service(
        llm,
        repository=repository,
    )

    with pytest.raises(Conflict):
        await service.handle_turn(make_turn())


@pytest.mark.asyncio
async def test_clarification_does_not_call_retrieval():
    llm = FakeLLM(
        [
            IntentResult(
                intent="consult",
                needs_clarification=True,
                clarification_question="Какой у вас налоговый режим?",
            ),
        ]
    )

    knowledge = FakeKnowledge(
        SearchResult(run_id=uuid4(), evidence=()),
    )

    service = make_service(
        llm,
        knowledge=knowledge,
    )

    result = await service.handle_turn(make_turn())

    assert result.outcome == "clarification"
    assert knowledge.calls == 0


@pytest.mark.asyncio
async def test_profile_patch_updates_only_explicit_fields():
    repository = FakeRepository()
    turn = make_turn(text="Я работаю на УСН.")

    repository.states[turn.conversation_id] = AssistantState(
        version=0,
        history=[],
        profile=Profile(
            stage="ИП",
            industry="IT",
            experience="3 года",
            current_goal=None,
            tax_regime=None,
            free_only=False,
        ),
    )

    llm = FakeLLM(
        [
            IntentResult(
                intent="out_of_scope",
                profile_patch=ProfilePatch(
                    tax_regime="УСН",
                    free_only=None,
                ),
            ),
        ]
    )

    service = make_service(
        llm,
        repository=repository,
    )

    await service.handle_turn(turn)

    state = await repository.load_state(turn.conversation_id)

    assert state.profile.stage == "ИП"
    assert state.profile.industry == "IT"
    assert state.profile.experience == "3 года"
    assert state.profile.tax_regime == "УСН"
    assert state.profile.free_only is False


@pytest.mark.asyncio
async def test_profile_patch_can_change_existing_value():
    repository = FakeRepository()
    turn = make_turn(text="Теперь я работаю на ОСНО.")

    repository.states[turn.conversation_id] = AssistantState(
        version=0,
        history=[],
        profile=Profile(
            tax_regime="УСН",
        ),
    )

    llm = FakeLLM(
        [
            IntentResult(
                intent="out_of_scope",
                profile_patch=ProfilePatch(
                    tax_regime="ОСНО",
                ),
            ),
        ]
    )

    service = make_service(
        llm,
        repository=repository,
    )

    await service.handle_turn(turn)

    state = await repository.load_state(turn.conversation_id)

    assert state.profile.tax_regime == "ОСНО"


@pytest.mark.asyncio
async def test_profile_patch_is_saved_during_clarification():
    repository = FakeRepository()
    turn = make_turn(text="Я занимаюсь разработкой сайтов.")

    llm = FakeLLM(
        [
            IntentResult(
                intent="consult",
                needs_clarification=True,
                clarification_question="Какой у вас налоговый режим?",
                profile_patch=ProfilePatch(
                    industry="Разработка сайтов",
                ),
            ),
        ]
    )

    service = make_service(
        llm,
        repository=repository,
    )

    result = await service.handle_turn(turn)
    state = await repository.load_state(turn.conversation_id)

    assert result.outcome == "clarification"
    assert state.profile.industry == "Разработка сайтов"
    assert state.pending_question == turn.text


@pytest.mark.asyncio
async def test_free_only_profile_is_passed_to_recommendations():
    repository = FakeRepository()
    turn = make_turn(text="Подбери мне материалы.")

    free_card = make_card(
        title="Бесплатный курс",
        access_type="free",
    )
    paid_card = make_card(
        title="Платный курс",
        access_type="paid",
    )

    knowledge = FakeKnowledge(
        SearchResult(
            run_id=uuid4(),
            evidence=(make_evidence(),),
        ),
        cards=(paid_card, free_card),
    )

    llm = FakeLLM(
        [
            IntentResult(
                intent="consult",
                profile_patch=ProfilePatch(
                    free_only=True,
                ),
            ),
            AnswerDraft(
                enough_info=True,
                answer="Вот информация из источника [1].",
            ),
        ]
    )

    service = make_service(
        llm,
        knowledge=knowledge,
        repository=repository,
    )

    result = await service.handle_turn(turn)
    state = await repository.load_state(turn.conversation_id)

    assert result.outcome == "answer"
    assert state.profile.free_only is True
    assert knowledge.recommend_calls == 1
    assert knowledge.recommended_profiles[0].free_only is True

    saved_text = repository.saved_texts[turn.update_id]

    assert "Бесплатный курс" in saved_text
    assert "Платный курс" not in saved_text


@pytest.mark.asyncio
async def test_free_only_false_keeps_paid_recommendations():
    repository = FakeRepository()
    turn = make_turn(text="Подбери материалы.")

    paid_card = make_card(
        title="Платный курс",
        access_type="paid",
    )

    knowledge = FakeKnowledge(
        SearchResult(
            run_id=uuid4(),
            evidence=(make_evidence(),),
        ),
        cards=(paid_card,),
    )

    llm = FakeLLM(
        [
            IntentResult(
                intent="consult",
                profile_patch=ProfilePatch(
                    free_only=False,
                ),
            ),
            AnswerDraft(
                enough_info=True,
                answer="Вот информация из источника [1].",
            ),
        ]
    )

    service = make_service(
        llm,
        knowledge=knowledge,
        repository=repository,
    )

    await service.handle_turn(turn)

    saved_text = repository.saved_texts[turn.update_id]

    assert "Платный курс" in saved_text
