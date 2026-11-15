"""Telegram — один из транспортов доставки, а не часть сервиса напоминаний."""

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import LinkPreviewOptions

from alfa_bot.bot.rendering import Part, html_chunks
from alfa_bot.contracts import Delivery, DeliveryRejected, DeliveryRetry


class TelegramDelivery:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send(self, delivery: Delivery) -> None:
        parts = [Part("Напоминаю: " + delivery.text)]
        if delivery.material_url:
            parts.extend([Part("\n\n"), Part("Открыть материал", delivery.material_url)])
        try:
            for chunk in html_chunks(parts):
                await self.bot.send_message(
                    delivery.chat_id,
                    chunk,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    request_timeout=10,
                )
        except (TelegramForbiddenError, TelegramBadRequest):
            raise DeliveryRejected() from None
        except TelegramRetryAfter as error:
            raise DeliveryRetry(error.retry_after) from None
        except (TelegramNetworkError, TelegramServerError, OSError, TimeoutError):
            raise DeliveryRetry(5) from None
