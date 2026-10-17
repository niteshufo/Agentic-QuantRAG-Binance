"""NumPy-based market microstructure and execution-risk calculations."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np
from numpy.typing import NDArray

from .data_loader import OrderBookSnapshot


LOGGER = logging.getLogger(__name__)

FloatArray = NDArray[np.float64]

DEFAULT_IMPACT_COEFFICIENT: Final[float] = 0.5
DEFAULT_MAX_ORDER_SIZE: Final[float] = 1.0
DEFAULT_MIN_ORDER_SIZE: Final[float] = 0.0
DEFAULT_MAX_PARTICIPATION_RATE: Final[float] = 0.10
DEFAULT_REGIME_VOLATILITY_THRESHOLD: Final[float] = 0.02
DEFAULT_REGIME_VOLUME_THRESHOLD: Final[float] = 1.0
EPSILON: Final[float] = 1e-12


class MicrostructureEngineError(Exception):
    """Base class for expected microstructure failures."""


class MicrostructureConfigurationError(MicrostructureEngineError):
    """The engine configuration is invalid."""


class MicrostructureDataQualityError(MicrostructureEngineError):
    """Input market data is missing, malformed, or unsafe."""


class EmptyMarketMatrixError(MicrostructureDataQualityError):
    """An order-book matrix has no usable rows."""


class InvalidOrderSizeError(MicrostructureDataQualityError):
    """The planned order size is invalid or exceeds configured limits."""


class MarketRegime(StrEnum):
    """Rule-based volatility/liquidity regime."""

    CALM = "calm"
    NORMAL = "normal"
    ELEVATED_VOLATILITY = "elevated_volatility"
    LIQUIDITY_STRESSED = "liquidity_stressed"
    HIGH_RISK = "high_risk"


@dataclass(frozen=True, slots=True)
class SlippageEstimate:
    """Predicted execution slippage for a signed order."""

    symbol: str
    order_size: float
    side: str
    best_price: float
    volume_weighted_execution_price: float
    book_slippage_bps: float
    expected_impact_bps: float
    total_expected_slippage_bps: float
    consumed_volume: float
    available_top_book_volume: float
    participation_rate: float
    volatility_24h: float
    impact_coefficient: float


@dataclass(frozen=True, slots=True)
class MarketRegimeAssessment:
    """Risk boundary and scale output for a market regime."""

    regime: MarketRegime
    volatility_proxy: float
    volume_proxy: float
    volatility_scalar: float
    liquidity_scalar: float
    risk_scalar: float
    allowed_max_order_size: float
    max_participation_rate: float


class MicrostructureEngine:
    """Calculate order-book slippage, market impact, and regime boundaries.

    The square-root impact estimate is:

        expected_impact = Y * sigma * sqrt(order_size / daily_volume)

    The returned impact is converted to basis points by multiplying the
    decimal impact by 10,000. ``order_size`` is signed: positive values are
    buy orders that consume asks, and negative values are sell orders that
    consume bids. The impact model uses the absolute quantity.
    """

    def __init__(
        self,
        *,
        volatility_24h: float,
        impact_coefficient: float = DEFAULT_IMPACT_COEFFICIENT,
        max_order_size: float = DEFAULT_MAX_ORDER_SIZE,
        max_participation_rate: float = DEFAULT_MAX_PARTICIPATION_RATE,
        volatility_threshold: float = DEFAULT_REGIME_VOLATILITY_THRESHOLD,
        volume_threshold: float = DEFAULT_REGIME_VOLUME_THRESHOLD,
        logger: logging.Logger | None = None,
    ) -> None:
        self._validate_finite_non_negative(volatility_24h, "volatility_24h")
        self._validate_finite_non_negative(
            impact_coefficient,
            "impact_coefficient",
        )
        self._validate_positive(max_order_size, "max_order_size")
        self._validate_positive(
            max_participation_rate,
            "max_participation_rate",
        )
        if max_participation_rate > 1.0:
            raise MicrostructureConfigurationError(
                "max_participation_rate must not exceed 1.0."
            )
        self._validate_positive(volatility_threshold, "volatility_threshold")
        self._validate_positive(volume_threshold, "volume_threshold")
        self._volatility_24h = volatility_24h
        self._impact_coefficient = impact_coefficient
        self._max_order_size = max_order_size
        self._max_participation_rate = max_participation_rate
        self._volatility_threshold = volatility_threshold
        self._volume_threshold = volume_threshold
        self._allowed_max_order_size = max_order_size
        self._dynamic_max_participation_rate = max_participation_rate
        self._logger = logger or LOGGER

    @property
    def volatility_24h(self) -> float:
        """Return the current 24-hour volatility proxy."""

        return self._volatility_24h

    def calculate_expected_slippage(
        self,
        order_size: float,
        order_book: OrderBookSnapshot,
    ) -> SlippageEstimate:
        """Estimate top-book slippage plus square-root market impact in bps.

        A positive ``order_size`` consumes asks; a negative value consumes
        bids. The order must fit within the configured top-book participation
        and maximum-size limits.
        """

        self._validate_order_size(order_size)
        side = "buy" if order_size > 0 else "sell"
        levels = order_book.asks if order_size > 0 else order_book.bids
        prices, quantities = self._book_arrays(levels, side)
        requested_quantity = abs(order_size)
        available_volume = float(np.sum(quantities, dtype=np.float64))
        if available_volume <= EPSILON:
            raise EmptyMarketMatrixError(
                f"{side} order-book volume is empty for {order_book.symbol}."
            )
        participation_rate = requested_quantity / available_volume
        if participation_rate > self._dynamic_max_participation_rate:
            raise InvalidOrderSizeError(
                f"Order participation {participation_rate:.6f} exceeds "
                f"maximum {self._dynamic_max_participation_rate:.6f}."
            )

        consumed_quantity = min(requested_quantity, available_volume)
        remaining = consumed_quantity
        notional = 0.0
        for price, quantity in zip(prices, quantities, strict=True):
            level_quantity = min(float(quantity), remaining)
            notional += float(price) * level_quantity
            remaining -= level_quantity
            if remaining <= EPSILON:
                break
        if remaining > EPSILON:
            raise InvalidOrderSizeError(
                "Requested order cannot be fully represented by the supplied "
                "top-of-book depth."
            )

        best_price = float(prices[0])
        execution_price = notional / consumed_quantity
        book_slippage_bps = (
            abs(execution_price - best_price) / best_price * 10_000.0
        )
        daily_volume_proxy = available_volume
        expected_impact_decimal = self._square_root_impact(
            order_size=consumed_quantity,
            daily_volume=daily_volume_proxy,
            volatility=self._volatility_24h,
        )
        expected_impact_bps = expected_impact_decimal * 10_000.0
        total_slippage_bps = book_slippage_bps + expected_impact_bps
        self._logger.info(
            "slippage_estimate symbol=%s side=%s order_size=%.8f "
            "book_slippage_bps=%.4f impact_bps=%.4f total_bps=%.4f "
            "participation=%.6f source=%s",
            order_book.symbol,
            side,
            order_size,
            book_slippage_bps,
            expected_impact_bps,
            total_slippage_bps,
            participation_rate,
            order_book.source,
        )
        return SlippageEstimate(
            symbol=order_book.symbol,
            order_size=order_size,
            side=side,
            best_price=best_price,
            volume_weighted_execution_price=execution_price,
            book_slippage_bps=book_slippage_bps,
            expected_impact_bps=expected_impact_bps,
            total_expected_slippage_bps=total_slippage_bps,
            consumed_volume=consumed_quantity,
            available_top_book_volume=available_volume,
            participation_rate=participation_rate,
            volatility_24h=self._volatility_24h,
            impact_coefficient=self._impact_coefficient,
        )

    def evaluate_market_regime(
        self,
        volatility_proxy: float,
        volume_proxy: float,
    ) -> MarketRegimeAssessment:
        """Classify volatility/liquidity risk and update allowed scale.

        ``volatility_proxy`` is a decimal 24-hour volatility measure. The
        ``volume_proxy`` is normalized to the configured baseline volume:
        values below ``volume_threshold`` indicate deteriorating liquidity.
        """

        self._validate_finite_non_negative(volatility_proxy, "volatility_proxy")
        self._validate_finite_non_negative(volume_proxy, "volume_proxy")
        self._volatility_24h = volatility_proxy

        volatility_ratio = volatility_proxy / self._volatility_threshold
        volume_ratio = volume_proxy / self._volume_threshold
        if volatility_ratio >= 2.0 and volume_ratio < 1.0:
            regime = MarketRegime.HIGH_RISK
            volatility_scalar = 0.25
            liquidity_scalar = 0.25
        elif volatility_ratio >= 2.0:
            regime = MarketRegime.ELEVATED_VOLATILITY
            volatility_scalar = 0.35
            liquidity_scalar = 0.75
        elif volume_ratio < 0.5:
            regime = MarketRegime.LIQUIDITY_STRESSED
            volatility_scalar = 0.50
            liquidity_scalar = 0.35
        elif volatility_ratio <= 0.75 and volume_ratio >= 1.0:
            regime = MarketRegime.CALM
            volatility_scalar = 1.00
            liquidity_scalar = 1.00
        else:
            regime = MarketRegime.NORMAL
            volatility_scalar = 0.75
            liquidity_scalar = 0.75

        risk_scalar = min(volatility_scalar, liquidity_scalar)
        allowed_max_order_size = self._max_order_size * risk_scalar
        dynamic_participation = self._max_participation_rate * risk_scalar
        self._allowed_max_order_size = allowed_max_order_size
        self._dynamic_max_participation_rate = dynamic_participation
        assessment = MarketRegimeAssessment(
            regime=regime,
            volatility_proxy=volatility_proxy,
            volume_proxy=volume_proxy,
            volatility_scalar=volatility_scalar,
            liquidity_scalar=liquidity_scalar,
            risk_scalar=risk_scalar,
            allowed_max_order_size=allowed_max_order_size,
            max_participation_rate=dynamic_participation,
        )
        self._logger.info(
            "market_regime regime=%s volatility=%.8f volume=%.8f "
            "risk_scalar=%.4f allowed_max_order_size=%.8f "
            "max_participation=%.6f",
            regime,
            volatility_proxy,
            volume_proxy,
            risk_scalar,
            allowed_max_order_size,
            dynamic_participation,
        )
        return assessment

    def _book_arrays(
        self,
        levels: tuple[object, ...],
        side: str,
    ) -> tuple[FloatArray, FloatArray]:
        if not levels:
            raise EmptyMarketMatrixError(f"{side} order-book matrix is empty.")
        prices = np.asarray(
            [getattr(level, "price", math.nan) for level in levels],
            dtype=np.float64,
        )
        quantities = np.asarray(
            [getattr(level, "quantity", math.nan) for level in levels],
            dtype=np.float64,
        )
        if prices.ndim != 1 or quantities.ndim != 1:
            raise MicrostructureDataQualityError(
                f"{side} order-book arrays must be one-dimensional."
            )
        if prices.shape != quantities.shape or prices.size == 0:
            raise EmptyMarketMatrixError(
                f"{side} order-book arrays have incompatible or empty shapes."
            )
        if not np.all(np.isfinite(prices)) or not np.all(np.isfinite(quantities)):
            raise MicrostructureDataQualityError(
                f"{side} order-book arrays contain non-finite values."
            )
        if np.any(prices <= 0.0) or np.any(quantities < 0.0):
            raise MicrostructureDataQualityError(
                f"{side} order-book prices must be positive and quantities "
                "must be non-negative."
            )
        return prices, quantities

    def _square_root_impact(
        self,
        *,
        order_size: float,
        daily_volume: float,
        volatility: float,
    ) -> float:
        self._validate_positive(order_size, "order_size")
        self._validate_positive(daily_volume, "daily_volume")
        self._validate_finite_non_negative(volatility, "volatility")
        participation = order_size / daily_volume
        if not math.isfinite(participation) or participation <= 0.0:
            raise MicrostructureDataQualityError(
                "Square-root impact participation must be finite and positive."
            )
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            try:
                impact = self._impact_coefficient * volatility * np.sqrt(
                    np.float64(participation)
                )
            except FloatingPointError as exc:
                raise MicrostructureDataQualityError(
                    "Square-root impact calculation overflowed or was invalid."
                ) from exc
        impact_value = float(impact)
        if not math.isfinite(impact_value) or impact_value < 0.0:
            raise MicrostructureDataQualityError(
                "Square-root impact produced an invalid result."
            )
        return impact_value

    def _validate_order_size(self, order_size: float) -> None:
        if not math.isfinite(order_size) or abs(order_size) <= EPSILON:
            raise InvalidOrderSizeError(
                "order_size must be finite and non-zero."
            )
        if abs(order_size) > self._allowed_max_order_size:
            raise InvalidOrderSizeError(
                f"order_size {order_size:.8f} exceeds configured maximum "
                f"{self._allowed_max_order_size:.8f}."
            )

    @staticmethod
    def _validate_positive(value: float, field: str) -> None:
        if not math.isfinite(value) or value <= 0.0:
            raise MicrostructureConfigurationError(
                f"{field} must be finite and positive."
            )

    @staticmethod
    def _validate_finite_non_negative(value: float, field: str) -> None:
        if not math.isfinite(value) or value < 0.0:
            raise MicrostructureConfigurationError(
                f"{field} must be finite and non-negative."
            )


__all__ = [
    "EmptyMarketMatrixError",
    "InvalidOrderSizeError",
    "MarketRegime",
    "MarketRegimeAssessment",
    "MicrostructureConfigurationError",
    "MicrostructureDataQualityError",
    "MicrostructureEngine",
    "MicrostructureEngineError",
    "SlippageEstimate",
]
