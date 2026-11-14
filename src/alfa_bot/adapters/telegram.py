import logging
import re
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
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
)
from sqlalchemy.exc import SQLAlchemyError

from alfa_bot.contracts import (
    Answer,
    AssistantPort,
    MaterialAccess,
    MaterialCard,
    MaterialKind,
    Reply,
    ReplyType,
)
from alfa_bot.reminders.service import (
    ReminderDraft,
    ReminderError,
    ReminderService,
    ReminderView,
)

logger = logging.getLogger(__name__)
router = Router(name="telegram")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

ASK_BUTTON = "Задать вопрос"
REMINDERS_BUTTON = "Мои напоминания"
OUT_OF_SCOPE_TEXT = (
    "Я помогаю с вопросами о бизнесе и обучении на «Альфа-Курсе», "
    "а ещё создаю напоминания.\n\n"
    "Задайте вопрос по теме или напишите, о чём и когда напомнить."
)
MATERIAL_KIND_NAMES = {
    MaterialKind.ARTICLE: "Статья",
    MaterialKind.COURSE: "Курс",
    MaterialKind.OTHER: "Материал",
}
MATERIAL_ACCESS_NAMES = {
    MaterialAccess.FREE: "Бесплатно",
    MaterialAccess.PAID: "Платно",
    MaterialAccess.UNKNOWN: "Условия доступа не указаны",
}
FREQUENCY_NAMES = {
    "once": "один раз",
    "daily": "ежедневно",
    "weekly": "еженедельно",
    "monthly": "ежемесячно",
}
STATUS_NAMES = {"active": "включено", "paused": "отключено", "done": "выполнено"}
MESSAGE_LIMIT = 4000  # Оставляем запас до лимита Telegram, считаем в UTF-16.
SOURCE_MARKER = re.compile(r"\[([1-9][0-9]{0,2})\]")


class ReminderState(StatesGroup):
    waiting_for_confirmation = State()


class DraftCallback(CallbackData, prefix="draft"):
    action: Literal["confirm", "cancel"]
    draft_id: UUID


class ReminderCallback(CallbackData, prefix="rem"):
    action: Literal["frequency", "disable", "delete", "delete_yes", "view"]
    reminder_id: UUID


class FrequencyCallback(CallbackData, prefix="freq"):
    frequency: Literal["once", "daily", "weekly", "monthly"]
    reminder_id: UUID


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


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _text_chunks(text: str, *, links: list[tuple[int, int, str]] | None = None):
    """Позиции ссылок входного текста заданы в символах Python."""
    links = links or []
    start = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start == len(text):
            return
        end = start
        units = 0
        for char in text[start:]:
            units += 2 if ord(char) > 0xFFFF else 1
            if units > MESSAGE_LIMIT:
                break
            end += 1
        if end < len(text):
            boundary = text.rfind("\n", start, end + 1)
            midpoint = (start + end) // 2
            if boundary < midpoint:
                boundary = text.rfind(" ", start, end + 1)
            if boundary >= midpoint:
                end = boundary
            for link_start, link_end, _ in links:
                if start < link_start < end < link_end:
                    end = link_start
                    break
        entities = [
            MessageEntity(
                type="text_link",
                offset=_utf16_length(text[start:link_start]),
                length=_utf16_length(text[link_start:link_end]),
                url=url,
            )
            for link_start, link_end, url in links
            if start <= link_start < link_end <= end
        ]
        yield text[start:end].rstrip(), entities
        start = end


async def _send_text(message: Message, text: str) -> None:
    for chunk, _ in _text_chunks(text):
        await message.answer(
            chunk,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


def _material_details(material: MaterialCard) -> str:
    access = MATERIAL_ACCESS_NAMES[material.access]
    if material.price:
        access += f" · {material.price}"
    details = f"{MATERIAL_KIND_NAMES[material.kind]} · {access}"
    description = " ".join(material.description.split())
    if description:
        details += "\n" + (description[:297] + "…" if len(description) > 300 else description)
    return details


async def _send_answer(message: Message, answer: Answer) -> None:
    body = answer.text
    links = [
        (match.start(), match.end(), answer.sources[number - 1].url)
        for match in SOURCE_MARKER.finditer(body)
        if (number := int(match[1])) <= len(answer.sources)
    ]
    if answer.recommendations:
        body += "\n\nЧто изучить дальше"
        for material in answer.recommendations:
            title = " ".join(material.title.split())[:180]
            body += "\n\n"
            links.append((len(body), len(body) + len(title), material.url))
            body += title + "\n" + _material_details(material)
    for chunk, entities in _text_chunks(body, links=links):
        await message.answer(
            chunk,
            entities=entities,
            parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


def _draft_keyboard(draft: ReminderDraft) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Подтвердить",
                callback_data=DraftCallback(action="confirm", draft_id=draft.id).pack(),
            ),
            InlineKeyboardButton(
                text="Отмена",
                callback_data=DraftCallback(action="cancel", draft_id=draft.id).pack(),
            ),
        ]
    ]
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
    return (
        "Проверьте напоминание:\n\n"
        + _details(
            text=draft.text,
            run_at=draft.starts_at,
            timezone=draft.timezone,
            frequency=draft.frequency,
        )
        + "\n\nВсё верно?"
    )


def _saved_text(reminder: ReminderView) -> str:
    return "Напоминание сохранено.\n\n" + _details(
        text=reminder.text,
        run_at=reminder.next_run_at,
        timezone=reminder.timezone,
        frequency=reminder.frequency,
    )


def _reminder_text(reminder: ReminderView, notice: str = "") -> str:
    text = _details(
        text=reminder.text,
        run_at=reminder.next_run_at,
        timezone=reminder.timezone,
        frequency=reminder.frequency,
    )
    text += f"\nСтатус: {STATUS_NAMES.get(reminder.status, reminder.status)}"
    return f"{notice}\n\n{text}" if notice else text


def _reminder_keyboard(reminder: ReminderView) -> InlineKeyboardMarkup:
    rows = []
    if reminder.status != "done":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Изменить частоту",
                    callback_data=ReminderCallback(
                        action="frequency", reminder_id=reminder.id
                    ).pack(),
                )
            ]
        )
    if reminder.status == "active":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Отключить",
                    callback_data=ReminderCallback(
                        action="disable", reminder_id=reminder.id
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Удалить",
                callback_data=ReminderCallback(action="delete", reminder_id=reminder.id).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _frequency_keyboard(reminder: ReminderView) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✓ " if frequency == reminder.frequency else "") + label,
            callback_data=FrequencyCallback(frequency=frequency, reminder_id=reminder.id).pack(),
        )
        for frequency, label in FREQUENCY_NAMES.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:2],
            buttons[2:],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=ReminderCallback(action="view", reminder_id=reminder.id).pack(),
                )
            ],
        ]
    )


def _delete_keyboard(reminder_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=ReminderCallback(
                        action="delete_yes", reminder_id=reminder_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=ReminderCallback(action="view", reminder_id=reminder_id).pack(),
                ),
            ]
        ]
    )


async def _remove_keyboard(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
            request_timeout=5,
        )
    except TelegramAPIError:
        # Старую кнопку всё равно отклонит проверка UUID и состояния.
        logger.info("Could not remove an old draft keyboard")


async def _edit_or_send(
    message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> Message:
    if message.text == text and message.reply_markup == reply_markup:
        return message
    try:
        edited = await message.edit_text(text, reply_markup=reply_markup, parse_mode=None)
        return edited if isinstance(edited, Message) else message
    except TelegramBadRequest as error:
        # Повторный callback может содержать старую копию уже обновлённой карточки.
        if "message is not modified" in error.message.lower():
            return message
        return await message.answer(text, reply_markup=reply_markup, parse_mode=None)


async def _private_message(callback: CallbackQuery) -> Message | None:
    if (
        not isinstance(callback.message, Message)
        or callback.message.chat.id != callback.from_user.id
    ):
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return None
    return callback.message


async def _callback_reminder(
    callback: CallbackQuery, reminders: ReminderService, reminder_id: UUID
) -> ReminderView | None:
    try:
        return await reminders.get(
            reminder_id=reminder_id,
            telegram_user_id=callback.from_user.id,
        )
    except ReminderError as error:
        await callback.answer(str(error), show_alert=True)
    except (SQLAlchemyError, OSError, TimeoutError):
        logger.exception("Cannot load reminder for management")
        await callback.answer(
            "Не получилось загрузить напоминание. Попробуйте позже.", show_alert=True
        )
    return None


def _matches_delete(data: dict, reminder_id: UUID, message: Message) -> bool:
    pending = data.get("delete_confirmation")
    return isinstance(pending, dict) and (
        pending.get("reminder_id") == str(reminder_id)
        and pending.get("chat_id") == message.chat.id
        and pending.get("message_id") == message.message_id
    )


async def _clear_delete(state: FSMContext, reminder_id: UUID, message: Message) -> None:
    if _matches_delete(await state.get_data(), reminder_id, message):
        # Не затрагиваем возможный черновик создания в этом же состоянии FSM.
        await state.update_data(delete_confirmation=None)


async def _finish(state: FSMContext, *, draft_id: UUID, message_id: int, action: str) -> None:
    # Оставляем одну квитанцию для повторных нажатий; сам черновик удаляем.
    await state.set_data(
        {
            "finished_draft_id": str(draft_id),
            "finished_message_id": message_id,
            "finished_action": action,
        }
    )
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
        "Я отвечаю на вопросы о бизнесе по базе знаний «Альфа-Курса», "
        "подбираю материалы и помогаю создавать напоминания.\n\n"
        "/start — главное меню\n"
        "/new — очистить историю и профиль, начать новый диалог\n"
        "/reminders — список, частота, отключение и удаление\n"
        "/cancel — отменить текущий запрос или черновик\n\n"
        "Примеры: «Как привлечь клиентов в кофейню?», "
        "«Подбери бесплатные курсы по продажам», "
        "«Напомни завтра в 10:00 МСК изучить статью».\n"
        "Напоминание создаётся только после подтверждения."
    )


async def _assistant_command(
    message: Message,
    assistant: AssistantPort,
    command: str,
) -> Reply | None:
    if message.from_user is None:
        return None
    try:
        return await assistant.handle(
            telegram_user_id=message.from_user.id,
            text=command,
            now=_now_utc(),
        )
    except Exception:
        logger.exception("Assistant command failed")
        await message.answer("Не получилось обновить диалог. Попробуйте позже.")
        return None


@router.message(Command("new"))
async def handle_new(
    message: Message,
    state: FSMContext,
    bot: Bot,
    assistant: AssistantPort,
) -> None:
    reply = await _assistant_command(message, assistant, "/new")
    if reply is None:
        return
    data = await state.get_data()
    await state.clear()
    await _remove_keyboard(bot, message.chat.id, data.get("draft_message_id"))
    await _send_text(message, reply.answer.text)


@router.message(Command("cancel"))
async def handle_cancel(
    message: Message,
    state: FSMContext,
    bot: Bot,
    assistant: AssistantPort,
) -> None:
    data = await state.get_data()
    draft = data.get("draft")
    reply = await _assistant_command(message, assistant, "/cancel")
    if reply is None:
        return
    if not isinstance(draft, ReminderDraft):
        await _send_text(message, reply.answer.text)
        return
    await _finish(
        state,
        draft_id=draft.id,
        message_id=data["draft_message_id"],
        action="cancelled",
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
        await message.answer(
            _reminder_text(item),
            reply_markup=_reminder_keyboard(item),
            parse_mode=None,
        )


@router.callback_query(ReminderCallback.filter())
async def handle_reminder_action(
    callback: CallbackQuery,
    callback_data: ReminderCallback,
    state: FSMContext,
    reminders: ReminderService,
) -> None:
    message = await _private_message(callback)
    if message is None:
        return
    reminder_id = callback_data.reminder_id

    if callback_data.action == "delete_yes":
        if not _matches_delete(await state.get_data(), reminder_id, message):
            await callback.answer(
                "Подтверждение удаления устарело. Нажмите «Удалить» ещё раз.",
                show_alert=True,
            )
            return
        await callback.answer()
        try:
            deleted = await reminders.delete(
                reminder_id=reminder_id,
                telegram_user_id=callback.from_user.id,
            )
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.exception("Cannot delete reminder")
            await message.answer("Не получилось удалить напоминание. Попробуйте ещё раз позже.")
            return
        await _clear_delete(state, reminder_id, message)
        await _edit_or_send(
            message,
            "Напоминание удалено." if deleted else "Напоминание уже удалено или недоступно.",
        )
        return

    reminder = await _callback_reminder(callback, reminders, reminder_id)
    if reminder is None:
        return
    if reminder.status == "done" and callback_data.action in ("frequency", "disable"):
        await _clear_delete(state, reminder_id, message)
        await callback.answer("Напоминание уже выполнено.", show_alert=True)
        await _edit_or_send(message, "Напоминание уже выполнено. Обновите «Мои напоминания».")
        return

    await callback.answer()
    if callback_data.action != "delete":
        await _clear_delete(state, reminder_id, message)

    if callback_data.action == "frequency":
        notice = "Выберите частоту напоминания."
        if reminder.status == "paused":
            notice += " Статус «отключено» сохранится."
        await _edit_or_send(
            message,
            _reminder_text(reminder, notice),
            _frequency_keyboard(reminder),
        )
    elif callback_data.action == "disable":
        try:
            updated = await reminders.disable(
                reminder_id=reminder_id,
                telegram_user_id=callback.from_user.id,
            )
        except ReminderError as error:
            await _edit_or_send(message, f"{error}\nОбновите «Мои напоминания».")
            return
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.exception("Cannot disable reminder")
            await message.answer("Не получилось отключить напоминание. Попробуйте позже.")
            return
        await _edit_or_send(
            message,
            _reminder_text(updated, "Напоминание отключено."),
            _reminder_keyboard(updated),
        )
    elif callback_data.action == "delete":
        card = await _edit_or_send(
            message,
            _reminder_text(reminder, "Удалить это напоминание?"),
            _delete_keyboard(reminder_id),
        )
        await state.update_data(
            delete_confirmation={
                "reminder_id": str(reminder_id),
                "chat_id": card.chat.id,
                "message_id": card.message_id,
            }
        )
    else:
        await _edit_or_send(message, _reminder_text(reminder), _reminder_keyboard(reminder))


@router.callback_query(FrequencyCallback.filter())
async def handle_frequency_choice(
    callback: CallbackQuery,
    callback_data: FrequencyCallback,
    state: FSMContext,
    reminders: ReminderService,
) -> None:
    message = await _private_message(callback)
    if message is None:
        return
    reminder = await _callback_reminder(callback, reminders, callback_data.reminder_id)
    if reminder is None:
        return
    if reminder.status == "done":
        await _clear_delete(state, reminder.id, message)
        await callback.answer("Напоминание уже выполнено.", show_alert=True)
        await _edit_or_send(message, "Напоминание уже выполнено. Обновите «Мои напоминания».")
        return

    await callback.answer()
    await _clear_delete(state, reminder.id, message)
    if callback_data.frequency == reminder.frequency:
        # Повторный выбор не должен сдвигать ближайшую дату запуска.
        updated = reminder
        notice = "Частота не изменилась."
    else:
        try:
            updated = await reminders.change_frequency(
                reminder_id=reminder.id,
                telegram_user_id=callback.from_user.id,
                frequency=callback_data.frequency,
                now=_now_utc(),
            )
        except ReminderError as error:
            await _edit_or_send(message, f"{error}\nОбновите «Мои напоминания».")
            return
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.exception("Cannot change reminder frequency")
            await message.answer("Не получилось изменить частоту. Попробуйте ещё раз позже.")
            return
        notice = "Частота изменена."
    await _edit_or_send(message, _reminder_text(updated, notice), _reminder_keyboard(updated))


@router.callback_query(F.data.startswith("rem:") | F.data.startswith("freq:"))
async def handle_invalid_reminder_button(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка недействительна. Обновите «Мои напоминания».", show_alert=True)


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
    if draft.telegram_user_id != callback.from_user.id or draft.chat_id != message.chat.id:
        await callback.answer("Этот черновик недоступен.", show_alert=True)
        return

    await callback.answer()
    if callback_data.action == "cancel":
        await _finish(
            state,
            draft_id=draft.id,
            message_id=message.message_id,
            action="cancelled",
        )
        await _edit_or_send(message, "Создание напоминания отменено.")
        return

    try:
        saved = await reminders.confirm(
            draft=draft,
            telegram_user_id=callback.from_user.id,
            chat_id=message.chat.id,
            now=_now_utc(),
        )
    except ReminderError as error:
        await state.clear()
        await _edit_or_send(
            message,
            f"Напоминание не сохранено: {error}\n\nНапишите новое сообщение.",
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
        state,
        draft_id=draft.id,
        message_id=message.message_id,
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
        # Реализация AssistantPort передаётся через DI в main.py.
        reply = await assistant.handle(
            telegram_user_id=message.from_user.id,
            text=text,
            now=_now_utc(),
        )
        if not isinstance(reply, Reply):
            raise ValueError("Assistant должен возвращать Reply.")
    except Exception:
        logger.exception("Assistant request failed")
        await message.answer("Сейчас не получается обработать сообщение. Попробуйте позже.")
        return

    if reply.type == ReplyType.ANSWER and reply.answer is not None:
        await _send_answer(message, reply.answer)
        return
    if reply.type == ReplyType.CLARIFICATION:
        await _send_text(message, reply.text)
        return
    if reply.type == ReplyType.OUT_OF_SCOPE:
        await message.answer(OUT_OF_SCOPE_TEXT, parse_mode=None)
        return
    if reply.type != ReplyType.REMINDER or reply.reminder is None:
        logger.error("Assistant returned an incomplete reply")
        await message.answer("Не получилось подготовить ответ. Попробуйте написать ещё раз.")
        return

    intent = reply.reminder
    try:
        draft = reminders.prepare(
            telegram_user_id=message.from_user.id,
            chat_id=message.chat.id,
            text=intent.text,
            local_datetime=intent.local_datetime,
            timezone_name=intent.timezone,
            frequency=intent.frequency,
            material_url=intent.material_url,
            now=_now_utc(),
        )
    except ReminderError as error:
        await message.answer(f"Не удалось подготовить напоминание: {error}", parse_mode=None)
        return

    previous = await state.get_data()
    sent = await message.answer(
        _draft_text(draft),
        reply_markup=_draft_keyboard(draft),
        parse_mode=None,
    )
    await state.set_data({"draft": draft, "draft_message_id": sent.message_id})
    await state.set_state(ReminderState.waiting_for_confirmation)
    await _remove_keyboard(bot, message.chat.id, previous.get("draft_message_id"))


@router.message()
async def handle_non_text(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте запрос текстом.")
