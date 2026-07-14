"""Supabase Auth token verification.

Supabase signs JWTs with an asymmetric key and publishes the public half at a JWKS
endpoint, so we verify tokens locally: no call back to the auth server on the request
path, and no shared secret for us to leak. Supabase remains the source of truth for
identity; the `users` row is a local projection, upserted the first time we see a subject.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.dialects.postgresql import insert

from lgapp.config import Settings, get_settings
from lgapp.deps import SessionDep
from lgapp.errors import ProblemError
from lgapp.models import User

log = logging.getLogger(__name__)

# Supabase stamps every end-user token with this audience.
SUPABASE_AUDIENCE = "authenticated"

# Asymmetric only. Listing algorithms explicitly is what stops an attacker presenting a
# token signed with "none", or an HS256 token whose "secret" is our public key.
ALLOWED_ALGORITHMS = ["ES256", "RS256", "EdDSA"]

_jwk_client: PyJWKClient | None = None


class AuthError(ProblemError):
    def __init__(self, detail: str) -> None:
        super().__init__(status=401, title="Not authenticated", detail=detail)


@dataclass(frozen=True, slots=True)
class Claims:
    subject: uuid.UUID
    email: str | None
    raw: dict[str, Any]


def get_jwk_client(settings: Settings | None = None) -> PyJWKClient:
    """Process-wide JWKS client.

    PyJWKClient caches the key set for `lifespan` seconds and, on a token whose `kid` it
    does not recognise, refetches once before failing — which is key rotation handled for
    us. Building one client per request would defeat both.
    """
    global _jwk_client
    if _jwk_client is None:
        settings = settings or get_settings()
        _jwk_client = PyJWKClient(
            settings.jwks_url,
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=settings.jwks_cache_seconds,
            timeout=5,
        )
    return _jwk_client


def reset_jwk_client() -> None:
    """Drop the cached client. For tests and for config reloads."""
    global _jwk_client
    _jwk_client = None


def _verify_sync(token: str, settings: Settings, client: PyJWKClient) -> dict[str, Any]:
    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except jwt.PyJWKClientError as exc:
        # An unknown kid survives a refresh: either a forged token or a rotation we
        # cannot see. Either way the caller gets 401, but this one is worth logging.
        log.warning("jwks lookup failed", extra={"error": str(exc)})
        raise AuthError("Token signing key is not recognised.") from exc
    except jwt.InvalidTokenError as exc:
        # get_signing_key_from_jwt parses the header before looking anything up, so a
        # malformed token raises here rather than from decode(). Without this, garbage in
        # an Authorization header would surface as a 500 instead of a 401.
        raise AuthError("Token is malformed.") from exc

    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=SUPABASE_AUDIENCE,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired.") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthError("Token audience is invalid.") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthError("Token issuer is invalid.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Token is invalid.") from exc


async def verify_token(token: str, settings: Settings | None = None) -> Claims:
    """Verify a bearer token and return its claims.

    Runs off the event loop: PyJWKClient fetches the key set with blocking urllib, so the
    first request after the cache expires would otherwise stall every other request in
    the process.
    """
    settings = settings or get_settings()
    client = get_jwk_client(settings)
    payload = await asyncio.to_thread(_verify_sync, token, settings, client)

    try:
        subject = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthError("Token subject is not a valid user id.") from exc

    return Claims(subject=subject, email=payload.get("email"), raw=payload)


bearer_scheme = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_claims(credentials: BearerDep) -> Claims:
    if credentials is None:
        raise AuthError("Missing bearer token.")
    return await verify_token(credentials.credentials)


ClaimsDep = Annotated[Claims, Depends(get_claims)]


async def get_current_user(claims: ClaimsDep, session: SessionDep) -> User:
    """Resolve the authenticated user, creating the local row on first sight.

    The upsert is unconditional rather than a select-then-insert: two concurrent requests
    from a brand-new user would otherwise race and one would hit a duplicate key.
    """
    values = {"id": claims.subject, "email": claims.email}
    statement = (
        insert(User)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[User.id],
            # Keep the local projection in step with Supabase if the email changed.
            set_={"email": claims.email},
        )
        .returning(User)
    )
    user = (await session.execute(statement)).scalar_one()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
