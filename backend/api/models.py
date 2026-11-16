"""App-owned data: one row per routing run, scoped to the organisation by Palette (RLS)."""

from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from palette_sdk.db import OrgScopedTable


class Run(OrgScopedTable):
    __tablename__ = "palette_video__runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    conversation_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    prompt: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[str] = mapped_column(Text, default="{}")  # the full run document (JSON)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)
