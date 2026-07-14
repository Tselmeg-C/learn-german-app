"""Progress statistics, computed in the database.

All of this is derived from `review_logs`, which is why it stays honest through replays
and re-syncs.
"""

from datetime import datetime, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.models import CardState, Rating, ReviewLog, User, UserCard
from lgapp.services.queue import day_start, user_timezone


async def reviews_total(session: AsyncSession, user: User) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(ReviewLog).where(ReviewLog.user_id == user.id)
        )
    ).scalar_one()


async def reviews_since(session: AsyncSession, user: User, since: datetime) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ReviewLog)
            .where(ReviewLog.user_id == user.id, ReviewLog.reviewed_at >= since)
        )
    ).scalar_one()


async def retention_rate(session: AsyncSession, user: User) -> float | None:
    """Share of reviews on mature cards graded better than Again.

    Restricted to cards in the Review state: counting reviews of cards still being
    learned would measure how many times you clicked through a new card, not how well you
    remember anything. Returns None rather than 0.0 when there is nothing to measure —
    "no data" and "you forgot everything" must not look the same.
    """
    passed = func.count().filter(ReviewLog.rating != Rating.again)
    total = func.count()
    statement = (
        select(passed, total)
        .join(
            UserCard,
            (UserCard.card_id == ReviewLog.card_id) & (UserCard.user_id == ReviewLog.user_id),
        )
        .where(ReviewLog.user_id == user.id, UserCard.state == CardState.review)
    )
    passed_count, total_count = (await session.execute(statement)).one()
    if not total_count:
        return None
    return float(passed_count) / float(total_count)


async def cards_by_state(session: AsyncSession, user: User) -> dict[str, int]:
    rows = (
        await session.execute(
            select(UserCard.state, func.count())
            .where(UserCard.user_id == user.id)
            .group_by(UserCard.state)
        )
    ).all()
    counts = {state.value: 0 for state in CardState}
    counts["new"] = (
        await session.execute(
            select(func.count())
            .select_from(UserCard)
            .where(UserCard.user_id == user.id, UserCard.reps == 0)
        )
    ).scalar_one()
    for state, count in rows:
        counts[state.value] = count
    # A never-reviewed card is in the learning state but is really "new"; don't count it twice.
    counts[CardState.learning.value] = max(0, counts[CardState.learning.value] - counts["new"])
    return counts


async def reviews_per_day(
    session: AsyncSession, user: User, *, now: datetime, days: int = 30
) -> list[tuple[str, int]]:
    """Daily review counts, bucketed in the learner's timezone."""
    tz = user_timezone(user)
    since = day_start(user, now) - timedelta(days=days - 1)
    local_day = cast(func.timezone(str(tz), ReviewLog.reviewed_at), Date)

    rows = (
        await session.execute(
            select(local_day.label("day"), func.count())
            .where(ReviewLog.user_id == user.id, ReviewLog.reviewed_at >= since)
            .group_by(local_day)
            .order_by(local_day)
        )
    ).all()
    return [(day.isoformat(), count) for day, count in rows]


async def streak_days(session: AsyncSession, user: User, *, now: datetime) -> int:
    """Consecutive days with at least one review, counting back from today.

    Today not being studied yet does not break a streak — it only ends once yesterday has
    no reviews either. Otherwise every streak would read zero each morning.
    """
    tz = user_timezone(user)
    local_day = cast(func.timezone(str(tz), ReviewLog.reviewed_at), Date)
    days = (
        (
            await session.execute(
                select(local_day.label("day"))
                .where(ReviewLog.user_id == user.id)
                .group_by(local_day)
                .order_by(local_day.desc())
            )
        )
        .scalars()
        .all()
    )
    if not days:
        return 0

    today = now.astimezone(tz).date()
    if (today - days[0]).days > 1:
        return 0

    streak = 0
    expected = days[0]
    for day in days:
        if day != expected:
            break
        streak += 1
        expected = day - timedelta(days=1)
    return streak


async def due_today(session: AsyncSession, user: User, *, now: datetime) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(UserCard)
            .where(UserCard.user_id == user.id, UserCard.reps > 0, UserCard.due <= now)
        )
    ).scalar_one()
