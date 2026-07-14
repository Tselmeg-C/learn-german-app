"""FSRS scheduling, wrapped.

Everything here is pure: values in, values out, no database and no clock of its own. That
is deliberate — `replay()` is the correctness-critical function in the app and it should
be testable by calling it.

Two invariants this module exists to protect:

1. **Determinism.** `user_cards` is a cache of what `replay()` computes from
   `review_logs`. If scheduling were non-deterministic the cache could disagree with its
   own source of truth, and a card's due date would change every time we recomputed it.
   FSRS's interval fuzzing is therefore disabled — see `_build_scheduler`.

2. **Isolation.** FSRS types never leave this module. Callers pass and receive our own
   `MemoryState`, so swapping the library is a change here and nowhere else.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from fsrs import Card as FsrsCard
from fsrs import Rating as FsrsRating
from fsrs import Scheduler as FsrsScheduler
from fsrs import State as FsrsState

from lgapp.models import CardState, Rating

DEFAULT_DESIRED_RETENTION = 0.9

_TO_FSRS_STATE = {
    CardState.learning: FsrsState.Learning,
    CardState.review: FsrsState.Review,
    CardState.relearning: FsrsState.Relearning,
}
_FROM_FSRS_STATE = {v: k for k, v in _TO_FSRS_STATE.items()}


@dataclass(frozen=True, slots=True)
class MemoryState:
    """A card's scheduling state for one learner. Mirrors the `user_cards` columns."""

    state: CardState
    due: datetime
    stability: float | None = None
    difficulty: float | None = None
    step: int | None = None
    last_review: datetime | None = None
    reps: int = 0
    lapses: int = 0


@dataclass(frozen=True, slots=True)
class Review:
    """One graded answer. Mirrors the `review_logs` columns we schedule from."""

    rating: Rating
    reviewed_at: datetime
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Per-user FSRS settings. `parameters=None` means the library defaults."""

    parameters: Sequence[float] | None = None
    desired_retention: float = DEFAULT_DESIRED_RETENTION


def _build_scheduler(config: SchedulerConfig) -> FsrsScheduler:
    kwargs: dict[str, object] = {
        "desired_retention": config.desired_retention,
        # Fuzzing randomises each interval by a few percent to stop cards that were
        # learned together from staying clumped together forever. We cannot use it: it
        # makes review_card() non-deterministic, so replaying a log would produce a
        # different due date than applying the same reviews incrementally — the cache and
        # its source of truth would disagree. Measured: identical input, five distinct
        # due dates across five days. If clumping ever becomes a real complaint, the fix
        # is to derive a jitter from the review's UUID, which is stable under replay.
        "enable_fuzzing": False,
    }
    if config.parameters is not None:
        kwargs["parameters"] = tuple(config.parameters)
    return FsrsScheduler(**kwargs)  # type: ignore[arg-type]


def _to_fsrs_card(state: MemoryState) -> FsrsCard:
    return FsrsCard(
        state=_TO_FSRS_STATE[state.state],
        step=state.step,
        stability=state.stability,
        difficulty=state.difficulty,
        due=state.due,
        last_review=state.last_review,
    )


def new_card(due: datetime) -> MemoryState:
    """State for a card the learner has never seen.

    Stability and difficulty stay None: FSRS has no memory model for an unseen card, and
    storing zeros would be a lie the check constraints would (correctly) reject.
    """
    return MemoryState(state=CardState.learning, due=due, step=0)


def apply_review(state: MemoryState, review: Review, config: SchedulerConfig) -> MemoryState:
    """Schedule one review on top of `state`.

    Valid only when `review` is the newest review for the card — the caller must use
    `replay()` for anything that arrives late. `apply_review` cannot detect that itself
    without the full history, so `services/reviews.py` owns the decision.
    """
    scheduler = _build_scheduler(config)
    card, _ = scheduler.review_card(
        _to_fsrs_card(state),
        FsrsRating(int(review.rating)),
        review_datetime=review.reviewed_at,
        review_duration=review.duration_ms,
    )

    # FSRS 6 dropped reps/lapses from its Card, so we count them. A lapse is a card in
    # Review being failed — failing a card that is already in Learning or Relearning is
    # not a new lapse, it's the same one continuing.
    lapsed = state.state is CardState.review and review.rating is Rating.again

    return replace(
        state,
        state=_FROM_FSRS_STATE[card.state],
        due=card.due,
        stability=card.stability,
        difficulty=card.difficulty,
        step=card.step,
        last_review=card.last_review,
        reps=state.reps + 1,
        lapses=state.lapses + (1 if lapsed else 0),
    )


def replay(
    reviews: Iterable[Review], config: SchedulerConfig, *, created_at: datetime
) -> MemoryState:
    """Rebuild a card's state from its whole review history.

    This is the authority. `user_cards` only ever holds what this function would return,
    which is what makes out-of-order offline submissions safe: rather than applying a
    late review on top of newer state, we rebuild from the log.

    Reviews are sorted by `reviewed_at`; FSRS is order-dependent and an offline client's
    submissions arrive in whatever order the network allows.

    `created_at` seeds the initial due date for a card with no reviews yet.
    """
    ordered = sorted(reviews, key=lambda r: r.reviewed_at)
    state = new_card(created_at)
    for review in ordered:
        state = apply_review(state, review, config)
    return state
