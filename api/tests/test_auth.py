"""Auth tests.

These sign real ES256 tokens with a real generated keypair and serve a real JWKS
document; only the network fetch is stubbed. Mocking the verification itself would test
nothing — the whole point is that we reject what should be rejected.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.auth import (
    SUPABASE_AUDIENCE,
    AuthError,
    get_current_user,
    reset_jwk_client,
    verify_token,
)
from lgapp.config import Settings, get_settings
from lgapp.models import User

PROJECT_REF = "testprojectref"
ISSUER = f"https://{PROJECT_REF}.supabase.co/auth/v1"
KID = "test-key-1"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    monkeypatch.setenv("LGAPP_SUPABASE_PROJECT_REF", PROJECT_REF)
    get_settings.cache_clear()
    reset_jwk_client()
    yield get_settings()
    get_settings.cache_clear()
    reset_jwk_client()


@pytest.fixture
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _jwks(key: ec.EllipticCurvePrivateKey, kid: str = KID) -> dict[str, Any]:
    jwk = ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "use": "sig", "alg": "ES256"}]}


@pytest.fixture
def jwks_endpoint(
    monkeypatch: pytest.MonkeyPatch, signing_key: ec.EllipticCurvePrivateKey
) -> dict[str, Any]:
    """Stub only the HTTP fetch; the caching and kid-matching logic stays real."""
    served = _jwks(signing_key)
    calls = {"count": 0}

    def fake_fetch(self: Any) -> dict[str, Any]:
        calls["count"] += 1
        return served

    monkeypatch.setattr("jwt.PyJWKClient.fetch_data", fake_fetch)
    return {"served": served, "calls": calls}


def make_token(
    key: ec.EllipticCurvePrivateKey,
    *,
    subject: str | None = None,
    email: str | None = "learner@example.com",
    audience: str = SUPABASE_AUDIENCE,
    issuer: str = ISSUER,
    expires_in: timedelta = timedelta(hours=1),
    kid: str = KID,
    algorithm: str = "ES256",
    **overrides: Any,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject or str(uuid.uuid4()),
        "aud": audience,
        "iss": issuer,
        "exp": now + expires_in,
        "iat": now,
        "email": email,
        "role": "authenticated",
    }
    payload.update(overrides)
    for key_to_drop in [k for k, v in overrides.items() if v is None]:
        payload.pop(key_to_drop, None)
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})


class TestVerifyToken:
    async def test_accepts_a_valid_token(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        subject = uuid.uuid4()
        claims = await verify_token(make_token(signing_key, subject=str(subject)))
        assert claims.subject == subject
        assert claims.email == "learner@example.com"

    async def test_rejects_an_expired_token(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        token = make_token(signing_key, expires_in=timedelta(hours=-1))
        with pytest.raises(AuthError, match="expired"):
            await verify_token(token)

    async def test_rejects_a_token_from_another_issuer(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        """A token from someone else's Supabase project must not authenticate here."""
        token = make_token(signing_key, issuer="https://evil.supabase.co/auth/v1")
        with pytest.raises(AuthError, match="issuer"):
            await verify_token(token)

    async def test_rejects_a_wrong_audience(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        token = make_token(signing_key, audience="some-other-service")
        with pytest.raises(AuthError, match="audience"):
            await verify_token(token)

    async def test_rejects_a_token_signed_by_a_different_key(
        self, settings: Settings, jwks_endpoint: Any
    ) -> None:
        """The signature check must actually run."""
        attacker_key = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(AuthError):
            await verify_token(make_token(attacker_key))

    async def test_rejects_an_unsigned_token(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        """The classic alg=none attack."""
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "aud": SUPABASE_AUDIENCE,
                "iss": ISSUER,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            key="",
            algorithm="none",
            headers={"kid": KID},
        )
        with pytest.raises(AuthError):
            await verify_token(token)

    async def test_rejects_a_symmetric_token_signed_with_the_public_key(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        """The algorithm-confusion attack.

        The public key is, by design, public. If HS256 were accepted, anyone could sign a
        token using that public key as the HMAC secret and we would verify it happily.
        This is why ALLOWED_ALGORITHMS lists asymmetric algorithms only.
        """
        public_pem = _jwks(signing_key)  # the attacker knows this document
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "aud": SUPABASE_AUDIENCE,
                "iss": ISSUER,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            key=str(public_pem),
            algorithm="HS256",
            headers={"kid": KID},
        )
        with pytest.raises(AuthError):
            await verify_token(forged)

    async def test_rejects_an_unknown_signing_key(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        token = make_token(signing_key, kid="a-kid-we-never-published")
        with pytest.raises(AuthError, match="signing key"):
            await verify_token(token)

    async def test_rejects_a_token_without_a_subject(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        with pytest.raises(AuthError):
            await verify_token(make_token(signing_key, sub=None))

    async def test_rejects_a_non_uuid_subject(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        with pytest.raises(AuthError, match="user id"):
            await verify_token(make_token(signing_key, subject="not-a-uuid"))

    async def test_rejects_garbage(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        with pytest.raises(AuthError):
            await verify_token("this-is-not-a-jwt")

    async def test_accepts_a_token_with_no_email(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        """Email is optional — a phone or anonymous sign-in has none."""
        claims = await verify_token(make_token(signing_key, email=None))
        assert claims.email is None


class TestJwksCaching:
    async def test_key_set_is_fetched_once_across_requests(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        """Otherwise every request would make an outbound HTTP call."""
        for _ in range(5):
            await verify_token(make_token(signing_key))
        assert jwks_endpoint["calls"]["count"] == 1

    async def test_unknown_kid_triggers_one_refetch(
        self, settings: Settings, signing_key: ec.EllipticCurvePrivateKey, jwks_endpoint: Any
    ) -> None:
        """Key rotation: a kid we have never seen must prompt a refresh, not a hard fail."""
        await verify_token(make_token(signing_key))
        assert jwks_endpoint["calls"]["count"] == 1

        rotated_key = ec.generate_private_key(ec.SECP256R1())
        jwks_endpoint["served"]["keys"] = _jwks(rotated_key, kid="rotated-key")["keys"]

        claims = await verify_token(make_token(rotated_key, kid="rotated-key"))
        assert claims.subject is not None
        assert jwks_endpoint["calls"]["count"] == 2, "should refetch exactly once on unknown kid"


class TestGetCurrentUser:
    async def test_creates_the_user_row_on_first_sight(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        subject = uuid.uuid4()
        claims = _claims(subject, "new@example.com")

        user = await get_current_user(claims, session)
        assert user.id == subject
        assert user.email == "new@example.com"

        stored = (await session.execute(select(User).where(User.id == subject))).scalar_one()
        assert stored.id == subject

    async def test_is_idempotent_across_requests(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        subject = uuid.uuid4()
        first = await get_current_user(_claims(subject, "a@example.com"), session)
        second = await get_current_user(_claims(subject, "a@example.com"), session)
        assert first.id == second.id

        count = len((await session.execute(select(User).where(User.id == subject))).scalars().all())
        assert count == 1

    async def test_updates_the_email_when_supabase_changes_it(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        subject = uuid.uuid4()
        await get_current_user(_claims(subject, "old@example.com"), session)
        user = await get_current_user(_claims(subject, "new@example.com"), session)
        assert user.email == "new@example.com"

    async def test_applies_defaults_to_a_new_user(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        user = await get_current_user(_claims(uuid.uuid4(), "x@example.com"), session)
        await session.refresh(user)
        assert user.timezone == "UTC"
        assert user.desired_retention == pytest.approx(0.9)
        assert user.fsrs_parameters is None


def _claims(subject: uuid.UUID, email: str | None) -> Any:
    from lgapp.auth import Claims

    return Claims(subject=subject, email=email, raw={})


def test_rsa_keys_are_also_accepted_by_configuration() -> None:
    """Supabase issues ES256 today but supports RS256; both are in the allow-list."""
    from lgapp.auth import ALLOWED_ALGORITHMS

    assert "RS256" in ALLOWED_ALGORITHMS
    assert "HS256" not in ALLOWED_ALGORITHMS, "symmetric algorithms enable key confusion"
    assert rsa is not None
