"""Initial migration: routing runs, with organisation row-level security."""

from alembic import op
import sqlalchemy as sa

from palette_sdk.db import ensure_org_rls

revision = "001_init"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "palette_video__runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_palette_video__runs_org", "palette_video__runs", ["organization_id"])
    op.create_index("ix_palette_video__runs_created", "palette_video__runs", ["created_at"])
    ensure_org_rls(op, "palette_video__runs")


def downgrade() -> None:
    op.drop_index("ix_palette_video__runs_created", table_name="palette_video__runs")
    op.drop_index("ix_palette_video__runs_org", table_name="palette_video__runs")
    op.drop_table("palette_video__runs")
