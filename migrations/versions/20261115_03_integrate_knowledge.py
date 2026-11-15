"""Install the contributed knowledge schema; preserve the previous index."""

from alembic import op

revision = "20261115_03"
down_revision = "20261115_02"
branch_labels = None
depends_on = None

DDL = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE SCHEMA IF NOT EXISTS kb",
    "CREATE TABLE kb.ingestion_runs (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    status text NOT NULL DEFAULT 'building'\n        CHECK (status IN ('building', 'ready', 'failed')),\n    source_fingerprint text NOT NULL,\n    embedding_model text NOT NULL,\n    embedding_revision text NOT NULL,\n    embedding_dim integer NOT NULL CHECK (embedding_dim BETWEEN 1 AND 8192),\n    pipeline_version text NOT NULL,\n    mapping_version text NOT NULL,\n    policy_version text NOT NULL,\n    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,\n    started_at timestamptz NOT NULL DEFAULT now(),\n    finished_at timestamptz\n)",
    "CREATE TABLE kb.index_state (\n    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),\n    active_run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id)\n)",
    "CREATE TABLE kb.materials (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id),\n    source_table text NOT NULL,\n    source_id text NOT NULL, -- исходный идентификатор сохраняется без предположения о типе\n    kind text NOT NULL CHECK (kind IN ('article', 'document', 'term', 'course', 'webinar')),\n    title text NOT NULL CHECK (length(btrim(title)) > 0),\n    summary text NOT NULL DEFAULT '', -- только разрешённое публичное описание\n    url text CHECK (url IS NULL OR url ~* '^https?://'),\n    access_type text NOT NULL DEFAULT 'unknown'\n        CHECK (access_type IN ('free', 'paid', 'subscription', 'unknown')),\n    price numeric(14,2) CHECK (price IS NULL OR price >= 0),\n    currency text,\n    price_note text,\n    is_active boolean NOT NULL DEFAULT false,\n    answer_allowed boolean NOT NULL DEFAULT false,\n    recommend_allowed boolean NOT NULL DEFAULT false,\n    external_allowed boolean NOT NULL DEFAULT false,\n    source_modified_at timestamptz,\n    content_hash text NOT NULL,\n    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,\n    created_at timestamptz NOT NULL DEFAULT now(),\n    UNIQUE (run_id, source_table, source_id),\n    UNIQUE (run_id, id),\n    CHECK (NOT answer_allowed OR (is_active AND access_type = 'free')),\n    CHECK (NOT recommend_allowed OR (is_active AND url IS NOT NULL))\n)",
    "CREATE TABLE kb.chunks (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    run_id uuid NOT NULL,\n    material_id uuid NOT NULL,\n    purpose text NOT NULL CHECK (purpose IN ('knowledge', 'catalog')),\n    chunk_no integer NOT NULL CHECK (chunk_no >= 0),\n    text text NOT NULL CHECK (length(btrim(text)) > 0),\n    section_path text,\n    source_blocks jsonb NOT NULL DEFAULT '[]'::jsonb,\n    token_count integer NOT NULL CHECK (token_count BETWEEN 0 AND 8192),\n    content_hash text NOT NULL,\n    embedding vector NOT NULL,\n    search_tsv tsvector GENERATED ALWAYS AS\n        (to_tsvector('russian'::regconfig, text)) STORED,\n    UNIQUE (material_id, purpose, chunk_no),\n    FOREIGN KEY (run_id, material_id) REFERENCES kb.materials(run_id, id)\n)",
    "CREATE INDEX chunks_scope_idx ON kb.chunks(run_id, purpose, material_id)",
    "CREATE INDEX chunks_fts_idx ON kb.chunks USING gin(search_tsv)",
    "CREATE TABLE kb.topics (\n    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n    run_id uuid NOT NULL REFERENCES kb.ingestion_runs(id),\n    source_id text NOT NULL,\n    title text NOT NULL,\n    UNIQUE (run_id, source_id),\n    UNIQUE (run_id, id)\n)",
    "CREATE TABLE kb.material_topics (\n    run_id uuid NOT NULL,\n    material_id uuid NOT NULL,\n    topic_id uuid NOT NULL,\n    PRIMARY KEY (material_id, topic_id),\n    FOREIGN KEY (run_id, material_id) REFERENCES kb.materials(run_id, id),\n    FOREIGN KEY (run_id, topic_id) REFERENCES kb.topics(run_id, id)\n)",
    "CREATE INDEX material_topics_topic_idx ON kb.material_topics(topic_id, material_id)",
    "CREATE TABLE kb.material_relations (\n    run_id uuid NOT NULL,\n    from_material_id uuid NOT NULL,\n    to_material_id uuid NOT NULL,\n    relation_type text NOT NULL DEFAULT 'recommended',\n    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,\n    PRIMARY KEY (from_material_id, to_material_id, relation_type),\n    FOREIGN KEY (run_id, from_material_id) REFERENCES kb.materials(run_id, id),\n    FOREIGN KEY (run_id, to_material_id) REFERENCES kb.materials(run_id, id),\n    CHECK (from_material_id <> to_material_id)\n)",
    "ALTER TABLE kb.materials ADD CONSTRAINT materials_catalog_url_summary_check CHECK (NOT recommend_allowed OR (url ~* '^https://' AND length(btrim(summary)) > 0))",
)


def upgrade():
    op.execute("ALTER SCHEMA kb RENAME TO kb_v04")
    for statement in DDL:
        op.execute(statement)


def downgrade():
    op.execute("DROP SCHEMA kb CASCADE")
    op.execute("ALTER SCHEMA kb_v04 RENAME TO kb")
