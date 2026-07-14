"""Building a learner's review queue.

Two kinds of card go into a session: those that are due for review, and a bounded number
of cards the learner has never seen. The bound exists because FSRS will happily let you
start 500 new cards today and then punish you for a month.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import DateTime, Select, func, literal, select
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from lgapp.models import Card, ReviewLog, User, UserCard, UserDeck

DEFAULT_QUEUE_LIMIT = 50


def user_timezone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # A bad timezone must not break someone's review session.
        return ZoneInfo("UTC")


def day_start(user: User, now: datetime) -> datetime:
    """Midnight for the learner, not midnight UTC.

    A learner in Berlin reviewing at 00:30 local is on a new day; UTC would still call it
    yesterday and their daily limit would be wrong.
    """
    tz = user_timezone(user)
    local: date = now.astimezone(tz).date()
    return datetime.combine(local, time.min, tzinfo=tz)


def _with_card(statement: Select[tuple[UserCard]]) -> Select[tuple[UserCard]]:
    return statement.options(joinedload(UserCard.card))


async def new_cards_introduced_today(session: AsyncSession, user: User, now: datetime) -> int:
    """How many never-before-seen cards the learner has already started today.

    Counted from the log — a card counts as introduced on the day of its *first* review,
    which survives re-syncs and replays. Deriving it from user_cards.created_at would
    count subscription, not study.
    """
    firsts = (
        select(ReviewLog.card_id, func.min(ReviewLog.reviewed_at).label("first_seen"))
        .where(ReviewLog.user_id == user.id)
        .group_by(ReviewLog.card_id)
        .subquery()
    )
    statement = (
        select(func.count()).select_from(firsts).where(firsts.c.first_seen >= day_start(user, now))
    )
    return (await session.execute(statement)).scalar_one()


@dataclass(frozen=True, slots=True)
class Queue:
    due: list[UserCard]
    new: list[UserCard]
    due_total: int
    new_remaining_today: int

    @property
    def cards(self) -> list[UserCard]:
        """Due cards first: reviews that are already late matter more than new material."""
        return [*self.due, *self.new]


async def build_queue(
    session: AsyncSession,
    user: User,
    *,
    now: datetime,
    deck_id: uuid.UUID | None = None,
    limit: int = DEFAULT_QUEUE_LIMIT,
) -> Queue:
    """Assemble what the learner should see next."""
    subscribed = select(UserDeck.deck_id).where(UserDeck.user_id == user.id)
    if deck_id is not None:
        subscribed = subscribed.where(UserDeck.deck_id == deck_id)

    base = select(UserCard).where(
        UserCard.user_id == user.id,
        UserCard.deck_id.in_(subscribed),
    )

    # reps == 0 is the definition of "never seen". It is derived from the review log via
    # the scheduler, so it stays true through replays.
    due_query = (
        base.where(UserCard.reps > 0, UserCard.due <= now)
        .order_by(UserCard.due)  # most overdue first
        .limit(limit)
    )
    due = list((await session.execute(_with_card(due_query))).scalars().unique())

    due_total = (
        await session.execute(
            select(func.count())
            .select_from(UserCard)
            .where(
                UserCard.user_id == user.id,
                UserCard.deck_id.in_(subscribed),
                UserCard.reps > 0,
                UserCard.due <= now,
            )
        )
    ).scalar_one()

    daily_limit = await _daily_new_limit(session, user, deck_id)
    introduced = await new_cards_introduced_today(session, user, now)
    new_remaining = max(0, daily_limit - introduced)

    new: list[UserCard] = []
    if new_remaining and (room := limit - len(due)) > 0:
        new_query = (
            base.where(UserCard.reps == 0)
            .join(Card, Card.id == UserCard.card_id)
            .order_by(Card.position, Card.external_id)  # follow the deck's intended order
            .limit(min(room, new_remaining))
        )
        new = list((await session.execute(_with_card(new_query))).scalars().unique())

    return Queue(due=due, new=new, due_total=due_total, new_remaining_today=new_remaining)


async def _daily_new_limit(session: AsyncSession, user: User, deck_id: uuid.UUID | None) -> int:
    statement = select(func.coalesce(func.sum(UserDeck.daily_new_limit), 0)).where(
        UserDeck.user_id == user.id
    )
    if deck_id is not None:
        statement = statement.where(UserDeck.deck_id == deck_id)
    return (await session.execute(statement)).scalar_one()


async def subscribe(session: AsyncSession, user: User, deck_id: uuid.UUID, *, now: datetime) -> int:
    """Subscribe a learner to a deck, creating their card states.

    Returns the number of cards added. Re-subscribing adds any cards imported since,
    without disturbing progress on the ones already there.
    """
    await session.execute(
        insert(UserDeck)
        .values(user_id=user.id, deck_id=deck_id, subscribed_at=now)
        .on_conflict_do_nothing(constraint="uq_user_decks_user_id_deck_id")
    )

    # INSERT ... SELECT: one statement, so a 10k-card deck is one round trip rather than
    # 10k. The literals ride along in the SELECT — from_select() and values() cannot be
    # combined. DO NOTHING protects existing progress on a re-subscribe.
    source = select(
        literal(user.id, UUID(as_uuid=True)),
        Card.id,
        Card.deck_id,
        literal(now, DateTime(timezone=True)),
    ).where(Card.deck_id == deck_id)

    statement = insert(UserCard).from_select(["user_id", "card_id", "deck_id", "due"], source)
    # RETURNING rather than rowcount: DO NOTHING makes rowcount ambiguous across drivers,
    # and this counts exactly the rows that were actually created.
    added = await session.execute(
        statement.on_conflict_do_nothing(index_elements=["user_id", "card_id"]).returning(
            UserCard.card_id
        )
    )
    return len(added.scalars().all())


async def cards_in_deck(session: AsyncSession, deck_id: uuid.UUID) -> int:
    return (
        await session.execute(select(func.count()).select_from(Card).where(Card.deck_id == deck_id))
    ).scalar_one()
