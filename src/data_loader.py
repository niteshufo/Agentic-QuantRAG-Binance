"""Asynchronous Binance futures market-data ingestion.

The module keeps the exchange client behind a small adapter boundary. Public
market-data calls are read-only, and synthetic fallback data is explicitly
marked so downstream components cannot mistake it for live observations.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import math
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol, cast


DEFAULT_SYMBOL: Final[str] = "BTC/USDT:USDT"
DEFAULT_ORDER_BOOK_LIMIT: Final[int] = 20
DEFAULT_MAX_RETRIES: Final[int] = 3
DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0
DEFAULT_RETRY_BASE_SECONDS: Final[float] = 0.5
SYNTHETIC_BASE_PRICE: Final[float] = 60_000.0
BINANCE_FUTURES_DEPTH_URL: Final[str] = (
    "https://fapi.binance.com/fapi/v1/depth"
)
LOGGER = logging.getLogger(__name__)


class DataSource(StrEnum):
    """Origin of an observation."""

    LIVE = "live"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    """One bid or ask level from an order book."""

    price: float
    quantity: float


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Top-of-book depth snapshot."""

    symbol: str
    timestamp: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    source: DataSource


@dataclass(frozen=True, slots=True)
class FundingRateSnapshot:
    """Current and estimated daily funding for a perpetual contract."""

    symbol: str
    timestamp: datetime
    funding_rate: float
    daily_funding_rate: float
    source: DataSource


@dataclass(frozen=True, slots=True)
class LiquidationStats:
    """Aggregated liquidation activity over a bounded lookback window."""

    symbol: str
    timestamp: datetime
    lookback_start: datetime
    event_count: int
    long_quantity: float
    short_quantity: float
    long_notional: float
    short_notional: float
    source: DataSource


@dataclass(frozen=True, slots=True)
class BinanceMarketSnapshot:
    """Atomic application-level snapshot assembled from Binance endpoints."""

    symbol: str
    captured_at: datetime
    order_book: OrderBookSnapshot
    funding: FundingRateSnapshot
    liquidations: LiquidationStats
    source: DataSource


class ExchangeClient(Protocol):
    """Minimal async CCXT-compatible client surface used by the ingestor."""

    async def close(self) -> None:
        """Close all exchange resources."""

    async def fetch_order_book(
        self,
        symbol: str,
        limit: int = DEFAULT_ORDER_BOOK_LIMIT,
    ) -> Mapping[str, Any]:
        """Fetch an order book."""

    async def fetch_funding_rate(self, symbol: str) -> Mapping[str, Any]:
        """Fetch the current funding rate."""


class LiquidationClient(ExchangeClient, Protocol):
    """Optional CCXT client capability for liquidation history."""

    async def fetch_liquidations(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch liquidation events."""


class BinanceIngestorError(Exception):
    """Base class for expected ingestion failures."""


class BinanceConfigurationError(BinanceIngestorError):
    """The ingestor configuration is invalid."""


class BinanceNetworkError(BinanceIngestorError):
    """A bounded exchange operation failed."""


class BinanceTimeoutError(BinanceNetworkError):
    """An exchange operation exceeded its deadline."""


class BinanceAuthenticationError(BinanceNetworkError):
    """Credentials were rejected by the exchange."""


class BinanceDataQualityError(BinanceIngestorError):
    """The exchange returned malformed or unsafe market data."""


@dataclass(frozen=True, slots=True)
class _RetryPolicy:
    """Bounded retry settings for idempotent read-only operations."""

    max_retries: int
    timeout_seconds: float
    base_delay_seconds: float


class BinanceIngestor:
    """Fetch BTC/USDT perpetual futures market data asynchronously.

    The class is read-only. It never creates orders and never exposes API
    credentials in exceptions or logs. Synthetic fallback is intended for
    replay, development, and unavailable-key scenarios; it must not be used as
    evidence that live market data was received.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret: str | None = None,
        symbol: str = DEFAULT_SYMBOL,
        sandbox: bool = False,
        use_pro: bool = True,
        allow_synthetic_fallback: bool = True,
        order_book_limit: int = DEFAULT_ORDER_BOOK_LIMIT,
        liquidation_lookback: timedelta = timedelta(days=1),
        liquidation_limit: int = 1_000,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base_delay_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        client: ExchangeClient | None = None,
    ) -> None:
        self._validate_configuration(
            symbol=symbol,
            order_book_limit=order_book_limit,
            liquidation_lookback=liquidation_lookback,
            liquidation_limit=liquidation_limit,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
        self._api_key = api_key
        self._secret = secret
        self._symbol = symbol
        self._sandbox = sandbox
        self._use_pro = use_pro
        self._allow_synthetic_fallback = allow_synthetic_fallback
        self._order_book_limit = order_book_limit
        self._liquidation_lookback = liquidation_lookback
        self._liquidation_limit = liquidation_limit
        self._retry_policy = _RetryPolicy(
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            base_delay_seconds=retry_base_delay_seconds,
        )
        self._client = client
        self._owns_client = client is None
        self._closed = False
        self._logger = LOGGER

    @property
    def symbol(self) -> str:
        """Return the configured perpetual futures symbol."""

        return self._symbol

    def set_symbol(self, symbol: str) -> None:
        """Update the read-only market-data instrument for the next fetch."""

        self._validate_configuration(
            symbol=symbol,
            order_book_limit=self._order_book_limit,
            liquidation_lookback=self._liquidation_lookback,
            liquidation_limit=self._liquidation_limit,
            timeout_seconds=self._retry_policy.timeout_seconds,
            max_retries=self._retry_policy.max_retries,
            retry_base_delay_seconds=self._retry_policy.base_delay_seconds,
        )
        self._symbol = symbol

    @property
    def using_synthetic_fallback(self) -> bool:
        """Return whether absent credentials currently force synthetic mode."""

        return not self._has_credentials

    @property
    def _has_credentials(self) -> bool:
        return bool(self._api_key and self._secret)

    async def __aenter__(self) -> BinanceIngestor:
        """Open the ingestor for use in an async context manager."""

        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Close exchange resources."""

        await self.close()

    async def open(self) -> None:
        """Create the configured async CCXT client when live access is possible."""

        self._ensure_open()
        if self._client is not None or not self._has_credentials:
            return

        try:
            module_name = "ccxt.pro" if self._use_pro else "ccxt.async_support"
            ccxt_module: Any = importlib.import_module(module_name)
            exchange_class: Any = getattr(ccxt_module, "binanceusdm")
            client_object: Any = exchange_class(
                {
                    "apiKey": self._api_key,
                    "secret": self._secret,
                    "enableRateLimit": True,
                    "timeout": int(self._retry_policy.timeout_seconds * 1_000),
                    "options": {"defaultType": "future"},
                }
            )
            if self._sandbox:
                client_object.set_sandbox_mode(True)
            self._client = cast(ExchangeClient, client_object)
        except (ImportError, AttributeError, TypeError) as exc:
            if self._allow_synthetic_fallback:
                self._client = None
                return
            raise BinanceConfigurationError(
                "An async CCXT Binance client is unavailable or incompatible."
            ) from exc

    async def close(self) -> None:
        """Close an owned exchange client and release resources."""

        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.close()
        self._client = None

    async def fetch_snapshot(self) -> BinanceMarketSnapshot:
        """Fetch order-book, funding, and liquidation data as one snapshot."""

        self._ensure_open()
        await self.open()
        if self._client is None:
            captured_at = datetime.now(UTC)
            order_book, funding, liquidations = await asyncio.gather(
                self.fetch_order_book(),
                asyncio.to_thread(self._synthetic_funding, timestamp=captured_at),
                asyncio.to_thread(
                    self._synthetic_liquidations,
                    timestamp=captured_at,
                ),
            )
            return BinanceMarketSnapshot(
                symbol=self._symbol,
                captured_at=captured_at,
                order_book=order_book,
                funding=funding,
                liquidations=liquidations,
                source=order_book.source,
            )

        captured_at = datetime.now(UTC)
        try:
            order_book, funding, liquidations = await asyncio.gather(
                self.fetch_order_book(),
                self.fetch_funding_rate(),
                self.fetch_liquidations(),
            )
        except BinanceAuthenticationError:
            if not self._allow_synthetic_fallback:
                raise
            return self._synthetic_snapshot(captured_at=captured_at)
        return BinanceMarketSnapshot(
            symbol=self._symbol,
            captured_at=captured_at,
            order_book=order_book,
            funding=funding,
            liquidations=liquidations,
            source=DataSource.LIVE,
        )

    async def fetch_order_book(self) -> OrderBookSnapshot:
        """Fetch exactly the configured top bid/ask depth levels."""

        self._ensure_open()
        await self.open()
        if self._client is None:
            try:
                return await self._fetch_public_order_book()
            except asyncio.CancelledError:
                raise
            except (BinanceNetworkError, BinanceDataQualityError) as exc:
                if not self._allow_synthetic_fallback:
                    raise
                self._logger.warning(
                    "binance_public_order_book_degraded symbol=%s "
                    "fallback=synthetic reason=%s",
                    self._symbol,
                    str(exc),
                )
                return self._synthetic_order_book()

        try:
            raw = await self._call_exchange(
                "fetch_order_book",
                lambda: self._client.fetch_order_book(
                    self._symbol,
                    limit=self._order_book_limit,
                ),
            )
        except BinanceAuthenticationError:
            if not self._allow_synthetic_fallback:
                raise
            return self._synthetic_order_book()
        timestamp = self._timestamp_from_payload(raw)
        bids = self._parse_levels(raw.get("bids"), "bids")
        asks = self._parse_levels(raw.get("asks"), "asks")
        return OrderBookSnapshot(
            symbol=self._symbol,
            timestamp=timestamp,
            bids=bids[: self._order_book_limit],
            asks=asks[: self._order_book_limit],
            source=DataSource.LIVE,
        )

    async def _fetch_public_order_book(self) -> OrderBookSnapshot:
        """Fetch public USDⓈ-M Futures depth without requiring API keys."""

        exchange_symbol = self._symbol.replace("/", "").replace(":USDT", "")
        url = (
            f"{BINANCE_FUTURES_DEPTH_URL}?symbol={exchange_symbol}"
            f"&limit={self._order_book_limit}"
        )
        raw = await self._call_public_http(url)
        bids = self._parse_levels(raw.get("bids"), "bids")
        asks = self._parse_levels(raw.get("asks"), "asks")
        timestamp = self._timestamp_from_payload(raw)
        return OrderBookSnapshot(
            symbol=self._symbol,
            timestamp=timestamp,
            bids=bids[: self._order_book_limit],
            asks=asks[: self._order_book_limit],
            source=DataSource.LIVE,
        )

    async def _call_public_http(self, url: str) -> Mapping[str, Any]:
        """Run a bounded, read-only public REST request off the event loop."""

        last_error: BaseException | None = None
        for attempt in range(self._retry_policy.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._request_json,
                        url,
                        self._retry_policy.timeout_seconds,
                    ),
                    timeout=self._retry_policy.timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                last_error = BinanceTimeoutError(
                    "Public Binance depth request exceeded its timeout."
                )
            except BinanceDataQualityError:
                raise
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = BinanceNetworkError(
                    "Public Binance depth request failed."
                )
                if attempt >= self._retry_policy.max_retries:
                    raise last_error from exc
            if attempt < self._retry_policy.max_retries:
                await asyncio.sleep(
                    self._retry_policy.base_delay_seconds * (2**attempt)
                )
        raise BinanceNetworkError(
            "Public Binance depth request failed after bounded retries."
        ) from last_error

    @staticmethod
    def _request_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
        """Perform one blocking public GET for execution in a worker thread."""

        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "QuantRAG/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload: Any = json.loads(response.read().decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BinanceDataQualityError(
                "Public Binance depth response was not valid JSON."
            ) from exc
        if not isinstance(payload, Mapping):
            raise BinanceDataQualityError(
                "Public Binance depth response must be a JSON object."
            )
        return payload

    async def fetch_funding_rate(self) -> FundingRateSnapshot:
        """Fetch current funding and estimate the one-day rate.

        Binance perpetual funding is conventionally paid every eight hours;
        the daily estimate therefore multiplies the current rate by three.
        """

        self._ensure_open()
        await self.open()
        if self._client is None:
            return self._synthetic_funding()

        try:
            raw = await self._call_exchange(
                "fetch_funding_rate",
                lambda: self._client.fetch_funding_rate(self._symbol),
            )
        except BinanceAuthenticationError:
            if not self._allow_synthetic_fallback:
                raise
            return self._synthetic_funding()
        funding_rate = self._finite_float(raw.get("fundingRate"), "fundingRate")
        timestamp = self._timestamp_from_payload(raw)
        return FundingRateSnapshot(
            symbol=self._symbol,
            timestamp=timestamp,
            funding_rate=funding_rate,
            daily_funding_rate=funding_rate * 3.0,
            source=DataSource.LIVE,
        )

    async def fetch_liquidations(self) -> LiquidationStats:
        """Aggregate liquidation events over the configured lookback period."""

        self._ensure_open()
        await self.open()
        if self._client is None:
            return self._synthetic_liquidations()

        fetch_method = getattr(self._client, "fetch_liquidations", None)
        if not callable(fetch_method):
            if self._allow_synthetic_fallback:
                return self._synthetic_liquidations()
            raise BinanceConfigurationError(
                "The configured CCXT client does not support fetch_liquidations."
            )

        since = int(
            (datetime.now(UTC) - self._liquidation_lookback).timestamp() * 1_000
        )
        try:
            raw_events = await self._call_exchange(
                "fetch_liquidations",
                lambda: cast(
                    Awaitable[Sequence[Mapping[str, Any]]],
                    fetch_method(
                        self._symbol,
                        since=since,
                        limit=self._liquidation_limit,
                    ),
                ),
            )
        except BinanceAuthenticationError:
            if not self._allow_synthetic_fallback:
                raise
            return self._synthetic_liquidations()
        return self._aggregate_liquidations(raw_events)

    async def _call_exchange(
        self,
        operation: str,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run one idempotent exchange read with bounded retry behavior."""

        last_error: BaseException | None = None
        for attempt in range(self._retry_policy.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    call(),
                    timeout=self._retry_policy.timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                last_error = BinanceTimeoutError(
                    f"{operation} exceeded the configured timeout."
                )
            except BinanceAuthenticationError:
                raise
            except Exception as exc:
                if self._is_authentication_error(exc):
                    raise BinanceAuthenticationError(
                        f"{operation} was rejected by Binance credentials."
                    ) from exc
                if not self._is_retryable_error(exc) or attempt >= self._retry_policy.max_retries:
                    raise BinanceNetworkError(
                        f"{operation} failed after bounded retry handling."
                    ) from exc
                last_error = exc

            if attempt < self._retry_policy.max_retries:
                await asyncio.sleep(
                    self._retry_policy.base_delay_seconds * (2**attempt)
                )

        raise BinanceNetworkError(f"{operation} failed.") from last_error

    def _aggregate_liquidations(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> LiquidationStats:
        long_quantity = 0.0
        short_quantity = 0.0
        long_notional = 0.0
        short_notional = 0.0
        for event in events:
            quantity = self._first_finite(
                event,
                ("contracts", "amount", "quantity", "qty"),
                default=0.0,
            )
            price = self._first_finite(
                event,
                ("price",),
                default=0.0,
            )
            side = str(event.get("side", "")).lower()
            notional = quantity * price
            if side == "buy":
                short_quantity += quantity
                short_notional += notional
            elif side == "sell":
                long_quantity += quantity
                long_notional += notional

        timestamp = datetime.now(UTC)
        return LiquidationStats(
            symbol=self._symbol,
            timestamp=timestamp,
            lookback_start=timestamp - self._liquidation_lookback,
            event_count=len(events),
            long_quantity=long_quantity,
            short_quantity=short_quantity,
            long_notional=long_notional,
            short_notional=short_notional,
            source=DataSource.LIVE,
        )

    def _synthetic_snapshot(
        self,
        *,
        captured_at: datetime | None = None,
    ) -> BinanceMarketSnapshot:
        timestamp = captured_at or datetime.now(UTC)
        return BinanceMarketSnapshot(
            symbol=self._symbol,
            captured_at=timestamp,
            order_book=self._synthetic_order_book(timestamp=timestamp),
            funding=self._synthetic_funding(timestamp=timestamp),
            liquidations=self._synthetic_liquidations(timestamp=timestamp),
            source=DataSource.SYNTHETIC,
        )

    def _synthetic_order_book(
        self,
        *,
        timestamp: datetime | None = None,
    ) -> OrderBookSnapshot:
        timestamp = timestamp or datetime.now(UTC)
        base_price = self._synthetic_price(timestamp)
        spread = base_price * 0.0002
        bids = tuple(
            OrderBookLevel(
                price=base_price - spread / 2 - index * base_price * 0.00001,
                quantity=0.01 + index * 0.002,
            )
            for index in range(self._order_book_limit)
        )
        asks = tuple(
            OrderBookLevel(
                price=base_price + spread / 2 + index * base_price * 0.00001,
                quantity=0.01 + index * 0.002,
            )
            for index in range(self._order_book_limit)
        )
        return OrderBookSnapshot(
            symbol=self._symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
            source=DataSource.SYNTHETIC,
        )

    def _synthetic_funding(
        self,
        *,
        timestamp: datetime | None = None,
    ) -> FundingRateSnapshot:
        timestamp = timestamp or datetime.now(UTC)
        funding_rate = self._synthetic_signed_value(timestamp, scale=0.0001)
        return FundingRateSnapshot(
            symbol=self._symbol,
            timestamp=timestamp,
            funding_rate=funding_rate,
            daily_funding_rate=funding_rate * 3.0,
            source=DataSource.SYNTHETIC,
        )

    def _synthetic_liquidations(
        self,
        *,
        timestamp: datetime | None = None,
    ) -> LiquidationStats:
        timestamp = timestamp or datetime.now(UTC)
        imbalance = self._synthetic_signed_value(timestamp, scale=1.0)
        long_quantity = 0.15 * (1.0 + imbalance)
        short_quantity = 0.15 * (1.0 - imbalance)
        base_price = self._synthetic_price(timestamp)
        return LiquidationStats(
            symbol=self._symbol,
            timestamp=timestamp,
            lookback_start=timestamp - self._liquidation_lookback,
            event_count=6,
            long_quantity=long_quantity,
            short_quantity=short_quantity,
            long_notional=long_quantity * base_price,
            short_notional=short_quantity * base_price,
            source=DataSource.SYNTHETIC,
        )

    @staticmethod
    def _validate_configuration(
        *,
        symbol: str,
        order_book_limit: int,
        liquidation_lookback: timedelta,
        liquidation_limit: int,
        timeout_seconds: float,
        max_retries: int,
        retry_base_delay_seconds: float,
    ) -> None:
        if not symbol.strip():
            raise BinanceConfigurationError("symbol must not be empty.")
        if order_book_limit != DEFAULT_ORDER_BOOK_LIMIT:
            raise BinanceConfigurationError(
                f"order_book_limit must be exactly {DEFAULT_ORDER_BOOK_LIMIT}."
            )
        if liquidation_lookback <= timedelta(0):
            raise BinanceConfigurationError("liquidation_lookback must be positive.")
        if liquidation_limit <= 0:
            raise BinanceConfigurationError("liquidation_limit must be positive.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise BinanceConfigurationError("timeout_seconds must be finite and positive.")
        if max_retries < 0:
            raise BinanceConfigurationError("max_retries must not be negative.")
        if (
            not math.isfinite(retry_base_delay_seconds)
            or retry_base_delay_seconds < 0
        ):
            raise BinanceConfigurationError(
                "retry_base_delay_seconds must be finite and non-negative."
            )

    def _parse_levels(
        self,
        raw_levels: Any,
        side: str,
    ) -> tuple[OrderBookLevel, ...]:
        if not isinstance(raw_levels, Sequence) or isinstance(raw_levels, (str, bytes)):
            raise BinanceDataQualityError(f"Binance returned invalid {side} levels.")
        parsed: list[OrderBookLevel] = []
        for level in raw_levels[: self._order_book_limit]:
            if not isinstance(level, Sequence) or len(level) < 2:
                raise BinanceDataQualityError(
                    f"Binance returned an invalid {side} level."
                )
            price = self._finite_float(level[0], f"{side}.price")
            quantity = self._finite_float(level[1], f"{side}.quantity")
            if price <= 0 or quantity < 0:
                raise BinanceDataQualityError(
                    f"Binance returned non-positive {side} values."
                )
            parsed.append(OrderBookLevel(price=price, quantity=quantity))
        if not parsed:
            raise BinanceDataQualityError(f"Binance returned no {side} levels.")
        return tuple(parsed)

    @classmethod
    def _timestamp_from_payload(cls, payload: Mapping[str, Any]) -> datetime:
        raw_timestamp = payload.get("timestamp")
        if raw_timestamp is None:
            return datetime.now(UTC)
        timestamp_ms = cls._finite_float(raw_timestamp, "timestamp")
        return datetime.fromtimestamp(timestamp_ms / 1_000.0, tz=UTC)

    @staticmethod
    def _finite_float(value: Any, field: str) -> float:
        try:
            converted = float(value)
        except (TypeError, ValueError) as exc:
            raise BinanceDataQualityError(f"{field} is not numeric.") from exc
        if not math.isfinite(converted):
            raise BinanceDataQualityError(f"{field} is not finite.")
        return converted

    @classmethod
    def _first_finite(
        cls,
        mapping: Mapping[str, Any],
        keys: Sequence[str],
        *,
        default: float,
    ) -> float:
        for key in keys:
            if mapping.get(key) is not None:
                return cls._finite_float(mapping[key], key)
        return default

    @staticmethod
    def _is_authentication_error(error: BaseException) -> bool:
        message = str(error).lower()
        markers = (
            "authentication",
            "invalid api-key",
            "invalid api key",
            "api-key format invalid",
            "permission",
            "invalid signature",
            "401",
            "403",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _is_retryable_error(error: BaseException) -> bool:
        message = str(error).lower()
        non_retryable_markers = (
            "invalid symbol",
            "bad request",
            "permission",
            "authentication",
            "invalid api",
            "invalid signature",
            "401",
            "403",
        )
        return not any(marker in message for marker in non_retryable_markers)

    def _ensure_open(self) -> None:
        if self._closed:
            raise BinanceIngestorError("BinanceIngestor is closed.")

    def _synthetic_price(self, timestamp: datetime) -> float:
        return SYNTHETIC_BASE_PRICE * (
            1.0 + 0.01 * self._synthetic_signed_value(timestamp, scale=1.0)
        )

    def _synthetic_signed_value(self, timestamp: datetime, *, scale: float) -> float:
        seed = f"{self._symbol}:{timestamp.strftime('%Y-%m-%d-%H')}".encode()
        digest = hashlib.sha256(seed).digest()
        normalized = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        return (normalized * 2.0 - 1.0) * scale


__all__ = [
    "BinanceAuthenticationError",
    "BinanceConfigurationError",
    "BinanceDataQualityError",
    "BinanceIngestor",
    "BinanceIngestorError",
    "BinanceMarketSnapshot",
    "BinanceNetworkError",
    "BinanceTimeoutError",
    "DataSource",
    "FundingRateSnapshot",
    "LiquidationStats",
    "OrderBookLevel",
    "OrderBookSnapshot",
]
