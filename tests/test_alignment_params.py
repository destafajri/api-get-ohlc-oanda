import respx
from fastapi.testclient import TestClient
from httpx import Response


@respx.mock
def test_get_ohlc_forwards_alignment_parameters_to_oanda(
    client: TestClient,
) -> None:
    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={"instrument": "XAU_USD", "granularity": "H4", "candles": []},
        )
    )

    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": "2025-03-27T12:00:00Z",
            "to": "2025-03-29T12:00:00Z",
            "dailyAlignment": 17,
            "alignmentTimezone": "Asia/Jerusalem",
            "smooth": "false",
        },
    )

    assert response.status_code == 200
    params = route.calls.last.request.url.params
    assert params["dailyAlignment"] == "17"
    assert params["alignmentTimezone"] == "Asia/Jerusalem"
    assert params["smooth"] == "false"


def test_get_ohlc_rejects_out_of_range_daily_alignment(
    client: TestClient,
) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "count": 1,
            "dailyAlignment": 24,
        },
    )

    assert response.status_code == 422
