# RAG на примере «Альфа-Курса»

RAG связывает поиск и генерацию. Поиск приносит факты из базы, модель собирает из них ответ для конкретного пользователя. Для Java-бэкендера структура знакомая. aiogram играет роль контроллера, AssistantService похож на прикладной сервис, а KnowledgePort описывает контракт поиска. Pydantic проверяет DTO, Alembic версионирует схему, как Flyway.

## Подготовка индекса

[Импорт](../src/alfa_bot/knowledge/ingest.py) читает подготовленный JSONL или опубликованные записи в [восстановленной базе](../src/alfa_bot/knowledge/source_postgres.py). Поля Title, Description и HtmlContent проходят очистку HTML через BeautifulSoup. Курсы дают только публичные карточки. [Разбиение](../src/alfa_bot/knowledge/corpus.py) по возможности использует границы абзацев и слов.

    chunks = prepare_corpus(
        records, chunk_size=settings.chunk_size_tokens, overlap=settings.chunk_overlap_tokens,
    )
    await replace_index(database.sessions, chunks, embeddings)

По умолчанию чанк ограничен примерно 600 токенами. Перекрытие в 50 токенов помогает сохранить связь между соседними кусками. o200k_base служит мерой длины. У BGE-M3 свой токенизатор, поэтому запрос к Ollama запрещает тихое обрезание слишком длинного входа. [Документация разбиения LangChain](https://docs.langchain.com/oss/python/integrations/splitters/split_by_token).

BGE-M3 превращает фрагмент в 1024 числа. [Запись корпуса](../src/alfa_bot/knowledge/index_postgres.py) сохраняет в kb.chunks вектор, текст, назначение и JSONB с ID источника, заголовком, URL и доступом. Назначение knowledge даёт факты для ответа, catalog даёт публичные карточки. После вычисления векторов одна транзакция публикует новый снимок. Одинаковые корпус и имя модели определяются по отпечатку. Для обновления весов под тем же именем используйте --force. [Карточка модели BGE-M3](https://huggingface.co/BAAI/bge-m3).

## Поиск по вопросу

По запросу вычисляется вектор в том же пространстве. pgvector сравнивает направления векторов. Чем ближе смысл, тем чаще сходство выше. Упрощённая форма запроса:

    SELECT id, 1 - (embedding <=> CAST(:query_vector AS vector)) AS score
    FROM kb.chunks
    WHERE purpose = 'knowledge'
    ORDER BY score DESC
    LIMIT 5;

Оператор <=> возвращает косинусное расстояние. Score 0,60 — начальный порог сходства, а не вероятность правильного ответа. [Рабочий SQL](../src/alfa_bot/knowledge/retrieval.py) применяет порог, фильтры доступа и лимит в два фрагмента на материал. Точный поиск подходит для текущего объёма. При росте корпуса замерьте задержку и recall перед выбором HNSW. [Документация pgvector](https://github.com/pgvector/pgvector#querying).

## Диалог и персонализация

- AssistantPort.handle принимает Telegram ID, текст и время. Reply содержит готовый Answer, просьбу уточнить, ReminderIntent или признак вопроса вне темы. [DTO между Telegram и ассистентом](../src/alfa_bot/contracts.py).
- KnowledgePort.retrieve возвращает фрагмент, метаданные и score. KnowledgePort.recommend ищет карточки по теме, виду и доступу. [Контракт базы знаний](../src/alfa_bot/knowledge/service.py).
- Decision от маршрутизатора содержит интент, поисковую формулировку и явно названные поля профиля. GroundedAnswer возвращает answerable и абзацы с chunk_ids. [Схемы Pydantic](../src/alfa_bot/assistant/schemas.py).

Упрощённый фрагмент оркестрации с теми же типами и вызовами, что в сервисе:

    decision = await llm.complete(system=ROUTER_SYSTEM, payload=turn, schema=Decision)
    update_profile(profile, decision)
    style = answer_style(profile, decision)
    hits = await knowledge.retrieve(decision.query)
    if not hits:
        return NO_KNOWLEDGE

Дальше ассистент собирает evidence, вызывает Qwen со схемой GroundedAnswer и сверяет её chunk_ids с hits. Если пользователь сообщил намерение напомнить, сервис собирает ReminderIntent и ждёт подтверждения в Telegram. Этот путь обходится без retrieval.

Уточнение «на УСН» меняет предмет поиска по налогам. Просьба «сейчас кратко» меняет формат только текущего ответа. Просьба «обычно отвечай кратко» сохраняется в профиле. Сведения о стадии бизнеса и уровне опыта помогают выбрать практический шаг или учебный материал. Текущая просьба имеет приоритет. [Логика персонализации](../src/alfa_bot/assistant/personalization.py).

Пример данных для генерации:

    {
      "question": "Как привлечь первых клиентов в кофейню?",
      "profile": {"business_stage": "planning", "business_type": "кофейня"},
      "response_style": "brief",
      "evidence": [
        {"chunk_id": "article:coffee-first:0", "text": "Добавьте адрес и часы работы в карточку кофейни."}
      ]
    }

Системный промпт требует брать бизнес-факты из evidence и указывать chunk_ids в каждом абзаце. Когда фактов мало, модель возвращает answerable=false. Ассистент проверяет ID и берёт URL из метаданных. Telegram превращает номера источников в ссылки. [Полный промпт для Qwen](../src/alfa_bot/assistant/prompts.py).

## Что использует LangChain

RecursiveCharacterTextSplitter режет текст. ChatPromptTemplate формирует сообщения модели. Поиск выполняет прямой SQL. Он позволяет видеть порог, фильтры и лимиты. Ollama получает JSON Schema, Pydantic проверяет структурированный ответ. StrOutputParser здесь лишил бы приложение проверки полей. [Structured outputs в Ollama](https://docs.ollama.com/capabilities/structured-outputs).

За память отвечает текущий репозиторий PostgreSQL. Он одной версионированной записью сохраняет историю, профиль и ожидаемое уточнение после обработки. RunnableWithMessageHistory полезен, когда сама цепочка владеет историей. Здесь граница сохранения охватывает весь ход диалога. [RunnableWithMessageHistory](https://reference.langchain.com/python/langchain-core/runnables/history/RunnableWithMessageHistory).

## Что каждый участник может показать на защите

- Java-бэкенд: миграция, путь Telegram → AssistantPort → KnowledgePort и управление напоминаниями.
- Python 1: отбор публичных материалов, батчи эмбеддингов и замеры скорости индексации.
- Python 2: набор запросов с эталонными ссылками, recall@5 и частота ложных совпадений. Сравнить сохранённый BM25 и BGE-M3.
- Python 3: ответ с источником, смена стиля, честный отказ и проверки интентов.
- Продакт: пройти сценарии жюри на двух профилях и объяснить, как проверить возврат на платформу и покупки.

Для отладки сначала запускайте knowledge.search. Если нужный текст уже в top-5, смотрите промпт, GroundedAnswer и отображение Reply.
