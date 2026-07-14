import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import func, select

from lgapp.auth import CurrentUser
from lgapp.deps import SessionDep
from lgapp.errors import ProblemError
from lgapp.models import Card, Deck, UserCard, UserDeck
from lgapp.schemas.api import DeckOut, SubscribeOut
from lgapp.services import queue

router = APIRouter(prefix="/v1/decks", tags=["decks"])


@router.get("", summary="List decks with the learner's progress")
async def list_decks(user: CurrentUser, session: SessionDep) -> list[DeckOut]:
    now = datetime.now(UTC)

    card_counts = (
        select(Card.deck_id, func.count().label("card_count")).group_by(Card.deck_id).subquery()
    )
    due_counts = (
        select(UserCard.deck_id, func.count().label("due_count"))
        .where(UserCard.user_id == user.id, UserCard.reps > 0, UserCard.due <= now)
        .group_by(UserCard.deck_id)
        .subquery()
    )
    subscriptions = select(UserDeck.deck_id).where(UserDeck.user_id == user.id).subquery()

    rows = (
        await session.execute(
            select(
                Deck,
                func.coalesce(card_counts.c.card_count, 0),
                func.coalesce(due_counts.c.due_count, 0),
                subscriptions.c.deck_id.isnot(None),
            )
            .outerjoin(card_counts, card_counts.c.deck_id == Deck.id)
            .outerjoin(due_counts, due_counts.c.deck_id == Deck.id)
            .outerjoin(subscriptions, subscriptions.c.deck_id == Deck.id)
            .order_by(Deck.position, Deck.name)
        )
    ).all()

    return [
        DeckOut(
            id=deck.id,
            slug=deck.slug,
            name=deck.name,
            description=deck.description,
            cefr_level=deck.cefr_level,
            card_count=card_count,
            due_count=due_count,
            subscribed=subscribed,
        )
        for deck, card_count, due_count, subscribed in rows
    ]


@router.post("/{deck_id}/subscribe", summary="Subscribe to a deck", status_code=201)
async def subscribe_to_deck(
    deck_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> SubscribeOut:
    """Idempotent: re-subscribing picks up newly imported cards and leaves progress alone."""
    deck = (await session.execute(select(Deck).where(Deck.id == deck_id))).scalar_one_or_none()
    if deck is None:
        raise ProblemError(status=404, title="Deck not found", detail=f"No deck with id {deck_id}.")

    added = await queue.subscribe(session, user, deck_id, now=datetime.now(UTC))
    return SubscribeOut(deck_id=deck_id, cards_added=added)
