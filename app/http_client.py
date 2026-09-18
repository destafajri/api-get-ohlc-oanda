import httpx


def create_http_client() -> httpx.AsyncClient:
    """Create the shared async HTTP client used for OANDA requests."""
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )
