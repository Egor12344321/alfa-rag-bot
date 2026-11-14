from aiogram import Router

router = Router(name="telegram")

# Python 3: команды /start, /help, /reminders; кнопки confirm/change/cancel.
# Сначала durable inbox, затем обработка Assistant. Отправка через outbox.
# В callbacks проверять пользователя, draft_id/reminder_id и актуальную revision.
# Пока здесь нет handler: зарегистрировать его после реализации inbox.
