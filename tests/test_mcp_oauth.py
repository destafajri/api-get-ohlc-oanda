from __future__ import annotations

import json
import time

import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from httpx import Response

from app.config import get_mcp_settings
from app.main import app as fastapi_app
from app.mcp_server import build_mcp_http_app, create_runtime_mcp_server


PUBLIC_URL = "https://api.example.com/mcp/"
ISSUER_URL = "https://auth.example.com"
JWKS_URL = "https://auth.example.com/oauth2/jwks"


@pytest.fixture(autouse=True)
def oauth_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "legacy-static-token")
    monkeypatch.setenv("MCP_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("MCP_OAUTH_ISSUER_URL", ISSUER_URL)
    monkeypatch.setenv("MCP_OAUTH_JWKS_URL", JWKS_URL)
    get_mcp_settings.cache_clear()
    yield
    get_mcp_settings.cache_clear()


@pytest.fixture(scope="module")
def signing_material():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    )
    public_jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    return private_key, {"keys": [public_jwk]}


def _initialize_payload() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest-oauth", "version": "1.0"},
        },
    }


def _oauth_token(private_key, *, audience: str = PUBLIC_URL) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": ISSUER_URL,
            "aud": audience,
            "sub": "user_test",
            "client_id": "client_test",
            "iat": now,
            "exp": now + 300,
            "scope": "openid profile",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _oauth_app():
    settings = get_mcp_settings()
    server = create_runtime_mcp_server(settings)
    return build_mcp_http_app(server, settings)


def test_oauth_protected_resource_metadata_is_advertised() -> None:
    with TestClient(_oauth_app(), base_url="https://api.example.com") as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp/")

    assert response.status_code == 200
    assert response.json() == {
        "resource": PUBLIC_URL,
        "authorization_servers": [ISSUER_URL],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["openid"],
    }


def test_fastapi_mount_exposes_oauth_metadata() -> None:
    with TestClient(fastapi_app, base_url="https://api.example.com") as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp/")

    assert response.status_code == 200
    assert response.json()["resource"] == PUBLIC_URL


def test_existing_health_route_remains_available_in_oauth_mode() -> None:
    with TestClient(fastapi_app, base_url="https://api.example.com") as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_partial_oauth_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_OAUTH_JWKS_URL", raising=False)
    get_mcp_settings.cache_clear()
    settings = get_mcp_settings()
    server = create_runtime_mcp_server(settings)
    http_app = build_mcp_http_app(server, settings)

    with TestClient(http_app, base_url="https://api.example.com") as client:
        response = client.post(
            "/mcp/",
            json=_initialize_payload(),
            headers={
                "Authorization": "Bearer legacy-static-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"] == "mcp_auth_not_configured"


def test_oauth_configuration_rejects_insecure_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_OAUTH_ISSUER_URL", "http://auth.example.com")
    get_mcp_settings.cache_clear()
    settings = get_mcp_settings()
    server = create_runtime_mcp_server(settings)
    http_app = build_mcp_http_app(server, settings)

    with TestClient(http_app, base_url="https://api.example.com") as client:
        response = client.post(
            "/mcp/",
            json=_initialize_payload(),
            headers={
                "Authorization": "Bearer legacy-static-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"] == "mcp_auth_not_configured"


def test_oauth_challenge_points_to_resource_metadata() -> None:
    with TestClient(_oauth_app(), base_url="https://api.example.com") as client:
        response = client.post(
            "/mcp/",
            json=_initialize_payload(),
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert "Bearer" in challenge
    assert "resource_metadata=" in challenge
    assert "/.well-known/oauth-protected-resource/mcp/" in challenge


def test_static_bearer_still_works_when_oauth_is_enabled() -> None:
    with TestClient(_oauth_app(), base_url="https://api.example.com") as client:
        response = client.post(
            "/mcp/",
            json=_initialize_payload(),
            headers={
                "Authorization": "Bearer legacy-static-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "OANDA OHLC MCP"


@respx.mock
def test_valid_oauth_jwt_initializes_mcp(signing_material) -> None:
    private_key, jwks = signing_material
    respx.get(JWKS_URL).mock(return_value=Response(200, json=jwks))
    token = _oauth_token(private_key)

    with TestClient(_oauth_app(), base_url="https://api.example.com") as client:
        response = client.post(
            "/mcp/",
            json=_initialize_payload(),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "OANDA OHLC MCP"


@respx.mock
def test_oauth_jwt_with_wrong_audience_is_rejected(signing_material) -> None:
    private_key, jwks = signing_material
    respx.get(JWKS_URL).mock(return_value=Response(200, json=jwks))
    token = _oauth_token(private_key, audience="https://other.example.com/mcp/")

    with TestClient(_oauth_app(), base_url="https://api.example.com") as client:
        response = client.post(
            "/mcp/",
            json=_initialize_payload(),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 401
