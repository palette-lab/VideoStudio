"""Chat: runs belong to a conversation (a request and its follow-ups)."""

from alembic import op
import sqlalchemy as sa

revision = "002_conversations"
down_revision = "001_init"


def upgrade() -> None:
    op.add_column("palette_video__runs", sa.Column("conversation_id", sa.String(32), nullable=False, server_default=""))
    op.create_index("ix_palette_video__runs_conversation", "palette_video__runs", ["conversation_id"])
    op.execute("UPDATE palette_video__runs SET conversation_id = id WHERE conversation_id = ''")


def downgrade() -> None:
    op.drop_index("ix_palette_video__runs_conversation", table_name="palette_video__runs")
    op.drop_column("palette_video__runs", "conversation_id")
