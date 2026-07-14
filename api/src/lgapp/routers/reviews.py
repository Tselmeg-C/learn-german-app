import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from lgapp.auth import CurrentUser
from lgapp.deps import SessionDep
from lgapp.errors import ProblemError
from lgapp.schemas.api import (
    CardOut,
    CardStateOut,
    QueueCardOut,
    QueueOut,
    ReviewBatchIn,
    ReviewBatchOut,
)
from lgapp.services import queue
from lgapp.services.reviews import ReviewSubmission, UnknownCardError, submit_reviews

router = APIRouter(prefix="/v1/reviews", tags=["reviews"])

# A review timestamped comfortably in the future is a broken client clock, not a real
# answer. Accepting it would schedule the card into that future and strand it there.
MAX_CLOCK_SKEW = timedelta(minutes=5)


@router.get("/queue", summary="Cards to review now")
async def get_queue(
    user: CurrentUser,
    session: SessionDep,
    deck_id: uuid.UUID | None = None,
    limit: int = Query(default=queue.DEFAULT_QUEUE_LIMIT, ge=1, le=200),
) -> QueueOut:
    now = datetime.now(UTC)
    built = await queue.build_queue(session, user, now=now, deck_id=deck_id, limit=limit)
    return QueueOut(
        cards=[
            QueueCardOut(
                card=CardOut.model_validate(user_card.card),
                state=user_card.state,
                due=user_card.due,
                reps=user_card.reps,
                is_new=user_card.reps == 0,
            )
            for user_card in built.cards
        ],
        due_total=built.due_total,
        new_remaining_today=built.new_remaining_today,
    )


@router.post("", summary="Submit a batch of reviews")
async def post_reviews(
    payload: ReviewBatchIn, user: CurrentUser, session: SessionDep
) -> ReviewBatchOut:
    """Record reviews and return the authoritative card states.

    Accepts a batch because an offline client drains its outbox at once, and is
    idempotent on review id so a retry after a timeout cannot double-apply.
    """
    horizon = datetime.now(UTC) + MAX_CLOCK_SKEW
    if future := [r.id for r in payload.reviews if r.reviewed_at > horizon]:
        raise ProblemError(
            status=422,
            title="Review timestamp is in the future",
            detail=("reviewed_at must not be in the future; the device clock is probably wrong."),
            review_ids=[str(i) for i in future],
        )

    submissions = [
        ReviewSubmission(
            id=r.id,
            card_id=r.card_id,
            rating=r.rating,
            reviewed_at=r.reviewed_at,
            duration_ms=r.duration_ms,
        )
        for r in payload.reviews
    ]

    try:
        cards = await submit_reviews(session, user, submissions)
    except UnknownCardError as exc:
        raise ProblemError(
            status=404,
            title="Unknown card",
            detail="Some reviews reference cards you are not subscribed to.",
            card_ids=sorted(str(c) for c in exc.card_ids),
        ) from exc

    return ReviewBatchOut(cards=[CardStateOut.model_validate(card) for card in cards])
