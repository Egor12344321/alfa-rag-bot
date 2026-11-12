"""Require verified HTTPS URLs and nonempty summaries for catalog cards."""

from alembic import op

revision = "0002_catalog_policy"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "materials_catalog_url_summary_check",
        "materials",
        "NOT recommend_allowed OR (url ~* '^https://' AND length(btrim(summary)) > 0)",
        schema="kb",
    )


def downgrade() -> None:
    op.drop_constraint(
        "materials_catalog_url_summary_check", "materials", schema="kb", type_="check"
    )
