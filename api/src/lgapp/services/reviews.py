"""Review submission and the offline sync rules.

The design in one paragraph: `review_logs` is the source of truth and `user_cards` caches
the FSRS state derived from it. Submitting a review appends to the log, then brings the
cache back in line. When the review is the newest one for its card — the online case — we
can step the cache forward, which is O(1). When it arrives late, which offline guarantees,
stepping forward would apply an old review on top of newer state and corrupt the schedule,
so we rebuild that card from its log instead.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.models import Rating, ReviewLog, User, UserCard
from lgapp.services import scheduler
from lgapp.services.scheduler import MemoryState, Review, SchedulerConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReviewSubmission:
    """One review as the client reports it.

    `id` is the client's UUIDv7, generated at review time. It is the idempotency key: a
    retry after a network timeout carries the same id and is absorbed rather than
    double-counted.
    """

    id: uuid.UUID
    card_id: uuid.UUID
    rating: Rating
    reviewed_at: datetime
    duration_ms: int | None = None


class UnknownCardError(Exception):
    """A review for a card the learner has not subscribed to."""

    def __init__(self, card_ids: set[uuid.UUID]) -> None:
        self.card_ids = card_ids
        super().__init__(f"not subscribed to card(s): {sorted(map(str, card_ids))}")


def config_for(user: User) -> SchedulerConfig:
    return SchedulerConfig(
        parameters=user.fsrs_parameters,
        desired_retention=user.desired_retention,
    )


def _to_state(card: UserCard) -> MemoryState:
    return MemoryState(
        state=card.state,
        due=card.due,
        stability=card.stability,
        difficulty=card.difficulty,
        step=card.step,
        last_review=card.last_review,
        reps=card.reps,
        lapses=card.lapses,
    )


def _write_state(card: UserCard, state: MemoryState) -> None:
    card.state = state.state
    card.due = state.due
    card.stability = state.stability
    card.difficulty = state.difficulty
    card.step = state.step
    card.last_review = state.last_review
    card.reps = state.reps
    card.lapses = state.lapses


async def submit_reviews(
    session: AsyncSession,
    user: User,
    submissions: list[ReviewSubmission],
) -> list[UserCard]:
    """Record a batch of reviews and return the resulting card states.

    Safe to call twice with the same batch: the log insert ignores ids it already has,
    and the cache is recomputed from the log rather than incremented, so nothing is
    double-applied.
    """
    if not submissions:
        return []

    card_ids = {s.card_id for s in submissions}

    # FOR UPDATE serialises concurrent batches touching the same card — two devices
    # syncing at once would otherwise interleave read-modify-write and lose one result.
    user_cards = {
        card.card_id: card
        for card in (
            await session.execute(
                select(UserCard)
                .where(UserCard.user_id == user.id, UserCard.card_id.in_(card_ids))
                .with_for_update()
            )
        ).scalars()
    }

    if unknown := card_ids - user_cards.keys():
        raise UnknownCardError(unknown)

    # ON CONFLICT DO NOTHING is what makes a retried batch harmless. RETURNING tells us
    # which ids were genuinely new, purely so we can report it.
    statement = insert(ReviewLog).values(
        [
            {
                "id": s.id,
                "user_id": user.id,
                "card_id": s.card_id,
                "rating": s.rating,
                "reviewed_at": s.reviewed_at,
                "duration_ms": s.duration_ms,
            }
            for s in submissions
        ]
    )
    accepted = (
        (
            await session.execute(
                statement.on_conflict_do_nothing(index_elements=[ReviewLog.id]).returning(
                    ReviewLog.id
                )
            )
        )
        .scalars()
        .all()
    )
    if duplicates := len(submissions) - len(accepted):
        log.info("ignored already-recorded reviews", extra={"count": duplicates})

    config = config_for(user)
    by_card: dict[uuid.UUID, list[ReviewSubmission]] = defaultdict(list)
    for submission in submissions:
        by_card[submission.card_id].append(submission)

    for card_id, batch in by_card.items():
        user_card = user_cards[card_id]
        await _resync_card(session, user, user_card, batch, config)

    await session.flush()
    return [user_cards[card_id] for card_id in by_card]


async def _resync_card(
    session: AsyncSession,
    user: User,
    user_card: UserCard,
    batch: list[ReviewSubmission],
    config: SchedulerConfig,
) -> None:
    """Bring one card's cached state back in line with its log."""
    earliest = min(s.reviewed_at for s in batch)
    is_late = user_card.last_review is not None and earliest <= user_card.last_review

    if is_late:
        # Rebuilding from the log is the only correct answer here, and it is also how we
        # absorb a re-submitted batch: the log already holds the reviews, so replaying
        # cannot double-apply them.
        log.info(
            "replaying card from log after out-of-order review",
            extra={"card_id": str(user_card.card_id), "reviewed_at": earliest.isoformat()},
        )
        await _replay_from_log(session, user, user_card, config)
        return

    state = _to_state(user_card)
    for submission in sorted(batch, key=lambda s: s.reviewed_at):
        state = scheduler.apply_review(
            state,
            Review(
                rating=submission.rating,
                reviewed_at=submission.reviewed_at,
                duration_ms=submission.duration_ms,
            ),
            config,
        )
    _write_state(user_card, state)


async def _replay_from_log(
    session: AsyncSession,
    user: User,
    user_card: UserCard,
    config: SchedulerConfig,
) -> None:
    rows = (
        (
            await session.execute(
                select(ReviewLog)
                .where(ReviewLog.user_id == user.id, ReviewLog.card_id == user_card.card_id)
                .order_by(ReviewLog.reviewed_at)
            )
        )
        .scalars()
        .all()
    )
    state = scheduler.replay(
        [
            Review(rating=r.rating, reviewed_at=r.reviewed_at, duration_ms=r.duration_ms)
            for r in rows
        ],
        config,
        created_at=user_card.created_at,
    )
    _write_state(user_card, state)
