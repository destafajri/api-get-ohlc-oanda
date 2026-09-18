from __future__ import annotations

import asyncio
import time
from hmac import compare_digest
from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import AnyHttpUrl, SecretStr


JWKS_CACHE_SECONDS = 300
JWKS_MAX_BYTES = 1_000_000


class McpOAuthConfigurationError(RuntimeError):
    """Raised when OAuth is partially or incorrectly configured."""


class CompositeTokenVerifier(TokenVerifier):
    """Accept the legacy static token and standards-based OAuth JWT access tokens."""

    def __init__(
        self,
        *,
        issuer_url: AnyHttpUrl,
        resource_url: AnyHttpUrl,
        jwks_url: AnyHttpUrl,
        static_token: SecretStr | None = None,
    ) -> None:
        self.issuer_url = str(issuer_url).rstrip("/")
        self.resource_url = str(resource_url)
        self.jwks_url = str(jwks_url)
        self.static_token = static_token
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires_at = 0.0
        self._jwks_lock = asyncio.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        if self._matches_static_token(token):
            return AccessToken(
                token=token,
                client_id="static-bearer",
                scopes=["openid"],
                resource=self.resource_url,
                subject="static-bearer",
                claims={"iss": self.issuer_url},
            )

        try:
            return await self._verify_oauth_jwt(token)
        except (jwt.PyJWTError, ValueError, TypeError, httpx.HTTPError):
            return None

    def _matches_static_token(self, token: str) -> bool:
        if self.static_token is None:
            return False
        expected = self.static_token.get_secret_value()
        return bool(expected) and compare_digest(token, expected)

    async def _verify_oauth_jwt(self, token: str) -> AccessToken | None:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            return None

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            return None

        key_data = await self._find_jwk(kid)
        if key_data is None:
            # Key rotation can make a previously cached JWKS stale. Refresh once.
            await self._get_jwks(force=True)
            key_data = await self._find_jwk(kid, refresh=False)
            if key_data is None:
                return None

        key = jwt.PyJWK.from_dict(key_data, algorithm="RS256").key
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=self.issuer_url,
            audience=self.resource_url,
            options={"require": ["iss", "aud", "sub", "iat", "exp"]},
        )

        client_id = payload.get("client_id")
        subject = payload.get("sub")
        expires_at = payload.get("exp")
        if not isinstance(client_id, str) or not client_id:
            return None
        if not isinstance(subject, str) or not subject:
            return None
        if not isinstance(expires_at, int):
            return None

        scopes = _parse_scopes(payload.get("scope"))
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=self.resource_url,
            subject=subject,
            claims={"iss": self.issuer_url},
        )

    async def _find_jwk(
        self, kid: str, *, refresh: bool = True
    ) -> dict[str, Any] | None:
        jwks = await self._get_jwks(force=False) if refresh else self._jwks
        if not isinstance(jwks, dict):
            return None

        keys = jwks.get("keys")
        if not isinstance(keys, list):
            return None

        for candidate in keys:
            if (
                isinstance(candidate, dict)
                and candidate.get("kid") == kid
                and candidate.get("kty") == "RSA"
            ):
                return candidate
        return None

    async def _get_jwks(self, *, force: bool) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._jwks is not None and now < self._jwks_expires_at:
            return self._jwks

        async with self._jwks_lock:
            now = time.monotonic()
            if (
                not force
                and self._jwks is not None
                and now < self._jwks_expires_at
            ):
                return self._jwks

            timeout = httpx.Timeout(5.0)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    self.jwks_url,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                if len(response.content) > JWKS_MAX_BYTES:
                    raise ValueError("JWKS response is too large")
                data = response.json()

            if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
                raise ValueError("Invalid JWKS document")

            self._jwks = data
            self._jwks_expires_at = time.monotonic() + JWKS_CACHE_SECONDS
            return data


def _parse_scopes(raw_scope: object) -> list[str]:
    if isinstance(raw_scope, str):
        return [scope for scope in raw_scope.split() if scope]
    if isinstance(raw_scope, list):
        return [scope for scope in raw_scope if isinstance(scope, str) and scope]
    return []
