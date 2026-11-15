"""Real PostgreSQL repository tests (TEST_DATABASE_URL required)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from alfa_bot.assistant.repository import AssistantRepository, ConcurrentTurnError
from alfa_bot.contracts import Answer, Reply, ReplyType


@pytest.mark.asyncio
async def test_pg_optimistic_conflict_rolls_back_profile_and_message_insert(db):
    repo = AssistantRepository(db.sessions)
    first, stale = await repo.load(1), await repo.load(1)
    first.profile.industry = "кофейня"
    reply = Reply(type=ReplyType.ANSWER, answer=Answer(text="Ответ"))
    await repo.commit_turn(
        first,
        [HumanMessage(content="Вопрос"), AIMessage(content="Ответ")],
        reply=reply,
        update_id=1,
        history_limit=10,
    )
    stale.profile.industry = "магазин"
    with pytest.raises(ConcurrentTurnError):
        await repo.commit_turn(
            stale,
            [HumanMessage(content="Устаревший вопрос")],
            reply=reply,
            update_id=2,
            history_limit=10,
        )
    saved = await repo.load(1)
    assert saved.profile.industry == "кофейня"
    assert len(await repo.history(1, 10)) == 2


@pytest.mark.asyncio
async def test_history_pruning_cache_and_reset_are_user_scoped(db):
    repo = AssistantRepository(db.sessions)
    for update in range(4):
        dialog = await repo.load(1)
        await repo.commit_turn(
            dialog,
            [HumanMessage(content=f"question {update}"), AIMessage(content="answer")],
            reply=Reply(type=ReplyType.ANSWER, answer=Answer(text=f"answer {update}")),
            update_id=update,
            history_limit=4,
        )
    assert len(await repo.history(1, 20)) == 4
    assert (await repo.get_reply(1, 0)).answer.text == "answer 0"
    assert await repo.get_reply(2, 0) is None
    await repo.reset(1)
    assert not await repo.history(1, 20)
    assert (await repo.load(1)).profile.industry is None
