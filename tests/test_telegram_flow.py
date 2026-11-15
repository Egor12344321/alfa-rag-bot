"""Run real aiogram handlers with a local recording transport."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from alfa_bot.bot.telegram import ReminderCallback, build_router
from alfa_bot.contracts import (
    ActionResult,
    Answer,
    Material,
    Recommendation,
    ReminderView,
    Reply,
    ReplyType,
)


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=len(self.requests),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        return True

    async def stream_content(self, url, **kwargs):
        if False:
            yield b""


class Assistant:
    def __init__(self):
        self.actions = []

    async def handle(self, request):
        return Reply(
            type=ReplyType.ANSWER,
            answer=Answer(
                text="Ответ по базе знаний [1].",
                sources=[
                    Material(
                        id="Articles:a",
                        source_type="article",
                        source_id="a",
                        title="Источник",
                        url="https://example.org/article",
                        is_paid=False,
                    )
                ],
                recommendations=[
                    Recommendation(
                        material=Material(
                            id="Courses:c",
                            source_type="course",
                            source_id="c",
                            title="Курс",
                            is_paid=True,
                            url="https://example.org/course",
                            price="1000 RUB",
                        ),
                        reason="По теме вопроса.",
                        score=0.9,
                    )
                ],
            ),
        )

    async def reminder_action(self, request):
        self.actions.append(request)
        return ActionResult(
            text="Напоминание создано.",
            reminder=ReminderView(
                id=request.reminder_id,
                text="Прочитать материал",
                starts_at=datetime.now(UTC),
                next_run_at=datetime.now(UTC) + timedelta(days=1),
                timezone="Europe/Moscow",
                frequency="daily",
                status="active",
            ),
        )


def message(text, user=1):
    return Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=user, type="private"),
        from_user=User(id=user, is_bot=False, first_name="User"),
        text=text,
    )


@pytest.mark.asyncio
async def test_answer_and_material_are_sent_as_separate_telegram_messages():
    session, assistant = RecordingSession(), Assistant()
    bot = Bot(token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk", session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(assistant))
    await dispatcher.feed_update(
        bot, Update(update_id=1, message=message("Как привлечь клиентов?"))
    )
    sent = [m for m in session.requests if isinstance(m, SendMessage)]
    assert len(sent) == 2
    assert "Ответ по базе знаний" in sent[0].text and "Курс" not in sent[0].text
    assert 'href="https://example.org/article"' in sent[0].text
    assert "Курс" in sent[1].text and "Платно" in sent[1].text
    assert "1000 RUB" in sent[1].text
    await bot.session.close()


@pytest.mark.asyncio
async def test_confirmation_callback_reaches_reminders_and_updates_keyboard():
    session, assistant = RecordingSession(), Assistant()
    bot = Bot(token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk", session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(assistant))
    id = uuid4()
    callback = CallbackQuery(
        id="q1",
        from_user=User(id=1, is_bot=False, first_name="User"),
        chat_instance="test",
        message=message("Черновик"),
        data=ReminderCallback(action="confirm", id=id).pack(),
    )
    await dispatcher.feed_update(bot, Update(update_id=2, callback_query=callback))
    assert assistant.actions[0].action == "confirm" and assistant.actions[0].reminder_id == id
    assert assistant.actions[0].user_id == 1
    edited = next(m for m in session.requests if isinstance(m, EditMessageText))
    assert "создано" in edited.text
    assert any("Отключить" in b.text for row in edited.reply_markup.inline_keyboard for b in row)
    await bot.session.close()
