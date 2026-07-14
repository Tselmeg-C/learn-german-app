"""Minimal builders for test rows.

Kept deliberately thin: they fill in required columns so tests only state the fields they
actually care about.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.models import Card, CardState, Deck, User, UserCard, UserDeck


async def make_user(session: AsyncSession, **kwargs: object) -> User:
    user = User(id=kwargs.pop("id", uuid.uuid4()), email="learner@example.com", **kwargs)
    session.add(user)
    await session.flush()
    return user


async def make_deck(session: AsyncSession, **kwargs: object) -> Deck:
    slug = kwargs.pop("slug", f"deck-{uuid.uuid4().hex[:8]}")
    deck = Deck(slug=slug, name=kwargs.pop("name", "Test Deck"), **kwargs)
    session.add(deck)
    await session.flush()
    return deck


async def make_card(session: AsyncSession, deck: Deck, **kwargs: object) -> Card:
    card = Card(
        deck_id=deck.id,
        external_id=kwargs.pop("external_id", f"ext-{uuid.uuid4().hex[:8]}"),
        german=kwargs.pop("german", "das Haus"),
        english=kwargs.pop("english", "the house"),
        **kwargs,
    )
    session.add(card)
    await session.flush()
    return card


async def make_user_deck(
    session: AsyncSession, user: User, deck: Deck, **kwargs: object
) -> UserDeck:
    user_deck = UserDeck(
        user_id=user.id,
        deck_id=deck.id,
        subscribed_at=kwargs.pop("subscribed_at", datetime.now(UTC)),
        **kwargs,
    )
    session.add(user_deck)
    await session.flush()
    return user_deck


async def make_user_card(
    session: AsyncSession, user: User, card: Card, **kwargs: object
) -> UserCard:
    user_card = UserCard(
        user_id=user.id,
        card_id=card.id,
        deck_id=card.deck_id,
        state=kwargs.pop("state", CardState.learning),
        due=kwargs.pop("due", datetime.now(UTC)),
        **kwargs,
    )
    session.add(user_card)
    await session.flush()
    return user_card
