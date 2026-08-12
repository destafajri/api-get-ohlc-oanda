from typing import Any

import httpx

from app.config import Settings
from app.models import Candle, Granularity, OhlcResponse


class OandaServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class OandaService:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def get_ohlc(
        self, instrument: str, granularity: Granularity, count: int
    ) -> OhlcResponse:
        url = f"{self.settings.oanda_base_url}/instruments/{instrument}/candles"
        headers = {
            "Authorization": f"Bearer {self.settings.oanda_token.get_secret_value()}",
            "Accept-Datetime-Format": "RFC3339",
        }
        params = {
            "granularity": granularity.value,
            "count": count,
            "price": "M",
        }

        try:
            response = await self.client.get(
                url,
                headers=headers,
                params=params,
                timeout=self.settings.oanda_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise OandaServiceError(
                504, "oanda_timeout", "OANDA did not respond before the timeout."
            ) from exc
        except httpx.RequestError as exc:
            raise OandaServiceError(
                503, "oanda_unavailable", "OANDA is currently unavailable."
            ) from exc

        if response.is_error:
            self._raise_for_error(response)

        try:
            payload: dict[str, Any] = response.json()
            candles = [
                Candle(
                    time=item["time"],
                    open=item["mid"]["o"],
                    high=item["mid"]["h"],
                    low=item["mid"]["l"],
                    close=item["mid"]["c"],
                    volume=item["volume"],
                    complete=item["complete"],
                )
                for item in payload["candles"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise OandaServiceError(
                502, "invalid_oanda_response", "OANDA returned an unexpected response."
            ) from exc

        return OhlcResponse(
            instrument=str(payload.get("instrument", instrument)),
            granularity=granularity,
            count=len(candles),
            candles=candles,
        )

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        try:
            payload = response.json()
            upstream_message = payload.get("errorMessage") or payload.get("message")
        except ValueError:
            upstream_message = None

        if response.status_code in (401, 403):
            raise OandaServiceError(
                502, "oanda_authentication_failed", "OANDA authentication failed."
            )
        if response.status_code == 404:
            raise OandaServiceError(
                404,
                "instrument_not_found",
                upstream_message or "The requested instrument was not found.",
            )
        if response.status_code == 429:
            raise OandaServiceError(
                503, "oanda_rate_limited", "OANDA rate limit reached. Try again later."
            )
        if response.status_code == 400:
            raise OandaServiceError(
                400, "invalid_oanda_request", upstream_message or "OANDA rejected the request."
            )
        raise OandaServiceError(
            502, "oanda_error", "OANDA returned an unexpected error."
        )
