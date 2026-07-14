"""Tests for the invariants the database itself enforces.

These are here because a check constraint that doesn't fire is indistinguishable from one
that does until something writes bad data.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.models import Card, CardState, Rating, ReviewLog, UserCard
from tests.factories import make_card, make_deck, make_user, make_user_card


async def test_deck_slug_is_unique(session: AsyncSession) -> None:
    await make_deck(session, slug="a1-basics")
    with pytest.raises(IntegrityError):
        await make_deck(session, slug="a1-basics")


async def test_card_external_id_is_unique_within_a_deck(session: AsyncSession) -> None:
    """The importer's idempotency key. Without this, re-import duplicates content."""
    deck = await make_deck(session)
    await make_card(session, deck, external_id="haus")
    with pytest.raises(IntegrityError):
        await make_card(session, deck, external_id="haus")


async def test_same_external_id_is_allowed_across_decks(session: AsyncSession) -> None:
    """Decks are authored independently; their external ids share no namespace."""
    deck_a = await make_deck(session)
    deck_b = await make_deck(session)
    await make_card(session, deck_a, external_id="haus")
    await make_card(session, deck_b, external_id="haus")  # must not raise


async def test_deleting_a_deck_cascades_to_its_cards(session: AsyncSession) -> None:
    deck = await make_deck(session)
    await make_card(session, deck)
    await session.delete(deck)
    await session.flush()
    remaining = (await session.execute(select(Card).where(Card.deck_id == deck.id))).all()
    assert remaining == []


async def test_rating_outside_the_fsrs_range_is_rejected(session: AsyncSession) -> None:
    user = await make_user(session)
    card = await make_card(session, await make_deck(session))
    session.add(
        ReviewLog(
            id=uuid.uuid4(),
            user_id=user.id,
            card_id=card.id,
            rating=7,  # FSRS only defines 1..4
            reviewed_at=datetime.now(UTC),
        )
    )
    with pytest.raises(DBAPIError):
        await session.flush()


async def test_review_log_id_is_the_idempotency_key(session: AsyncSession) -> None:
    """Re-submitting a review with the same id must collide, not duplicate.

    This is what makes an offline client's retry safe.
    """
    user = await make_user(session)
    card = await make_card(session, await make_deck(session))
    review_id = uuid.uuid4()

    def _log() -> ReviewLog:
        return ReviewLog(
            id=review_id,
            user_id=user.id,
            card_id=card.id,
            rating=Rating.good,
            reviewed_at=datetime.now(UTC),
        )

    session.add(_log())
    await session.flush()
    session.add(_log())
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_negative_stability_is_rejected(session: AsyncSession) -> None:
    user = await make_user(session)
    card = await make_card(session, await make_deck(session))
    user_card = await make_user_card(session, user, card)
    user_card.stability = -1.0
    with pytest.raises(DBAPIError):
        await session.flush()


async def test_difficulty_outside_fsrs_range_is_rejected(session: AsyncSession) -> None:
    user = await make_user(session)
    card = await make_card(session, await make_deck(session))
    user_card = await make_user_card(session, user, card)
    user_card.difficulty = 11.0  # FSRS difficulty lives in [1, 10]
    with pytest.raises(DBAPIError):
        await session.flush()


async def test_desired_retention_must_be_a_probability(session: AsyncSession) -> None:
    user = await make_user(session)
    user.desired_retention = 1.5
    with pytest.raises(DBAPIError):
        await session.flush()


async def test_a_user_has_one_row_per_card(session: AsyncSession) -> None:
    user = await make_user(session)
    card = await make_card(session, await make_deck(session))
    await make_user_card(session, user, card)
    session.add(
        UserCard(
            user_id=user.id,
            card_id=card.id,
            deck_id=card.deck_id,
            state=CardState.review,
            due=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_new_card_has_no_memory_state(session: AsyncSession) -> None:
    """FSRS has no stability or difficulty for a card that has never been seen."""
    user = await make_user(session)
    card = await make_card(session, await make_deck(session))
    user_card = await make_user_card(session, user, card)
    assert user_card.stability is None
    assert user_card.difficulty is None
    assert user_card.last_review is None
    assert user_card.reps == 0


async def test_due_queue_ordering(session: AsyncSession) -> None:
    """The hot-path query: a user's cards ordered by due date."""
    user = await make_user(session)
    deck = await make_deck(session)
    now = datetime.now(UTC)
    overdue = await make_user_card(
        session, user, await make_card(session, deck), due=now - timedelta(days=1)
    )
    later = await make_user_card(
        session, user, await make_card(session, deck), due=now + timedelta(days=1)
    )

    due_now = (
        (
            await session.execute(
                select(UserCard.card_id)
                .where(UserCard.user_id == user.id, UserCard.due <= now)
                .order_by(UserCard.due)
            )
        )
        .scalars()
        .all()
    )

    assert list(due_now) == [overdue.card_id]
    assert later.card_id not in due_now


async def test_review_logs_are_isolated_between_users(session: AsyncSession) -> None:
    deck = await make_deck(session)
    card = await make_card(session, deck)
    alice, bob = await make_user(session), await make_user(session)
    for user in (alice, bob):
        session.add(
            ReviewLog(
                id=uuid.uuid4(),
                user_id=user.id,
                card_id=card.id,
                rating=Rating.good,
                reviewed_at=datetime.now(UTC),
            )
        )
    await session.flush()

    alice_logs = (
        (await session.execute(select(ReviewLog).where(ReviewLog.user_id == alice.id)))
        .scalars()
        .all()
    )
    assert len(alice_logs) == 1
    assert alice_logs[0].user_id == alice.id
