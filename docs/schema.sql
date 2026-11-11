-- Проект рабочей БД assistant_db для Альфа Курс, 2026-11-11.
-- Выполнять миграционной ролью в НОВОЙ assistant_db, НЕ в alfa_source.
-- PostgreSQL с расширением pgvector. Embeddings: multilingual-e5-small, 384.
-- Это стартовая схема проекта, а не миграция уже существующей системы.
-- Правила доступа исходного дампа реализуются в ingestion mapping отдельно.
-- Применение и интеграционные тесты на живой PostgreSQL ещё необходимы.

BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS kb;
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE kb.ingestion_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status text NOT NULL DEFAULT 'building'
        CHECK (status IN ('building', 'ready', 'failed')),
    source_fingerprint text NOT NULL,
    embedding_model text NOT NULL,
    embedding_revision text NOT NULL,
    embedding_dim integer NOT NULL CHECK (embedding_dim = 384),
    pipeline_version text NOT NULL,
    mapping_version text NOT NULL,
    policy_version text NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE kb.index_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id)
);
-- Публиковать только ready-run после проверок, одной транзакцией.
-- runtime не получает право изменять эту таблицу.

CREATE TABLE kb.materials (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id),
    source_table text NOT NULL,
    source_id text NOT NULL, -- исходный идентификатор сохраняется без предположения о типе
    kind text NOT NULL CHECK (kind IN ('article', 'document', 'term', 'course', 'webinar')),
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    summary text NOT NULL DEFAULT '', -- только разрешённое публичное описание
    url text CHECK (url IS NULL OR url ~* '^https?://'),
    access_type text NOT NULL DEFAULT 'unknown'
        CHECK (access_type IN ('free', 'paid', 'subscription', 'unknown')),
    price numeric(14,2) CHECK (price IS NULL OR price >= 0),
    currency text,
    price_note text,
    is_active boolean NOT NULL DEFAULT false,
    answer_allowed boolean NOT NULL DEFAULT false,
    recommend_allowed boolean NOT NULL DEFAULT false,
    external_allowed boolean NOT NULL DEFAULT false,
    source_modified_at timestamptz,
    content_hash text NOT NULL,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, source_table, source_id),
    UNIQUE (run_id, id),
    CHECK (NOT answer_allowed OR (is_active AND access_type = 'free')),
    CHECK (NOT recommend_allowed OR (is_active AND url IS NOT NULL))
);

CREATE TABLE kb.chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    material_id uuid NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('knowledge', 'catalog')),
    chunk_no integer NOT NULL CHECK (chunk_no >= 0),
    text text NOT NULL CHECK (length(btrim(text)) > 0),
    section_path text,
    source_blocks jsonb NOT NULL DEFAULT '[]'::jsonb,
    token_count integer NOT NULL CHECK (token_count BETWEEN 1 AND 512),
    content_hash text NOT NULL,
    embedding vector(384) NOT NULL,
    search_tsv tsvector GENERATED ALWAYS AS
        (to_tsvector('russian'::regconfig, text)) STORED,
    UNIQUE (material_id, purpose, chunk_no),
    FOREIGN KEY (run_id, material_id) REFERENCES kb.materials(run_id, id)
);
-- token_count включает полный embedding input: prefix, title, text, special tokens.
-- Проверка purpose и содержимого против material flags выполняется ingest и retrieval.
-- catalog chunks НЕ используются как основание подробного ответа.
CREATE INDEX chunks_scope_idx ON kb.chunks(run_id, purpose, material_id);
CREATE INDEX chunks_fts_idx ON kb.chunks USING gin(search_tsv);
-- Опционально, только после сравнения задержки/полноты с точным поиском:
-- CREATE INDEX chunks_hnsw_idx ON kb.chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE kb.topics (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id),
    source_id text NOT NULL,
    title text NOT NULL,
    UNIQUE (run_id, source_id),
    UNIQUE (run_id, id)
);
CREATE TABLE kb.material_topics (
    run_id uuid NOT NULL,
    material_id uuid NOT NULL,
    topic_id uuid NOT NULL,
    PRIMARY KEY (material_id, topic_id),
    FOREIGN KEY (run_id, material_id) REFERENCES kb.materials(run_id, id),
    FOREIGN KEY (run_id, topic_id) REFERENCES kb.topics(run_id, id)
);
CREATE INDEX material_topics_topic_idx ON kb.material_topics(topic_id, material_id);

CREATE TABLE kb.material_relations (
    run_id uuid NOT NULL,
    from_material_id uuid NOT NULL,
    to_material_id uuid NOT NULL,
    relation_type text NOT NULL DEFAULT 'recommended',
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (from_material_id, to_material_id, relation_type),
    FOREIGN KEY (run_id, from_material_id) REFERENCES kb.materials(run_id, id),
    FOREIGN KEY (run_id, to_material_id) REFERENCES kb.materials(run_id, id),
    CHECK (from_material_id <> to_material_id)
);

CREATE TABLE app.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id bigint NOT NULL UNIQUE,
    telegram_chat_id bigint NOT NULL UNIQUE, -- MVP: только личные чаты
    timezone text, -- IANA zone, проверяется ZoneInfo в приложении
    profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    delivery_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(profile) = 'object')
);

CREATE TABLE app.conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES app.users(id),
    state text NOT NULL DEFAULT 'ready' CHECK (state IN (
        'ready', 'awaiting_question_clarification',
        'awaiting_reminder_details', 'awaiting_reminder_confirmation')),
    pending_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    clarification_count smallint NOT NULL DEFAULT 0
        CHECK (clarification_count BETWEEN 0 AND 1),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz
);
CREATE UNIQUE INDEX one_live_conversation_idx ON app.conversations(user_id)
    WHERE closed_at IS NULL;

CREATE TABLE app.telegram_updates (
    update_id bigint PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app.users(id),
    chat_id bigint NOT NULL,
    payload jsonb NOT NULL, -- нормализованные поля, без ненужного профиля Telegram
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lock_token uuid,
    locked_until timestamptz,
    last_error_code text,
    received_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK ((status = 'processing' AND lock_token IS NOT NULL AND locked_until IS NOT NULL)
        OR (status <> 'processing' AND lock_token IS NULL AND locked_until IS NULL))
);
CREATE INDEX updates_due_idx ON app.telegram_updates(available_at, update_id)
    WHERE status = 'pending';
CREATE UNIQUE INDEX one_processing_update_per_chat_idx ON app.telegram_updates(chat_id)
    WHERE status = 'processing';
CREATE INDEX updates_lease_idx ON app.telegram_updates(locked_until)
    WHERE status = 'processing';

CREATE TABLE app.messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES app.conversations(id),
    origin_update_id bigint REFERENCES app.telegram_updates(update_id),
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    text text NOT NULL,
    outcome text,
    retrieval_run_id uuid REFERENCES kb.ingestion_runs(id),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (origin_update_id, role)
);
CREATE INDEX messages_history_idx ON app.messages(conversation_id, created_at, id);

CREATE TABLE app.message_sources (
    message_id uuid NOT NULL REFERENCES app.messages(id),
    ordinal smallint NOT NULL CHECK (ordinal > 0),
    chunk_id uuid NOT NULL REFERENCES kb.chunks(id),
    title_snapshot text NOT NULL,
    url_snapshot text,
    PRIMARY KEY (message_id, ordinal)
);
CREATE INDEX message_sources_chunk_idx ON app.message_sources(chunk_id);
-- Исторические источники неизменяемы. Старые chunks нельзя удалять, пока есть FK.

CREATE TABLE app.reminders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES app.users(id),
    conversation_id uuid REFERENCES app.conversations(id),
    material_id uuid REFERENCES kb.materials(id), -- версия карточки на момент создания
    text text NOT NULL CHECK (length(btrim(text)) > 0),
    timezone text NOT NULL,
    local_start timestamp without time zone NOT NULL,
    repeat_kind text NOT NULL CHECK (repeat_kind IN ('once', 'daily', 'weekly')),
    interval_n smallint NOT NULL DEFAULT 1 CHECK (interval_n BETWEEN 1 AND 52),
    next_run_at timestamptz,
    status text NOT NULL DEFAULT 'active' CHECK (status IN (
        'active', 'paused', 'cancelled', 'completed', 'expired', 'blocked')),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    confirmation_key text NOT NULL UNIQUE, -- стабильный user_id + draft_id, НЕ update_id
    date_origin text NOT NULL CHECK (date_origin IN ('user', 'verified_source')),
    date_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    confirmed_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'active' OR next_run_at IS NOT NULL),
    CHECK (repeat_kind <> 'once' OR interval_n = 1)
);
CREATE INDEX reminders_due_idx ON app.reminders(next_run_at, id) WHERE status = 'active';
CREATE INDEX reminders_user_idx ON app.reminders(user_id, status);
-- Будущее время, валидная зона и DST проверяются сервисом; CHECK с now() не используется.

CREATE TABLE app.deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dedupe_key text NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('reply', 'reminder')),
    user_id uuid NOT NULL REFERENCES app.users(id),
    target_chat_id bigint NOT NULL,
    message_id uuid REFERENCES app.messages(id),
    part_no smallint NOT NULL DEFAULT 0 CHECK (part_no >= 0),
    reminder_id uuid REFERENCES app.reminders(id),
    reminder_revision integer,
    occurrence_at timestamptz,
    text text NOT NULL,
    reply_markup jsonb,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'sent', 'cancelled', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lock_token uuid,
    locked_until timestamptz,
    telegram_message_id bigint,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    UNIQUE (message_id, part_no),
    UNIQUE (reminder_id, reminder_revision, occurrence_at),
    CHECK (
        (kind = 'reply' AND message_id IS NOT NULL AND reminder_id IS NULL
         AND reminder_revision IS NULL AND occurrence_at IS NULL)
        OR
        (kind = 'reminder' AND message_id IS NULL AND reminder_id IS NOT NULL
         AND reminder_revision IS NOT NULL AND reminder_revision > 0 AND occurrence_at IS NOT NULL)
    ),
    CHECK ((status = 'leased' AND lock_token IS NOT NULL AND locked_until IS NOT NULL)
        OR (status <> 'leased' AND lock_token IS NULL AND locked_until IS NULL)),
    CHECK (status <> 'sent' OR (sent_at IS NOT NULL AND telegram_message_id IS NOT NULL))
);
CREATE INDEX deliveries_due_idx ON app.deliveries(available_at, id) WHERE status = 'pending';
CREATE INDEX deliveries_lease_idx ON app.deliveries(locked_until) WHERE status = 'leased';
CREATE INDEX deliveries_reminder_idx ON app.deliveries(reminder_id, reminder_revision);

CREATE TABLE app.events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES app.users(id),
    conversation_id uuid REFERENCES app.conversations(id),
    message_id uuid REFERENCES app.messages(id),
    material_id uuid REFERENCES kb.materials(id),
    event_type text NOT NULL,
    dedupe_key text UNIQUE,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX events_type_time_idx ON app.events(event_type, occurred_at);
CREATE INDEX events_user_time_idx ON app.events(user_id, occurred_at);
COMMIT;

-- Права задаются инфраструктурными миграциями под реальные имена ролей:
-- source_reader: CONNECT alfa_source + SELECT только нужных таблиц.
-- ingest_writer: kb read/write; app недоступна.
-- runtime: kb SELECT; app DML; без DDL и без подключения к alfa_source.
-- migration_owner: DDL; его пароль не используется приложением.

-- Пример retrieval: :run_id фиксируется один раз в начале запроса.
-- :query_embedding и остальные параметры передаются драйвером, не через f-string.
-- SELECT c.id, c.text, m.id AS material_id, m.title, m.url,
--        c.embedding <=> CAST(:query_embedding AS vector) AS distance
-- FROM kb.chunks c
-- JOIN kb.materials m ON m.id = c.material_id AND m.run_id = c.run_id
-- WHERE c.run_id = :run_id AND c.purpose = 'knowledge'
--   AND m.is_active AND m.answer_allowed
--   AND (:uses_external_llm = false OR m.external_allowed)
-- ORDER BY c.embedding <=> CAST(:query_embedding AS vector)
-- LIMIT :limit;

-- Claim delivery (параметры :lease_until, :token формирует сервис):
-- BEGIN;
-- WITH picked AS (
--   SELECT id FROM app.deliveries
--   WHERE status = 'pending' AND available_at <= now()
--   ORDER BY available_at, id LIMIT 20 FOR UPDATE SKIP LOCKED
-- )
-- UPDATE app.deliveries d SET status = 'leased', lock_token = :token,
--   locked_until = :lease_until, attempts = attempts + 1
-- FROM picked p WHERE d.id = p.id RETURNING d.*;
-- COMMIT;
-- Вызов Telegram после COMMIT. Для UPDATE результата требуется id + lock_token.
-- Lease должен превышать сетевой timeout с запасом; активный lease продлевается.
-- Истёкший lease: вернуть pending и очистить lock_token/locked_until одной операцией.
