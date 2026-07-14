from datetime import UTC, datetime

from fastapi import APIRouter

from lgapp.auth import CurrentUser
from lgapp.deps import SessionDep
from lgapp.schemas.api import DayCount, StatsOut
from lgapp.services import stats
from lgapp.services.queue import day_start

router = APIRouter(prefix="/v1/stats", tags=["stats"])


@router.get("", summary="Progress statistics")
async def get_stats(user: CurrentUser, session: SessionDep) -> StatsOut:
    now = datetime.now(UTC)
    return StatsOut(
        reviews_total=await stats.reviews_total(session, user),
        reviews_today=await stats.reviews_since(session, user, day_start(user, now)),
        retention_rate=await stats.retention_rate(session, user),
        streak_days=await stats.streak_days(session, user, now=now),
        cards_by_state=await stats.cards_by_state(session, user),
        due_today=await stats.due_today(session, user, now=now),
        reviews_per_day=[
            DayCount(day=day, count=count)
            for day, count in await stats.reviews_per_day(session, user, now=now)
        ],
    )
