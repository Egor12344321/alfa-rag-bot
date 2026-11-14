# Первое распределение работы

## Python 1

- Проверить PK/FK, статусы, доступ, тарифы, URL и типы блоков исходных таблиц.
- Заполнить knowledge/mapping.yml и заменить deny-by-default подтверждёнными правилами.
- Реализовать parsing, ingestion, repository и KnowledgeService.
- Зафиксировать embedding-модель/размерность/токенизацию, проверять лимит без обрезания.
- Проверить повтор ingest, версии, фильтры и качество retrieval на eval.

## Python 2

- Реализовать AssistantService и AssistantRepository.
- Сохранять исходный вопрос, профиль, уточнение и состояние по version.
- Разделять основания ответа и публичные карточки рекомендаций.
- Подключить LLM и проверить citations/URLs/paid restrictions/no-answer.
- С продактом разметить eval; не оценивать качество только similarity score.

## Python 3

- Реализовать Telegram-команды, callbacks и их проверку владельца.
- С DevOps подключить durable inbox, затем webhook/polling.
- Реализовать draft/confirm/list/change/cancel, timezone и повторения.
- enqueue_due создаёт delivery и меняет next_run_at одной транзакцией.
- Проверить двойной callback, отмену, revision, DST и перезапуск.

## DevOps

- Проверить Docker Compose на чистом окружении и применить initial migration.
- Разделить роли source-reader/ingest/runtime/migration-owner.
- С Python 3 реализовать Outbox sender, lease/backoff/retry_after и graceful shutdown.
- Настроить HTTPS, webhook secret, исключение секретов и мониторинг readiness.
- Координировать миграции и зафиксировать способ запуска для жюри.

## Продакт

- Зафиксировать обязательные сценарии трека 2 и критерии приёмки.
- Подготовить тестовые профили и вопросы, оценить уместность следующего шага.
- Описать opt-in, повторения, изменение/отключение и поведение при пропуске reminder.
- Подготовить демонстрацию и план проверки возврата на платформу/покупок.

Первый checkpoint: реальный вопрос в Telegram -> ответ с источником -> подтверждённое
напоминание -> доставка после перезапуска. Затем расширять корпус и покрытие сценариев.
