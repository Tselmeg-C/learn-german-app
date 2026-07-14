"""Request and response models.

These are the contract the web client generates its types from, so they are deliberately
explicit rather than dumping ORM objects.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from lgapp.models import CardState, Rating

MAX_REVIEW_BATCH = 500


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    german: str
    english: str
    part_of_speech: str | None = None
    article: str | None = None
    plural: str | None = None
    example_de: str | None = None
    example_en: str | None = None
    tags: list[str] = Field(default_factory=list)


class DeckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    cefr_level: str | None = None
    card_count: int
    subscribed: bool
    due_count: int = 0


class SubscribeOut(BaseModel):
    deck_id: uuid.UUID
    cards_added: int


class QueueCardOut(BaseModel):
    card: CardOut
    state: CardState
    due: datetime
    reps: int
    is_new: bool


class QueueOut(BaseModel):
    cards: list[QueueCardOut]
    due_total: int
    new_remaining_today: int


class ReviewIn(BaseModel):
    """One review from the client.

    `id` is generated client-side at review time so that a retry after a network failure
    carries the same id and is absorbed rather than double-counted. `reviewed_at` is when
    the learner actually answered, which for an offline review is well before we see it.
    """

    id: uuid.UUID
    card_id: uuid.UUID
    rating: Rating
    reviewed_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)


class ReviewBatchIn(BaseModel):
    reviews: list[ReviewIn] = Field(min_length=1, max_length=MAX_REVIEW_BATCH)


class CardStateOut(BaseModel):
    """Authoritative post-sync state. The client overwrites its local copy with this."""

    model_config = ConfigDict(from_attributes=True)

    card_id: uuid.UUID
    state: CardState
    due: datetime
    stability: float | None = None
    difficulty: float | None = None
    reps: int
    lapses: int
    last_review: datetime | None = None


class ReviewBatchOut(BaseModel):
    cards: list[CardStateOut]


class StatsOut(BaseModel):
    reviews_total: int
    reviews_today: int
    retention_rate: float | None = Field(
        default=None, description="Share of mature reviews graded better than Again."
    )
    streak_days: int
    cards_by_state: dict[str, int]
    due_today: int
    reviews_per_day: list["DayCount"]


class DayCount(BaseModel):
    day: str
    count: int


StatsOut.model_rebuild()
