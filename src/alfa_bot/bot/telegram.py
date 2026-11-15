"""Только Telegram: события -> DTO assistant -> представление ответа."""

import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.chat_action import ChatActionSender

from alfa_bot.bot.rendering import (
    FREQUENCIES,
    Part,
    answer_parts,
    draft_parts,
    html_chunks,
    recommendation_parts,
    reminder_parts,
)
from alfa_bot.contracts import (
    ActionResult,
    AssistantPort,
    ReminderAction,
    ReminderDraft,
    ReminderView,
    ReplyType,
    UserRequest,
)

logger = logging.getLogger(__name__)
ASK = "Задать вопрос"
REMINDERS = "Мои напоминания"


class ReminderCallback(CallbackData, prefix="r"):
    action: Literal[
        "show", "confirm", "cancel", "freq", "disable", "resume", "delete", "delete_yes"
    ]
    id: UUID


class FrequencyCallback(CallbackData, prefix="f"):
    frequency: Literal["once", "daily", "weekly", "monthly"]
    id: UUID


def _keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ASK)], [KeyboardButton(text=REMINDERS)]],
        resize_keyboard=True,
        input_field_placeholder="Вопрос или просьба о напоминании",
    )


def _button(text: str, action: str, id: UUID) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=ReminderCallback(action=action, id=id).pack()
    )


def draft_keyboard(draft: ReminderDraft):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("Подтвердить", "confirm", draft.id),
                _button("Отмена", "cancel", draft.id),
            ]
        ]
    )


def reminder_keyboard(reminder: ReminderView):
    rows = []
    if reminder.status != "done":
        rows.append([_button("Изменить частоту", "freq", reminder.id)])
        if reminder.status == "active":
            rows.append([_button("Отключить", "disable", reminder.id)])
        else:
            rows.append([_button("Включить", "resume", reminder.id)])
    rows.append([_button("Удалить", "delete", reminder.id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _frequency_keyboard(reminder: ReminderView):
    buttons = [
        InlineKeyboardButton(
            text=("✓ " if key == reminder.frequency else "") + label,
            callback_data=FrequencyCallback(frequency=key, id=reminder.id).pack(),
        )
        for key, label in FREQUENCIES.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:2],
            buttons[2:],
            [_button("Назад", "show", reminder.id)],
        ]
    )


async def _send(message: Message, parts: list[Part], markup=None):
    chunks = html_chunks(parts)
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=markup if index == len(chunks) - 1 else None,
        )


async def _edit(message: Message, parts: list[Part], markup=None):
    chunks = html_chunks(parts)
    if len(chunks) != 1:
        await _send(message, parts, markup)
        return
    try:
        await message.edit_text(
            chunks[0],
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=markup,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in error.message.lower():
            await _send(message, parts, markup)


async def _result(message: Message, result: ActionResult, *, edit: bool = False):
    send = _edit if edit else _send
    if result.reminder:
        await send(
            message,
            reminder_parts(result.reminder, result.text),
            reminder_keyboard(result.reminder),
        )
    else:
        if result.text:
            await send(message, [Part(result.text)])
        for reminder in result.items:
            await _send(message, reminder_parts(reminder), reminder_keyboard(reminder))


def build_router(assistant: AssistantPort) -> Router:
    router = Router(name="bot")
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")

    async def action(request: ReminderAction) -> ActionResult:
        try:
            return await assistant.reminder_action(request)
        except ValueError as error:
            return ActionResult(text=str(error))
        except Exception as error:  # noqa: BLE001 — граница фоновой задачи/транспорта
            logger.error("reminder_action_failed error_type=%s", type(error).__name__)
            return ActionResult(text="Не получилось обновить напоминание. Попробуйте позже.")

    async def handle_request(message: Message):
        if message.from_user is None or message.text is None:
            return
        if not message.text.strip():
            await _send(message, [Part("Напишите вопрос или просьбу о напоминании.")])
            return
        if len(message.text) > 8000:
            await _send(message, [Part("Сократите сообщение до 8000 символов.")])
            return
        try:
            async with ChatActionSender.typing(chat_id=message.chat.id, bot=message.bot):
                reply = await assistant.handle(
                    UserRequest(
                        user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        text=message.text,
                        now=datetime.now(UTC),
                        update_id=message.message_id,
                    )
                )
        except Exception as error:  # noqa: BLE001 — граница фоновой задачи/транспорта
            logger.error("assistant_request_failed error_type=%s", type(error).__name__)
            await _send(
                message,
                [Part("Сейчас не получилось обработать сообщение. Попробуйте позже.")],
            )
            return
        if reply.type == ReplyType.ANSWER:
            await _send(message, answer_parts(reply.answer), _keyboard())
            for recommendation in reply.answer.recommendations:
                await _send(message, recommendation_parts(recommendation))
        elif reply.type == ReplyType.REMINDER:
            await _send(message, draft_parts(reply.draft), draft_keyboard(reply.draft))
        else:
            text = reply.text or (
                "Я помогаю с вопросами о бизнесе и обучении на «Альфа-Курсе», "
                "подбираю материалы и создаю напоминания."
            )
            await _send(message, [Part(text)], _keyboard())

    @router.message(Command("help"))
    async def help_message(message: Message):
        await _send(
            message,
            [
                Part(
                    "Я отвечаю по материалам «Альфа-Курса» и учитываю вашу ситуацию.\n\n"
                    "/start — начать работу\n/new — очистить диалог и профиль\n"
                    "/profile — посмотреть сохранённые предпочтения\n"
                    "/reminders — мои напоминания\n/cancel — отменить незавершённый запрос\n\n"
                    "Можно написать: «Объясни кратко», «Дай подробный план», "
                    "«Я новичок, у меня кофейня на УСН».\n"
                    "Напоминания сохраняются после подтверждения."
                )
            ],
            _keyboard(),
        )

    @router.message(Command("reminders"))
    @router.message(F.text == REMINDERS)
    async def list_reminders(message: Message):
        if message.from_user is not None:
            result = await action(
                ReminderAction(
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    action="list",
                    now=datetime.now(UTC),
                )
            )
            await _result(message, result)

    @router.message(F.text == ASK)
    async def ask(message: Message):
        await _send(message, [Part("Напишите ваш вопрос.")])

    @router.message(Command("start", "new", "cancel", "profile"))
    async def command(message: Message):
        await handle_request(message)

    @router.callback_query(ReminderCallback.filter())
    async def reminder_callback(callback: CallbackQuery, callback_data: ReminderCallback):
        if (
            not isinstance(callback.message, Message)
            or callback.message.chat.id != callback.from_user.id
        ):
            await callback.answer("Сообщение недоступно.", show_alert=True)
            return
        await callback.answer()
        code = callback_data.action
        operation = {"cancel": "cancel_draft", "delete_yes": "delete"}.get(code, code)
        if code in {"freq", "delete"}:
            operation = "show"
        result = await action(
            ReminderAction(
                user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                action=operation,
                reminder_id=callback_data.id,
                now=datetime.now(UTC),
            )
        )
        if result.reminder and code == "freq":
            await _edit(
                callback.message,
                reminder_parts(result.reminder, "Выберите частоту."),
                _frequency_keyboard(result.reminder),
            )
        elif result.reminder and code == "delete":
            await _edit(
                callback.message,
                reminder_parts(result.reminder, "Удалить это напоминание?"),
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            _button("Да, удалить", "delete_yes", callback_data.id),
                            _button("Отмена", "show", callback_data.id),
                        ]
                    ]
                ),
            )
        else:
            await _result(callback.message, result, edit=True)

    @router.callback_query(FrequencyCallback.filter())
    async def frequency_callback(callback: CallbackQuery, callback_data: FrequencyCallback):
        if (
            not isinstance(callback.message, Message)
            or callback.message.chat.id != callback.from_user.id
        ):
            await callback.answer("Сообщение недоступно.", show_alert=True)
            return
        await callback.answer()
        result = await action(
            ReminderAction(
                user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
                action="frequency",
                reminder_id=callback_data.id,
                frequency=callback_data.frequency,
                now=datetime.now(UTC),
            )
        )
        await _result(callback.message, result, edit=True)

    @router.callback_query()
    async def invalid_callback(callback: CallbackQuery):
        await callback.answer(
            "Кнопка устарела. Откройте /reminders или создайте новый запрос.",
            show_alert=True,
        )

    @router.message(F.text.startswith("/"))
    async def unknown_command(message: Message):
        await _send(message, [Part("Неизвестная команда. Список команд: /help.")])

    @router.message(F.text)
    async def text_message(message: Message):
        await handle_request(message)

    @router.message()
    async def non_text(message: Message):
        await _send(message, [Part("Пожалуйста, отправьте запрос текстом.")])

    return router
