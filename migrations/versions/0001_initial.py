"""Начальная рабочая схема kb/app, 15 таблиц. Source дамп не меняется."""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DDL = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE SCHEMA IF NOT EXISTS kb",
    "CREATE SCHEMA IF NOT EXISTS app",
    "CREATE TABLE kb.ingestion_runs (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    status text NOT NULL DEFAULT 'building'\n        CHECK (status IN ('building', 'ready', 'failed')),\n    source_fingerprint text NOT NULL,\n    embedding_model text NOT NULL,\n    embedding_revision text NOT NULL,\n    embedding_dim integer NOT NULL CHECK (embedding_dim = 384),\n    pipeline_version text NOT NULL,\n    mapping_version text NOT NULL,\n    policy_version text NOT NULL,\n    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,\n    started_at timestamptz NOT NULL DEFAULT now(),\n    finished_at timestamptz\n)",
    "CREATE TABLE kb.index_state (\n    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),\n    active_run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id)\n)",
    "CREATE TABLE kb.materials (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id),\n    source_table text NOT NULL,\n    source_id text NOT NULL, -- исходный идентификатор сохраняется без предположения о типе\n    kind text NOT NULL CHECK (kind IN ('article', 'document', 'term', 'course', 'webinar')),\n    title text NOT NULL CHECK (length(btrim(title)) > 0),\n    summary text NOT NULL DEFAULT '', -- только разрешённое публичное описание\n    url text CHECK (url IS NULL OR url ~* '^https?://'),\n    access_type text NOT NULL DEFAULT 'unknown'\n        CHECK (access_type IN ('free', 'paid', 'subscription', 'unknown')),\n    price numeric(14,2) CHECK (price IS NULL OR price >= 0),\n    currency text,\n    price_note text,\n    is_active boolean NOT NULL DEFAULT false,\n    answer_allowed boolean NOT NULL DEFAULT false,\n    recommend_allowed boolean NOT NULL DEFAULT false,\n    external_allowed boolean NOT NULL DEFAULT false,\n    source_modified_at timestamptz,\n    content_hash text NOT NULL,\n    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,\n    created_at timestamptz NOT NULL DEFAULT now(),\n    UNIQUE (run_id, source_table, source_id),\n    UNIQUE (run_id, id),\n    CHECK (NOT answer_allowed OR (is_active AND access_type = 'free')),\n    CHECK (NOT recommend_allowed OR (is_active AND url IS NOT NULL))\n)",
    "CREATE TABLE kb.chunks (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    run_id uuid NOT NULL,\n    material_id uuid NOT NULL,\n    purpose text NOT NULL CHECK (purpose IN ('knowledge', 'catalog')),\n    chunk_no integer NOT NULL CHECK (chunk_no >= 0),\n    text text NOT NULL CHECK (length(btrim(text)) > 0),\n    section_path text,\n    source_blocks jsonb NOT NULL DEFAULT '[]'::jsonb,\n    token_count integer NOT NULL CHECK (token_count BETWEEN 1 AND 512),\n    content_hash text NOT NULL,\n    embedding vector(384) NOT NULL,\n    search_tsv tsvector GENERATED ALWAYS AS\n        (to_tsvector('russian'::regconfig, text)) STORED,\n    UNIQUE (material_id, purpose, chunk_no),\n    FOREIGN KEY (run_id, material_id) REFERENCES kb.materials(run_id, id)\n)",
    "CREATE INDEX chunks_scope_idx ON kb.chunks(run_id, purpose, material_id)",
    "CREATE INDEX chunks_fts_idx ON kb.chunks USING gin(search_tsv)",
    "CREATE TABLE kb.topics (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id),\n    source_id text NOT NULL,\n    title text NOT NULL,\n    UNIQUE (run_id, source_id),\n    UNIQUE (run_id, id)\n)",
    "CREATE TABLE kb.material_topics (\n    run_id uuid NOT NULL,\n    material_id uuid NOT NULL,\n    topic_id uuid NOT NULL,\n    PRIMARY KEY (material_id, topic_id),\n    FOREIGN KEY (run_id, material_id) REFERENCES kb.materials(run_id, id),\n    FOREIGN KEY (run_id, topic_id) REFERENCES kb.topics(run_id, id)\n)",
    "CREATE INDEX material_topics_topic_idx ON kb.material_topics(topic_id, material_id)",
    "CREATE TABLE kb.material_relations (\n    run_id uuid NOT NULL,\n    from_material_id uuid NOT NULL,\n    to_material_id uuid NOT NULL,\n    relation_type text NOT NULL DEFAULT 'recommended',\n    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,\n    PRIMARY KEY (from_material_id, to_material_id, relation_type),\n    FOREIGN KEY (run_id, from_material_id) REFERENCES kb.materials(run_id, id),\n    FOREIGN KEY (run_id, to_material_id) REFERENCES kb.materials(run_id, id),\n    CHECK (from_material_id <> to_material_id)\n)",
    "CREATE TABLE app.users (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    telegram_user_id bigint NOT NULL UNIQUE,\n    telegram_chat_id bigint NOT NULL UNIQUE, -- MVP: только личные чаты\n    timezone text, -- IANA zone, проверяется ZoneInfo в приложении\n    profile jsonb NOT NULL DEFAULT '{}'::jsonb,\n    delivery_enabled boolean NOT NULL DEFAULT true,\n    created_at timestamptz NOT NULL DEFAULT now(),\n    updated_at timestamptz NOT NULL DEFAULT now(),\n    CHECK (jsonb_typeof(profile) = 'object')\n)",
    "CREATE TABLE app.conversations (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    user_id uuid NOT NULL REFERENCES app.users(id),\n    state text NOT NULL DEFAULT 'ready' CHECK (state IN (\n        'ready', 'awaiting_question_clarification',\n        'awaiting_reminder_details', 'awaiting_reminder_confirmation')),\n    pending_payload jsonb NOT NULL DEFAULT '{}'::jsonb,\n    clarification_count smallint NOT NULL DEFAULT 0\n        CHECK (clarification_count BETWEEN 0 AND 1),\n    version integer NOT NULL DEFAULT 1 CHECK (version > 0),\n    created_at timestamptz NOT NULL DEFAULT now(),\n    updated_at timestamptz NOT NULL DEFAULT now(),\n    closed_at timestamptz\n)",
    "CREATE UNIQUE INDEX one_live_conversation_idx ON app.conversations(user_id)\n    WHERE closed_at IS NULL",
    "CREATE TABLE app.telegram_updates (\n    update_id bigint PRIMARY KEY,\n    user_id uuid NOT NULL REFERENCES app.users(id),\n    chat_id bigint NOT NULL,\n    payload jsonb NOT NULL, -- нормализованные поля, без ненужного профиля Telegram\n    status text NOT NULL DEFAULT 'pending'\n        CHECK (status IN ('pending', 'processing', 'done', 'failed')),\n    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),\n    available_at timestamptz NOT NULL DEFAULT now(),\n    lock_token uuid,\n    locked_until timestamptz,\n    last_error_code text,\n    received_at timestamptz NOT NULL DEFAULT now(),\n    finished_at timestamptz,\n    CHECK ((status = 'processing' AND lock_token IS NOT NULL AND locked_until IS NOT NULL)\n        OR (status <> 'processing' AND lock_token IS NULL AND locked_until IS NULL))\n)",
    "CREATE INDEX updates_due_idx ON app.telegram_updates(available_at, update_id)\n    WHERE status = 'pending'",
    "CREATE UNIQUE INDEX one_processing_update_per_chat_idx ON app.telegram_updates(chat_id)\n    WHERE status = 'processing'",
    "CREATE INDEX updates_lease_idx ON app.telegram_updates(locked_until)\n    WHERE status = 'processing'",
    "CREATE TABLE app.messages (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    conversation_id uuid NOT NULL REFERENCES app.conversations(id),\n    origin_update_id bigint REFERENCES app.telegram_updates(update_id),\n    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),\n    text text NOT NULL,\n    outcome text,\n    retrieval_run_id uuid REFERENCES kb.ingestion_runs(id),\n    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,\n    created_at timestamptz NOT NULL DEFAULT now(),\n    UNIQUE (origin_update_id, role)\n)",
    "CREATE INDEX messages_history_idx ON app.messages(conversation_id, created_at, id)",
    "CREATE TABLE app.message_sources (\n    message_id uuid NOT NULL REFERENCES app.messages(id),\n    ordinal smallint NOT NULL CHECK (ordinal > 0),\n    chunk_id uuid NOT NULL REFERENCES kb.chunks(id),\n    title_snapshot text NOT NULL,\n    url_snapshot text,\n    PRIMARY KEY (message_id, ordinal)\n)",
    "CREATE INDEX message_sources_chunk_idx ON app.message_sources(chunk_id)",
    "CREATE TABLE app.reminders (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    user_id uuid NOT NULL REFERENCES app.users(id),\n    conversation_id uuid REFERENCES app.conversations(id),\n    material_id uuid REFERENCES kb.materials(id), -- версия карточки на момент создания\n    text text NOT NULL CHECK (length(btrim(text)) > 0),\n    timezone text NOT NULL,\n    local_start timestamp without time zone NOT NULL,\n    repeat_kind text NOT NULL CHECK (repeat_kind IN ('once', 'daily', 'weekly')),\n    interval_n smallint NOT NULL DEFAULT 1 CHECK (interval_n BETWEEN 1 AND 52),\n    next_run_at timestamptz,\n    status text NOT NULL DEFAULT 'active' CHECK (status IN (\n        'active', 'paused', 'cancelled', 'completed', 'expired', 'blocked')),\n    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),\n    confirmation_key text NOT NULL UNIQUE, -- стабильный user_id + draft_id, НЕ update_id\n    date_origin text NOT NULL CHECK (date_origin IN ('user', 'verified_source')),\n    date_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,\n    confirmed_at timestamptz NOT NULL DEFAULT now(),\n    updated_at timestamptz NOT NULL DEFAULT now(),\n    CHECK (status <> 'active' OR next_run_at IS NOT NULL),\n    CHECK (repeat_kind <> 'once' OR interval_n = 1)\n)",
    "CREATE INDEX reminders_due_idx ON app.reminders(next_run_at, id) WHERE status = 'active'",
    "CREATE INDEX reminders_user_idx ON app.reminders(user_id, status)",
    "CREATE TABLE app.deliveries (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    dedupe_key text NOT NULL UNIQUE,\n    kind text NOT NULL CHECK (kind IN ('reply', 'reminder')),\n    user_id uuid NOT NULL REFERENCES app.users(id),\n    target_chat_id bigint NOT NULL,\n    message_id uuid REFERENCES app.messages(id),\n    part_no smallint NOT NULL DEFAULT 0 CHECK (part_no >= 0),\n    reminder_id uuid REFERENCES app.reminders(id),\n    reminder_revision integer,\n    occurrence_at timestamptz,\n    text text NOT NULL,\n    reply_markup jsonb,\n    status text NOT NULL DEFAULT 'pending'\n        CHECK (status IN ('pending', 'leased', 'sent', 'cancelled', 'dead')),\n    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),\n    available_at timestamptz NOT NULL DEFAULT now(),\n    lock_token uuid,\n    locked_until timestamptz,\n    telegram_message_id bigint,\n    last_error_code text,\n    created_at timestamptz NOT NULL DEFAULT now(),\n    sent_at timestamptz,\n    UNIQUE (message_id, part_no),\n    UNIQUE (reminder_id, reminder_revision, occurrence_at),\n    CHECK (\n        (kind = 'reply' AND message_id IS NOT NULL AND reminder_id IS NULL\n         AND reminder_revision IS NULL AND occurrence_at IS NULL)\n        OR\n        (kind = 'reminder' AND message_id IS NULL AND reminder_id IS NOT NULL\n         AND reminder_revision IS NOT NULL AND reminder_revision > 0 AND occurrence_at IS NOT NULL)\n    ),\n    CHECK ((status = 'leased' AND lock_token IS NOT NULL AND locked_until IS NOT NULL)\n        OR (status <> 'leased' AND lock_token IS NULL AND locked_until IS NULL)),\n    CHECK (status <> 'sent' OR (sent_at IS NOT NULL AND telegram_message_id IS NOT NULL))\n)",
    "CREATE INDEX deliveries_due_idx ON app.deliveries(available_at, id) WHERE status = 'pending'",
    "CREATE INDEX deliveries_lease_idx ON app.deliveries(locked_until) WHERE status = 'leased'",
    "CREATE INDEX deliveries_reminder_idx ON app.deliveries(reminder_id, reminder_revision)",
    "CREATE TABLE app.events (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    user_id uuid REFERENCES app.users(id),\n    conversation_id uuid REFERENCES app.conversations(id),\n    message_id uuid REFERENCES app.messages(id),\n    material_id uuid REFERENCES kb.materials(id),\n    event_type text NOT NULL,\n    dedupe_key text UNIQUE,\n    occurred_at timestamptz NOT NULL DEFAULT now(),\n    metadata jsonb NOT NULL DEFAULT '{}'::jsonb\n)",
    "CREATE INDEX events_type_time_idx ON app.events(event_type, occurred_at)",
    "CREATE INDEX events_user_time_idx ON app.events(user_id, occurred_at)",
)


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)


def downgrade() -> None:
    # Обратный порядок зависимостей. Extension и namespace остаются.
    tables = (
        "app.events",
        "app.deliveries",
        "app.reminders",
        "app.message_sources",
        "app.messages",
        "app.telegram_updates",
        "app.conversations",
        "app.users",
        "kb.material_relations",
        "kb.material_topics",
        "kb.topics",
        "kb.chunks",
        "kb.materials",
        "kb.index_state",
        "kb.ingestion_runs",
    )
    for name in tables:
        op.execute(f"DROP TABLE {name}")
