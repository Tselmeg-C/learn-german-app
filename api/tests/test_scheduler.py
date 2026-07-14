"""Scheduler tests.

No database here — the scheduler is pure, and these run in milliseconds. The most
important test in the file is `test_replay_agrees_with_incremental_application`: the
offline design assumes those two paths are interchangeable, and if they ever diverge the
`user_cards` cache would silently disagree with `review_logs`.
"""

from datetime import UTC, datetime, timedelta

import pytest

from lgapp.models import CardState, Rating
from lgapp.services.scheduler import (
    MemoryState,
    Review,
    SchedulerConfig,
    apply_review,
    new_card,
    replay,
)

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
CONFIG = SchedulerConfig()


def review(rating: Rating, at: datetime) -> Review:
    return Review(rating=rating, reviewed_at=at, duration_ms=1500)


def test_new_card_has_no_memory_state() -> None:
    state = new_card(T0)
    assert state.state is CardState.learning
    assert state.stability is None
    assert state.difficulty is None
    assert state.last_review is None
    assert state.reps == 0
    assert state.lapses == 0


def test_first_review_establishes_memory_state() -> None:
    state = apply_review(new_card(T0), review(Rating.good, T0), CONFIG)
    assert state.stability is not None and state.stability > 0
    assert state.difficulty is not None and 1 <= state.difficulty <= 10
    assert state.last_review == T0
    assert state.reps == 1
    assert state.due > T0


def test_easy_schedules_further_out_than_hard() -> None:
    easy = apply_review(new_card(T0), review(Rating.easy, T0), CONFIG)
    hard = apply_review(new_card(T0), review(Rating.hard, T0), CONFIG)
    assert easy.due > hard.due


def test_scheduling_is_deterministic() -> None:
    """The property the whole cache-is-derived design rests on.

    FSRS fuzzing is disabled precisely so this holds; with it on, identical input
    produced due dates spread across five days.
    """
    runs = {
        replay(
            [review(Rating.good, T0 + timedelta(days=i)) for i in range(4)], CONFIG, created_at=T0
        ).due
        for _ in range(20)
    }
    assert len(runs) == 1


def test_higher_desired_retention_schedules_sooner() -> None:
    """Sanity check that desired_retention is actually plumbed through to FSRS."""
    history = [review(Rating.good, T0 + timedelta(days=i)) for i in range(4)]
    relaxed = replay(history, SchedulerConfig(desired_retention=0.7), created_at=T0)
    strict = replay(history, SchedulerConfig(desired_retention=0.97), created_at=T0)
    assert strict.due < relaxed.due


class TestLapses:
    def test_failing_a_review_card_counts_a_lapse(self) -> None:
        state = new_card(T0)
        # Graduate the card into the Review state first.
        for i in range(4):
            state = apply_review(state, review(Rating.good, T0 + timedelta(days=i)), CONFIG)
        assert state.state is CardState.review
        assert state.lapses == 0

        state = apply_review(state, review(Rating.again, T0 + timedelta(days=30)), CONFIG)
        assert state.lapses == 1
        assert state.state is CardState.relearning

    def test_failing_a_learning_card_is_not_a_lapse(self) -> None:
        """A card still being learned has nothing to lapse from."""
        state = apply_review(new_card(T0), review(Rating.again, T0), CONFIG)
        assert state.state is CardState.learning
        assert state.lapses == 0

    def test_reps_count_every_review(self) -> None:
        state = new_card(T0)
        for i in range(5):
            state = apply_review(state, review(Rating.good, T0 + timedelta(minutes=10 * i)), CONFIG)
        assert state.reps == 5


class TestReplay:
    def test_empty_history_is_a_new_card(self) -> None:
        assert replay([], CONFIG, created_at=T0) == new_card(T0)

    def test_replay_agrees_with_incremental_application(self) -> None:
        """The load-bearing invariant of the offline design.

        Online reviews are applied incrementally; late ones trigger a replay. If these
        disagreed, a card's due date would jump the moment anything arrived out of order.
        """
        history = [
            review(Rating.good, T0),
            review(Rating.hard, T0 + timedelta(minutes=10)),
            review(Rating.good, T0 + timedelta(days=1)),
            review(Rating.again, T0 + timedelta(days=3)),
            review(Rating.good, T0 + timedelta(days=4)),
            review(Rating.easy, T0 + timedelta(days=12)),
        ]

        incremental = new_card(T0)
        for r in history:
            incremental = apply_review(incremental, r, CONFIG)

        assert replay(history, CONFIG, created_at=T0) == incremental

    def test_replay_is_insensitive_to_submission_order(self) -> None:
        """An offline batch may arrive shuffled; only reviewed_at may decide the order."""
        history = [
            review(Rating.good, T0),
            review(Rating.hard, T0 + timedelta(minutes=10)),
            review(Rating.good, T0 + timedelta(days=1)),
            review(Rating.again, T0 + timedelta(days=3)),
        ]
        shuffled = [history[2], history[0], history[3], history[1]]
        assert replay(shuffled, CONFIG, created_at=T0) == replay(history, CONFIG, created_at=T0)

    def test_replay_reproduces_state_after_a_late_arrival(self) -> None:
        """The scenario the design exists for.

        A review done offline at 09:00 reaches the server after a desktop review at
        09:20. Applying it on top of the newer state would be wrong; replaying the log
        gives the same answer as if both had arrived in order.
        """
        offline = review(Rating.hard, T0)
        desktop = review(Rating.good, T0 + timedelta(minutes=20))

        as_if_ordered = new_card(T0)
        for r in (offline, desktop):
            as_if_ordered = apply_review(as_if_ordered, r, CONFIG)

        # Arrival order: desktop first, then the late offline review.
        after_replay = replay([desktop, offline], CONFIG, created_at=T0)
        assert after_replay == as_if_ordered

    def test_replay_is_idempotent(self) -> None:
        history = [review(Rating.good, T0 + timedelta(days=i)) for i in range(3)]
        assert replay(history, CONFIG, created_at=T0) == replay(history, CONFIG, created_at=T0)

    @pytest.mark.parametrize("rating", list(Rating))
    def test_every_rating_produces_a_valid_state(self, rating: Rating) -> None:
        """Whatever the grade, the result must satisfy the database's check constraints."""
        state = apply_review(new_card(T0), review(rating, T0), CONFIG)
        assert state.stability is not None and state.stability > 0
        assert state.difficulty is not None and 1 <= state.difficulty <= 10
        assert state.due >= T0

    def test_state_is_immutable(self) -> None:
        """apply_review returns new state rather than mutating in place."""
        before = new_card(T0)
        apply_review(before, review(Rating.good, T0), CONFIG)
        assert before == new_card(T0)

    def test_custom_parameters_are_used(self) -> None:
        """Per-user weights must reach FSRS — the hook for fitting parameters later.

        Asserted on stability rather than `due`: the first review is still inside the
        fixed learning steps, so its due date is 10 minutes out regardless of weights.
        Stability is what the weights actually drive.
        """
        weights = [
            0.5,
            1.5,
            3.0,
            9.0,
            6.0,
            0.9,
            3.0,
            0.01,
            1.9,
            0.2,
            0.8,
            1.5,
            0.1,
            0.3,
            1.7,
            0.6,
            1.9,
            0.6,
            0.1,
            0.1,
            0.2,
        ]
        tuned = apply_review(new_card(T0), review(Rating.good, T0), SchedulerConfig(weights))
        default = apply_review(new_card(T0), review(Rating.good, T0), CONFIG)
        assert tuned.stability != default.stability


def _final(state: MemoryState) -> tuple[CardState, int, int]:
    return state.state, state.reps, state.lapses


def test_a_long_realistic_history_stays_consistent() -> None:
    """Longer sequence, mixing failures and successes, replayed against incremental."""
    ratings = [
        Rating.again,
        Rating.good,
        Rating.good,
        Rating.hard,
        Rating.good,
        Rating.easy,
        Rating.again,
        Rating.good,
        Rating.good,
        Rating.easy,
    ]
    history = [review(r, T0 + timedelta(days=i)) for i, r in enumerate(ratings)]

    incremental = new_card(T0)
    for r in history:
        incremental = apply_review(incremental, r, CONFIG)

    replayed = replay(list(reversed(history)), CONFIG, created_at=T0)
    assert replayed == incremental
    assert _final(replayed) == _final(incremental)
    assert replayed.reps == 10
