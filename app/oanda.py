import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.models import (
    Candle,
    HistoricalOhlcQuery,
    InstrumentSummary,
    InstrumentsResponse,
    OhlcQuery,
    OhlcResponse,
    ResearchAccountContext,
    ResearchContextResponse,
)


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

    async def get_ohlc(self, query: OhlcQuery) -> OhlcResponse:
        url = f"{self.settings.oanda_base_url}/instruments/{query.instrument}/candles"
        headers = {
            "Authorization": f"Bearer {self.settings.oanda_token.get_secret_value()}",
            "Accept-Datetime-Format": "RFC3339",
        }
        params = {
            "granularity": query.granularity.value,
            "price": "M",
        }
        if query.daily_alignment is not None:
            params["dailyAlignment"] = str(query.daily_alignment)
        if query.alignment_timezone is not None:
            params["alignmentTimezone"] = query.alignment_timezone
        if query.smooth is not None:
            params["smooth"] = "true" if query.smooth else "false"

        if query.count is not None:
            params["count"] = str(query.count)
        else:
            params["from"] = self._format_rfc3339(query.from_time)
            if query.to_time is not None:
                params["to"] = self._format_rfc3339(query.to_time)

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
            instrument=str(payload.get("instrument", query.instrument)),
            granularity=query.granularity,
            count=len(candles),
            candles=candles,
        )

    async def get_historical_chunk(
        self,
        query: HistoricalOhlcQuery,
        from_time: datetime,
        *,
        include_first: bool,
    ) -> list[Candle]:
        """Fetch one OANDA history page with retry for transient upstream failures."""
        url = f"{self.settings.oanda_base_url}/instruments/{query.instrument}/candles"
        headers = {
            "Authorization": f"Bearer {self.settings.oanda_token.get_secret_value()}",
            "Accept-Datetime-Format": "RFC3339",
        }
        params = {
            "granularity": query.granularity.value,
            "price": "M",
            "count": str(self.settings.historical_page_size),
            "from": self._format_rfc3339(from_time),
            "includeFirst": "true" if include_first else "false",
        }

        response: httpx.Response | None = None
        max_attempts = 3
        transient_statuses = {429, 502, 503, 504}

        for attempt in range(max_attempts):
            try:
                response = await self.client.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.settings.oanda_timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(
                        self.settings.historical_chunk_delay_seconds * (attempt + 1)
                    )
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    raise OandaServiceError(
                        504,
                        "oanda_timeout",
                        "OANDA did not respond before the timeout after retries.",
                    ) from exc
                raise OandaServiceError(
                    503,
                    "oanda_unavailable",
                    "OANDA is currently unavailable after retries.",
                ) from exc

            if (
                response.status_code in transient_statuses
                and attempt < max_attempts - 1
            ):
                await asyncio.sleep(
                    self.settings.historical_chunk_delay_seconds * (attempt + 1)
                )
                continue
            break

        if response is None:
            raise OandaServiceError(
                503, "oanda_unavailable", "OANDA is currently unavailable."
            )

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

        return candles

    async def iter_historical_candles(
        self,
        query: HistoricalOhlcQuery,
        first_chunk: list[Candle],
    ) -> AsyncIterator[Candle]:
        """Yield history page by page with a configurable delay."""
        chunk = first_chunk
        full_page_size = self.settings.historical_page_size

        while chunk:
            reached_until = False
            for candle in chunk:
                if candle.time >= query.until_time:
                    reached_until = True
                    break
                if candle.time >= query.from_time:
                    yield candle

            if reached_until or len(chunk) < full_page_size:
                return

            last_time = chunk[-1].time
            if last_time >= query.until_time:
                return

            await asyncio.sleep(self.settings.historical_chunk_delay_seconds)
            next_chunk = await self.get_historical_chunk(
                query,
                last_time,
                include_first=False,
            )
            if next_chunk and next_chunk[-1].time <= last_time:
                raise OandaServiceError(
                    502,
                    "invalid_oanda_response",
                    "OANDA pagination did not advance.",
                )

            # OANDA applies includeFirst=false to the candle covered by from.
            # A full continuation page can therefore contain one fewer candle
            # than the requested count. Treat that as full rather than EOF.
            chunk = next_chunk
            full_page_size = max(1, self.settings.historical_page_size - 1)

    async def get_instruments(self, account_id: str) -> InstrumentsResponse:
        url = f"{self.settings.oanda_base_url}/accounts/{account_id}/instruments"
        headers = {
            "Authorization": f"Bearer {self.settings.oanda_token.get_secret_value()}",
        }

        try:
            response = await self.client.get(
                url,
                headers=headers,
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

        if response.status_code == 404:
            raise OandaServiceError(
                502,
                "oanda_account_not_found",
                "Configured OANDA account was not found.",
            )
        if response.is_error:
            self._raise_for_error(response)

        try:
            payload: dict[str, Any] = response.json()
            raw_instruments = payload["instruments"]
            if not isinstance(raw_instruments, list):
                raise TypeError

            instruments: list[InstrumentSummary] = []
            for item in raw_instruments:
                if not isinstance(item, dict):
                    raise TypeError
                name = item["name"]
                display_name = item["displayName"]
                instrument_type = item["type"]
                if not all(
                    isinstance(value, str)
                    for value in (name, display_name, instrument_type)
                ):
                    raise TypeError
                instruments.append(
                    InstrumentSummary(
                        name=name,
                        display_name=display_name,
                        type=instrument_type,
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise OandaServiceError(
                502,
                "invalid_oanda_response",
                "OANDA returned an unexpected response.",
            ) from exc

        return InstrumentsResponse(
            environment=self.settings.oanda_environment,
            count=len(instruments),
            instruments=instruments,
        )

    async def get_research_context(self) -> ResearchContextResponse:
        headers = {
            "Authorization": f"Bearer {self.settings.oanda_token.get_secret_value()}",
            "Accept-Datetime-Format": "RFC3339",
        }
        try:
            response = await self.client.get(
                f"{self.settings.oanda_base_url}/accounts",
                headers=headers,
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
            if response.status_code in (401, 403):
                raise OandaServiceError(
                    502, "oanda_authentication_failed", "OANDA authentication failed."
                )
            if response.status_code == 429:
                raise OandaServiceError(
                    503, "oanda_rate_limited", "OANDA rate limit reached. Try again later."
                )
            raise OandaServiceError(
                502, "oanda_context_error", "OANDA context lookup failed."
            )

        try:
            payload = response.json()
            raw_accounts = payload["accounts"]
            if not isinstance(raw_accounts, list):
                raise TypeError

            contexts: dict[tuple[str, str], ResearchAccountContext] = {}
            for account in raw_accounts:
                if not isinstance(account, dict):
                    raise TypeError
                account_id = account["id"]
                if not isinstance(account_id, str):
                    raise TypeError

                parts = account_id.split("-")
                if len(parts) != 4 or not all(part.isdigit() for part in parts):
                    raise ValueError

                site_id, division_id = parts[0], parts[1]
                contexts[(site_id, division_id)] = ResearchAccountContext(
                    site_id=site_id,
                    division_id=division_id,
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise OandaServiceError(
                502,
                "invalid_oanda_response",
                "OANDA returned an unexpected response.",
            ) from exc

        return ResearchContextResponse(
            environment=self.settings.oanda_environment,
            upstream=self.settings.oanda_host,
            accounts=[contexts[key] for key in sorted(contexts)],
        )

    @staticmethod
    def _format_rfc3339(value: datetime | None) -> str:
        if value is None:  # Guarded by OhlcQuery validation.
            raise ValueError("range timestamp is required")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

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
        if response.status_code in (400, 422):
            raise OandaServiceError(
                response.status_code,
                "invalid_oanda_request",
                upstream_message or "OANDA rejected the request.",
            )
        if response.status_code == 502:
            raise OandaServiceError(
                502, "oanda_bad_gateway", "OANDA returned a bad gateway response."
            )
        if response.status_code == 503:
            raise OandaServiceError(
                503, "oanda_unavailable", "OANDA is currently unavailable."
            )
        if response.status_code == 504:
            raise OandaServiceError(
                504, "oanda_gateway_timeout", "OANDA upstream request timed out."
            )
        raise OandaServiceError(
            502, "oanda_error", "OANDA returned an unexpected error."
        )
