"""Per-user tables — never written by the importer.

`review_logs` is the source of truth for what a learner did. `user_cards` holds the FSRS
state derived from replaying that log, cached so the due-queue query stays a single index
scan. Any time a review arrives out of order, the cache is rebuilt from the log.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lgapp.models.base import Base, TimestampMixin
from lgapp.models.content import Card, Deck


class CardState(enum.StrEnum):
    """Mirrors FSRS's own card states; mapped in services/scheduler.py."""

    learning = "learning"
    review = "review"
    relearning = "relearning"


class Rating(enum.IntEnum):
    """FSRS grades. Stored as a smallint so ordering comparisons stay cheap."""

    again = 1
    hard = 2
    good = 3
    easy = 4


class User(TimestampMixin, Base):
    """Local projection of a Supabase Auth user.

    `id` is the Supabase `sub` claim — Supabase remains the identity source of truth and
    this row is upserted on first authenticated request. We keep it so that other tables
    have something to reference and so stats queries never leave the database.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)
    # IANA name, e.g. "Europe/Berlin". Drives day boundaries for streaks and daily limits.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    # Per-user FSRS weights. NULL means the library defaults; populated later if we fit
    # per-user parameters from review history.
    fsrs_parameters: Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    desired_retention: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.9")

    __table_args__ = (
        CheckConstraint(
            "desired_retention > 0 AND desired_retention < 1",
            name="desired_retention_is_a_probability",
        ),
    )


class UserDeck(TimestampMixin, Base):
    __tablename__ = "user_decks"
    __table_args__ = (UniqueConstraint("user_id", "deck_id", name="uq_user_decks_user_id_deck_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), nullable=False
    )
    daily_new_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    subscribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    deck: Mapped[Deck] = relationship()


class UserCard(TimestampMixin, Base):
    """FSRS state for one card and one learner — a cache derived from `review_logs`."""

    __tablename__ = "user_cards"
    __table_args__ = (
        # The hot path: "what is due for this user right now", an index-only range scan
        # regardless of how large the table grows.
        Index("ix_user_cards_user_id_due", "user_id", "due"),
        Index("ix_user_cards_user_id_deck_id", "user_id", "deck_id"),
        CheckConstraint("stability IS NULL OR stability > 0", name="stability_is_positive"),
        CheckConstraint(
            "difficulty IS NULL OR (difficulty >= 1 AND difficulty <= 10)",
            name="difficulty_within_fsrs_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    # Denormalised from cards.deck_id so per-deck queues don't need a join. Content is
    # never reassigned between decks, so this cannot drift.
    deck_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), nullable=False
    )

    state: Mapped[CardState] = mapped_column(
        Enum(CardState, name="card_state", native_enum=True),
        nullable=False,
        server_default=CardState.learning.value,
    )
    due: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL until the first review — FSRS has no memory state for an unseen card.
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    step: Mapped[int | None] = mapped_column(Integer)
    last_review: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reps: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    card: Mapped[Card] = relationship()


class ReviewLog(Base):
    """Append-only record of every review. The source of truth.

    `id` is a client-generated UUIDv7, which makes submission idempotent: a retry after a
    network timeout carries the same id and conflicts away harmlessly. Being v7, it also
    sorts by creation time, so the table stays insert-ordered on disk.
    """

    __tablename__ = "review_logs"
    __table_args__ = (
        # Replay reads one card's whole history in review order.
        Index("ix_review_logs_user_id_card_id_reviewed_at", "user_id", "card_id", "reviewed_at"),
        # Stats scan a user's reviews over a time window.
        Index("ix_review_logs_user_id_reviewed_at", "user_id", "reviewed_at"),
        CheckConstraint("rating BETWEEN 1 AND 4", name="rating_within_fsrs_range"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_is_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[Rating] = mapped_column(SmallInteger, nullable=False)
    # When the learner actually answered — may be well before it reached us, if offline.
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # When the server accepted it. reviewed_at != created_at reveals offline submissions.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
