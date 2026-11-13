import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)
from sqlalchemy.exc import SQLAlchemyError

from alfa_bot.contracts import AssistantPort, ReplyType
from alfa_bot.reminders.service import (
    ReminderDraft, ReminderError, ReminderService, ReminderView,
)


logger = logging.getLogger(__name__)
router = Router(name="telegram")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

ASK_BUTTON = "Задать вопрос"
REMINDERS_BUTTON = "Мои напоминания"
FREQUENCY_NAMES = {
    "once": "один раз", "daily": "ежедневно",
    "weekly": "еженедельно", "monthly": "ежемесячно",
}
STATUS_NAMES = {"active": "включено", "paused": "отключено", "done": "выполнено"}


class ReminderState(StatesGroup):
    waiting_for_confirmation = State()


class DraftCallback(CallbackData, prefix="draft"):
    action: Literal["confirm", "cancel"]
    draft_id: UUID


def _now_utc() -> datetime:
    return datetime.now(UTC)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ASK_BUTTON)],
            [KeyboardButton(text=REMINDERS_BUTTON)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Вопрос или просьба о напоминании",
    )


def _draft_keyboard(draft: ReminderDraft) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(
            text="Подтвердить",
            callback_data=DraftCallback(action="confirm", draft_id=draft.id).pack(),
        ),
        InlineKeyboardButton(
            text="Отмена",
            callback_data=DraftCallback(action="cancel", draft_id=draft.id).pack(),
        ),
    ]]
    if draft.material_url:
        rows.append([InlineKeyboardButton(text="Открыть материал", url=draft.material_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _details(*, text: str, run_at: datetime, timezone: str, frequency: str) -> str:
    try:
        local = run_at.astimezone(ZoneInfo(timezone))
        zone_label = f"{timezone} ({local:%z})"
    except (ZoneInfoNotFoundError, ValueError):
        local = run_at.astimezone(UTC)
        zone_label = f"{timezone} — время показано в UTC"
    return (
        f"Задача: {text}\n"
        f"Дата: {local:%d.%m.%Y}\n"
        f"Время: {local:%H:%M:%S}\n"
        f"Часовой пояс: {zone_label}\n"
        f"Повтор: {FREQUENCY_NAMES.get(frequency, frequency)}"
    )


def _draft_text(draft: ReminderDraft) -> str:
    return "Проверьте напоминание:\n\n" + _details(
        text=draft.text, run_at=draft.starts_at,
        timezone=draft.timezone, frequency=draft.frequency,
    ) + "\n\nВсё верно?"


def _saved_text(reminder: ReminderView) -> str:
    return "Напоминание сохранено.\n\n" + _details(
        text=reminder.text, run_at=reminder.next_run_at,
        timezone=reminder.timezone, frequency=reminder.frequency,
    )


async def _remove_keyboard(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None,
            request_timeout=5,
        )
    except TelegramAPIError:
        # Старую кнопку всё равно отклонит проверка UUID и состояния.
        logger.info("Could not remove an old draft keyboard")


async def _edit_or_send(message: Message, text: str) -> None:
    try:
        await message.edit_text(text, reply_markup=None, parse_mode=None)
    except TelegramBadRequest:
        await message.answer(text, parse_mode=None)


async def _finish(
    state: FSMContext, *, draft_id: UUID, message_id: int, action: str
) -> None:
    # Оставляем одну квитанцию для повторных нажатий; сам черновик удаляем.
    await state.set_data({
        "finished_draft_id": str(draft_id),
        "finished_message_id": message_id,
        "finished_action": action,
    })
    await state.set_state(None)


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    await _remove_keyboard(bot, message.chat.id, data.get("draft_message_id"))
    await message.answer(
        "Привет! Я помощник «Альфа-Курса».\n\n"
        "Напишите сообщение. Перед созданием напоминания я покажу "
        "дату, время и задачу для подтверждения.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(
        "Команды:\n"
        "/start — главное меню\n"
        "/help — помощь\n"
        "/reminders — мои напоминания\n"
        "/cancel — отменить текущий черновик\n\n"
        "Сейчас работает демонстрационный режим: любой обычный текст предлагает "
        "разовое напоминание о продолжении обучения через две минуты.\n"
        "Запись создаётся только после подтверждения."
    )


@router.message(Command("cancel"))
async def handle_cancel(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    draft = data.get("draft")
    if not isinstance(draft, ReminderDraft):
        await message.answer("Нет черновика, который можно отменить.")
        return
    await _finish(
        state, draft_id=draft.id,
        message_id=data["draft_message_id"], action="cancelled",
    )
    await _remove_keyboard(bot, message.chat.id, data["draft_message_id"])
    await message.answer("Создание напоминания отменено.")


@router.message(F.text == ASK_BUTTON)
async def handle_ask_button(message: Message) -> None:
    await message.answer("Напишите ваше сообщение.")


@router.message(Command("reminders"))
@router.message(F.text == REMINDERS_BUTTON)
async def handle_reminders(message: Message, reminders: ReminderService) -> None:
    if message.from_user is None:
        return
    try:
        items = await reminders.list_for_user(telegram_user_id=message.from_user.id)
    except (SQLAlchemyError, OSError, TimeoutError):
        logger.exception("Cannot list reminders")
        await message.answer("Сейчас не получается получить список. Попробуйте позже.")
        return
    if not items:
        await message.answer("У вас пока нет напоминаний.")
        return
    title = "Ваши напоминания:"
    if len(items) > 10:
        title += f" показаны последние 10 из {len(items)}."
    await message.answer(title, reply_markup=main_keyboard())
    for item in items[:10]:
        text = _details(
            text=item.text, run_at=item.next_run_at,
            timezone=item.timezone, frequency=item.frequency,
        )
        text += f"\nСтатус: {STATUS_NAMES.get(item.status, item.status)}"
        await message.answer(text, parse_mode=None)


@router.callback_query(DraftCallback.filter())
async def handle_draft_action(
    callback: CallbackQuery,
    callback_data: DraftCallback,
    state: FSMContext,
    reminders: ReminderService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    message = callback.message
    data = await state.get_data()

    if (
        data.get("finished_draft_id") == str(callback_data.draft_id)
        and data.get("finished_message_id") == message.message_id
    ):
        text = (
            "Это напоминание уже сохранено."
            if data["finished_action"] == "confirmed"
            else "Создание этого напоминания уже отменено."
        )
        await callback.answer(text, show_alert=True)
        return

    draft = data.get("draft")
    if (
        not isinstance(draft, ReminderDraft)
        or draft.id != callback_data.draft_id
        or data.get("draft_message_id") != message.message_id
    ):
        await callback.answer(
            "Черновик устарел. Используйте последнее сообщение или напишите новое.",
            show_alert=True,
        )
        return
    if (
        draft.telegram_user_id != callback.from_user.id
        or draft.chat_id != message.chat.id
    ):
        await callback.answer("Этот черновик недоступен.", show_alert=True)
        return

    await callback.answer()
    if callback_data.action == "cancel":
        await _finish(
            state, draft_id=draft.id, message_id=message.message_id,
            action="cancelled",
        )
        await _edit_or_send(message, "Создание напоминания отменено.")
        return

    try:
        saved = await reminders.confirm(
            draft=draft, telegram_user_id=callback.from_user.id,
            chat_id=message.chat.id, now=_now_utc(),
        )
    except ReminderError as error:
        await state.clear()
        await _edit_or_send(
            message, f"Напоминание не сохранено: {error}\n\nНапишите новое сообщение.",
        )
        return
    except (SQLAlchemyError, OSError, TimeoutError):
        logger.exception("Cannot confirm reminder")
        # Сохраняем черновик и кнопку для повторной попытки.
        await message.answer(
            "Сейчас не получается сохранить напоминание. "
            "Попробуйте нажать «Подтвердить» ещё раз позже."
        )
        return

    await _finish(
        state, draft_id=draft.id, message_id=message.message_id,
        action="confirmed",
    )
    await _edit_or_send(message, _saved_text(saved))


@router.callback_query(F.data.startswith("draft:"))
async def handle_invalid_draft_button(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка недействительна. Напишите новое сообщение.", show_alert=True)


@router.message(F.text.startswith("/"))
async def handle_unknown_command(message: Message) -> None:
    await message.answer("Неизвестная команда. Список команд: /help.")


@router.message(F.text)
async def handle_text(
    message: Message,
    state: FSMContext,
    bot: Bot,
    assistant: AssistantPort,
    reminders: ReminderService,
) -> None:
    if message.from_user is None or message.text is None:
        return
    text = message.text.strip()
    if not text:
        await message.answer("Напишите вопрос или просьбу о напоминании.")
        return

    try:
        # Точка вызова assistant. Сейчас через DI передаётся FakeAssistant.
        reply = await assistant.handle(
            telegram_user_id=message.from_user.id, text=text, now=_now_utc(),
        )
    except Exception:
        logger.exception("Assistant request failed")
        await message.answer("Сейчас не получается обработать сообщение. Попробуйте позже.")
        return

    if reply.type == ReplyType.ANSWER and reply.text.strip():
        await message.answer(reply.text, parse_mode=None)
        return
    if reply.type != ReplyType.REMINDER or reply.reminder is None:
        logger.error("Assistant returned an incomplete reply")
        await message.answer("Не получилось подготовить ответ. Попробуйте написать ещё раз.")
        return

    intent = reply.reminder
    try:
        draft = reminders.prepare(
            telegram_user_id=message.from_user.id, chat_id=message.chat.id,
            text=intent.text, local_datetime=intent.local_datetime,
            timezone_name=intent.timezone, frequency=intent.frequency,
            material_url=intent.material_url, now=_now_utc(),
        )
    except ReminderError as error:
        await message.answer(f"Не удалось подготовить напоминание: {error}", parse_mode=None)
        return

    previous = await state.get_data()
    sent = await message.answer(
        _draft_text(draft), reply_markup=_draft_keyboard(draft), parse_mode=None,
    )
    await state.set_data({"draft": draft, "draft_message_id": sent.message_id})
    await state.set_state(ReminderState.waiting_for_confirmation)
    await _remove_keyboard(bot, message.chat.id, previous.get("draft_message_id"))


@router.message()
async def handle_non_text(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте запрос текстом.")
