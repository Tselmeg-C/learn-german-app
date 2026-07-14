"""API integration tests.

Auth is overridden here — it has its own tests — so these can focus on behaviour. The
important ones are in TestOfflineSync: they exercise the case the whole design exists
for, through the real HTTP surface.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.auth import get_current_user
from lgapp.db import get_session
from lgapp.main import create_app
from lgapp.models import Rating, User
from lgapp.services.importer import import_cards
from tests.factories import make_deck, make_user
from tests.test_importer import ROW_FRAU, ROW_HAUS, write_csv


@pytest.fixture
async def user(session: AsyncSession) -> User:
    return await make_user(session, email="learner@example.com")


@pytest.fixture
async def client(session: AsyncSession, user: User) -> AsyncIterator[AsyncClient]:
    """An authenticated client. Auth itself is covered in test_auth.py."""
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def deck(session: AsyncSession, tmp_path: Any) -> Any:
    await import_cards(
        session, path=write_csv(tmp_path, ROW_HAUS, ROW_FRAU), deck_slug="a1", deck_name="A1"
    )
    from sqlalchemy import select

    from lgapp.models import Deck

    return (await session.execute(select(Deck).where(Deck.slug == "a1"))).scalar_one()


def review_payload(
    card_id: str, rating: Rating, reviewed_at: datetime, **kwargs: Any
) -> dict[str, Any]:
    return {
        "id": str(kwargs.pop("id", uuid.uuid4())),
        "card_id": card_id,
        "rating": int(rating),
        "reviewed_at": reviewed_at.isoformat(),
        "duration_ms": kwargs.pop("duration_ms", 1200),
    }


class TestAuthIsRequired:
    """The override above is per-test; these use a client without it."""

    async def test_decks_requires_a_token(self, session: AsyncSession) -> None:
        app = create_app()

        async def _session() -> AsyncIterator[AsyncSession]:
            yield session

        app.dependency_overrides[get_session] = _session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for method, path in [
                ("get", "/v1/decks"),
                ("get", "/v1/reviews/queue"),
                ("get", "/v1/stats"),
            ]:
                response = await getattr(c, method)(path)
                assert response.status_code == 401, f"{path} must require auth"
                assert response.headers["content-type"] == "application/problem+json"


class TestDecks:
    async def test_lists_decks_with_card_counts(self, client: AsyncClient, deck: Any) -> None:
        response = await client.get("/v1/decks")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["slug"] == "a1"
        assert body[0]["card_count"] == 2
        assert body[0]["subscribed"] is False

    async def test_subscribing_creates_card_states(self, client: AsyncClient, deck: Any) -> None:
        response = await client.post(f"/v1/decks/{deck.id}/subscribe")
        assert response.status_code == 201
        assert response.json()["cards_added"] == 2

        listed = (await client.get("/v1/decks")).json()
        assert listed[0]["subscribed"] is True

    async def test_subscribing_twice_is_harmless(self, client: AsyncClient, deck: Any) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        second = await client.post(f"/v1/decks/{deck.id}/subscribe")
        assert second.status_code == 201
        assert second.json()["cards_added"] == 0

    async def test_resubscribing_picks_up_newly_imported_cards(
        self, client: AsyncClient, deck: Any, session: AsyncSession, tmp_path: Any
    ) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        new_row = "kind,Kind,child,noun,das,Kinder,Das Kind spielt.,The child plays.,people|a1,3"
        await import_cards(
            session,
            path=write_csv(tmp_path, ROW_HAUS, ROW_FRAU, new_row, name="more.csv"),
            deck_slug="a1",
        )
        again = await client.post(f"/v1/decks/{deck.id}/subscribe")
        assert again.json()["cards_added"] == 1

    async def test_subscribing_to_an_unknown_deck_is_404(self, client: AsyncClient) -> None:
        response = await client.post(f"/v1/decks/{uuid.uuid4()}/subscribe")
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"


class TestQueue:
    async def test_new_cards_appear_after_subscribing(self, client: AsyncClient, deck: Any) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        body = (await client.get("/v1/reviews/queue")).json()
        assert len(body["cards"]) == 2
        assert all(card["is_new"] for card in body["cards"])
        assert body["cards"][0]["card"]["german"]

    async def test_queue_is_empty_without_a_subscription(
        self, client: AsyncClient, deck: Any
    ) -> None:
        assert (await client.get("/v1/reviews/queue")).json()["cards"] == []

    async def test_daily_new_limit_is_respected(
        self, client: AsyncClient, deck: Any, session: AsyncSession, user: User
    ) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        from sqlalchemy import select

        from lgapp.models import UserDeck

        user_deck = (
            await session.execute(select(UserDeck).where(UserDeck.user_id == user.id))
        ).scalar_one()
        user_deck.daily_new_limit = 1
        await session.flush()

        body = (await client.get("/v1/reviews/queue")).json()
        assert len(body["cards"]) == 1
        assert body["new_remaining_today"] == 1

    async def test_reviewed_card_leaves_the_new_queue(self, client: AsyncClient, deck: Any) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        card_id = (await client.get("/v1/reviews/queue")).json()["cards"][0]["card"]["id"]

        await client.post(
            "/v1/reviews",
            json={"reviews": [review_payload(card_id, Rating.good, datetime.now(UTC))]},
        )
        body = (await client.get("/v1/reviews/queue")).json()
        remaining_new = [c for c in body["cards"] if c["is_new"]]
        assert card_id not in [c["card"]["id"] for c in remaining_new]

    async def test_filters_by_deck(
        self, client: AsyncClient, deck: Any, session: AsyncSession
    ) -> None:
        other = await make_deck(session, slug="other")
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        body = (await client.get("/v1/reviews/queue", params={"deck_id": str(other.id)})).json()
        assert body["cards"] == []


class TestSubmitReviews:
    async def test_records_a_review_and_returns_new_state(
        self, client: AsyncClient, deck: Any
    ) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        card_id = (await client.get("/v1/reviews/queue")).json()["cards"][0]["card"]["id"]

        response = await client.post(
            "/v1/reviews",
            json={"reviews": [review_payload(card_id, Rating.good, datetime.now(UTC))]},
        )
        assert response.status_code == 200
        card = response.json()["cards"][0]
        assert card["card_id"] == card_id
        assert card["reps"] == 1
        assert card["stability"] is not None

    async def test_rejects_a_card_the_learner_is_not_subscribed_to(
        self, client: AsyncClient, deck: Any
    ) -> None:
        """Otherwise anyone could write review history against any card id."""
        response = await client.post(
            "/v1/reviews",
            json={"reviews": [review_payload(str(uuid.uuid4()), Rating.good, datetime.now(UTC))]},
        )
        assert response.status_code == 404

    async def test_rejects_a_future_timestamp(self, client: AsyncClient, deck: Any) -> None:
        """A future reviewed_at is a broken device clock; honouring it strands the card."""
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        card_id = (await client.get("/v1/reviews/queue")).json()["cards"][0]["card"]["id"]
        response = await client.post(
            "/v1/reviews",
            json={
                "reviews": [
                    review_payload(card_id, Rating.good, datetime.now(UTC) + timedelta(days=1))
                ]
            },
        )
        assert response.status_code == 422

    async def test_rejects_an_empty_batch(self, client: AsyncClient) -> None:
        assert (await client.post("/v1/reviews", json={"reviews": []})).status_code == 422

    async def test_rejects_an_invalid_rating(self, client: AsyncClient, deck: Any) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        card_id = (await client.get("/v1/reviews/queue")).json()["cards"][0]["card"]["id"]
        response = await client.post(
            "/v1/reviews",
            json={
                "reviews": [
                    {
                        "id": str(uuid.uuid4()),
                        "card_id": card_id,
                        "rating": 9,
                        "reviewed_at": datetime.now(UTC).isoformat(),
                    }
                ]
            },
        )
        assert response.status_code == 422


class TestOfflineSync:
    """The cases the whole review_logs-as-source-of-truth design exists for."""

    async def test_a_batch_of_reviews_syncs_at_once(self, client: AsyncClient, deck: Any) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        cards = (await client.get("/v1/reviews/queue")).json()["cards"]
        now = datetime.now(UTC)

        response = await client.post(
            "/v1/reviews",
            json={
                "reviews": [
                    review_payload(
                        cards[0]["card"]["id"], Rating.good, now - timedelta(minutes=10)
                    ),
                    review_payload(cards[1]["card"]["id"], Rating.hard, now - timedelta(minutes=9)),
                ]
            },
        )
        assert response.status_code == 200
        assert len(response.json()["cards"]) == 2

    async def test_resubmitting_a_batch_does_not_double_apply(
        self, client: AsyncClient, deck: Any
    ) -> None:
        """The retry-after-timeout case: the client never knew the first call landed."""
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        card_id = (await client.get("/v1/reviews/queue")).json()["cards"][0]["card"]["id"]
        batch = {"reviews": [review_payload(card_id, Rating.good, datetime.now(UTC))]}

        first = (await client.post("/v1/reviews", json=batch)).json()["cards"][0]
        second = (await client.post("/v1/reviews", json=batch)).json()["cards"][0]

        assert second["reps"] == first["reps"] == 1, "a replayed batch must not count twice"
        assert second["due"] == first["due"]
        assert second["stability"] == first["stability"]

    async def test_a_late_review_does_not_corrupt_the_schedule(
        self, client: AsyncClient, deck: Any
    ) -> None:
        """The scenario from the design: phone reviewed at 09:00, synced after 09:20.

        Applying the late review on top of the newer state would be wrong. Replaying the
        log must land exactly where in-order arrival would have.
        """
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        card_id = (await client.get("/v1/reviews/queue")).json()["cards"][0]["card"]["id"]

        t0 = datetime.now(UTC) - timedelta(hours=2)
        offline = review_payload(card_id, Rating.hard, t0)
        desktop = review_payload(card_id, Rating.good, t0 + timedelta(minutes=20))

        # Desktop syncs first; the phone's earlier review arrives afterwards.
        await client.post("/v1/reviews", json={"reviews": [desktop]})
        out_of_order = (await client.post("/v1/reviews", json={"reviews": [offline]})).json()[
            "cards"
        ][0]

        assert out_of_order["reps"] == 2
        assert out_of_order["last_review"] is not None

        # Now the same two reviews, in order, for a second learner on the same content.
        in_order = await _replay_in_order(client, deck, [offline, desktop])

        assert out_of_order["due"] == in_order["due"]
        assert out_of_order["stability"] == in_order["stability"]
        assert out_of_order["difficulty"] == in_order["difficulty"]

    async def test_reviews_arriving_shuffled_in_one_batch(
        self, client: AsyncClient, deck: Any
    ) -> None:
        """An outbox may drain in any order; only reviewed_at may decide the schedule."""
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        card_id = (await client.get("/v1/reviews/queue")).json()["cards"][0]["card"]["id"]

        t0 = datetime.now(UTC) - timedelta(hours=1)
        first = review_payload(card_id, Rating.good, t0)
        second = review_payload(card_id, Rating.hard, t0 + timedelta(minutes=10))

        shuffled = (await client.post("/v1/reviews", json={"reviews": [second, first]})).json()[
            "cards"
        ][0]
        assert shuffled["reps"] == 2

        ordered = await _replay_in_order(client, deck, [first, second])
        assert shuffled["due"] == ordered["due"]
        assert shuffled["stability"] == ordered["stability"]


async def _replay_in_order(client: AsyncClient, deck: Any, reviews: list[dict[str, Any]]) -> Any:
    """Submit the same reviews in timestamp order against a fresh card, for comparison."""
    fresh_card_id = (await client.get("/v1/reviews/queue")).json()["cards"][-1]["card"]["id"]
    last: Any = None
    for review in sorted(reviews, key=lambda r: r["reviewed_at"]):
        payload = {**review, "id": str(uuid.uuid4()), "card_id": fresh_card_id}
        last = (await client.post("/v1/reviews", json={"reviews": [payload]})).json()["cards"][0]
    return last


class TestStats:
    async def test_empty_stats_are_honest(self, client: AsyncClient) -> None:
        body = (await client.get("/v1/stats")).json()
        assert body["reviews_total"] == 0
        assert body["streak_days"] == 0
        assert body["retention_rate"] is None, "no data must not read as 0% retention"

    async def test_counts_reviews(self, client: AsyncClient, deck: Any) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        cards = (await client.get("/v1/reviews/queue")).json()["cards"]
        now = datetime.now(UTC)
        await client.post(
            "/v1/reviews",
            json={
                "reviews": [
                    review_payload(cards[0]["card"]["id"], Rating.good, now),
                    review_payload(cards[1]["card"]["id"], Rating.again, now),
                ]
            },
        )
        body = (await client.get("/v1/stats")).json()
        assert body["reviews_total"] == 2
        assert body["reviews_today"] == 2
        assert body["streak_days"] == 1
        assert len(body["reviews_per_day"]) == 1
        assert body["reviews_per_day"][0]["count"] == 2

    async def test_cards_by_state_counts_new_separately(
        self, client: AsyncClient, deck: Any
    ) -> None:
        await client.post(f"/v1/decks/{deck.id}/subscribe")
        body = (await client.get("/v1/stats")).json()
        assert body["cards_by_state"]["new"] == 2
        assert body["cards_by_state"]["learning"] == 0, "an unseen card is new, not learning"
