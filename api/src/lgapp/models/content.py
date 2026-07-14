"""Content tables — owned by the importer, shared across all users.

Nothing here is per-user. An import may freely insert and update these rows without ever
touching a learner's progress; that separation is what makes content re-importable.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lgapp.models.base import Base, TimestampMixin


class ImportStatus(enum.StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Deck(TimestampMixin, Base):
    __tablename__ = "decks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # CEFR level: A1..C2. Free-form rather than an enum — content may not map cleanly.
    cefr_level: Mapped[str | None] = mapped_column(String(2))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    cards: Mapped[list["Card"]] = relationship(back_populates="deck", cascade="all, delete-orphan")


class Card(TimestampMixin, Base):
    __tablename__ = "cards"
    __table_args__ = (
        # The importer's idempotency key: re-importing a row updates rather than duplicates.
        UniqueConstraint("deck_id", "external_id", name="uq_cards_deck_id_external_id"),
        Index("ix_cards_deck_id_position", "deck_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), nullable=False
    )
    # Stable identifier from the source file. Content authors own this value.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    german: Mapped[str] = mapped_column(Text, nullable=False)
    english: Mapped[str] = mapped_column(Text, nullable=False)
    part_of_speech: Mapped[str | None] = mapped_column(String(32))
    # Nouns only: der/die/das, and the plural form.
    article: Mapped[str | None] = mapped_column(String(8))
    plural: Mapped[str | None] = mapped_column(Text)
    example_de: Mapped[str | None] = mapped_column(Text)
    example_en: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    deck: Mapped[Deck] = relationship(back_populates="cards")


class ImportBatch(Base):
    """Audit trail: one row per import run, so every content change is attributable."""

    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_at_set_iff_not_running",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deck_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("decks.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of the source file: identifies a re-run of byte-identical content.
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status", native_enum=True), nullable=False
    )
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
