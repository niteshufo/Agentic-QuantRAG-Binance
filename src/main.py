"""Production-oriented asynchronous runtime for Agentic-QuantRAG-Binance."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .data_loader import BinanceIngestor, BinanceMarketSnapshot
from .math_engine import (
    DEFAULT_MAX_PARTICIPATION_RATE,
    InvalidOrderSizeError,
    MarketRegimeAssessment,
    MicrostructureEngine,
    SlippageEstimate,
)
from .rag_engine import MarketSentimentRAGEngine, SentimentAnalysis


LOGGER = logging.getLogger("agentic_quant_rag.runtime")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
RUNTIME_DIRECTORY = Path(
    r"C:\Users\nites\Desktop\Agentic-QuantRAG-Binance\data\runtime"
)
LATEST_STATE_PATH = RUNTIME_DIRECTORY / "latest_state.json"
EXECUTION_RECORDS_PATH = RUNTIME_DIRECTORY / "execution_records.jsonl"
DEFAULT_MACRO_PHRASES: tuple[str, ...] = (
    "regulatory impact on liquidity",
    "institutional volume shifts",
    "network stability and market risk",
)
DEFAULT_BLOCKED_IMPACT_BPS = 14.25
DEFAULT_BLOCKED_BOOK_SLIPPAGE_BPS = 8.40
DEFAULT_BLOCKED_TOTAL_EXECUTION_COST_BPS = 22.65
MIN_TWAP_SLICE_SIZE = 0.01


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Validated runtime settings loaded from environment variables."""

    api_key: str | None
    secret: str | None
    symbol: str
    target_order_size: float
    max_order_size: float
    loop_interval_seconds: float
    request_timeout_seconds: float
    max_retries: int
    use_ccxt_pro: bool
    sandbox: bool
    allow_synthetic_fallback: bool
    persist_directory: Path

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
    ) -> RuntimeConfig:
        """Build configuration without exposing secret values in logs."""

        target_order_size = _positive_float(
            environment.get("TARGET_ORDER_SIZE", "5.0"),
            "TARGET_ORDER_SIZE",
        )
        max_order_size = _positive_float(
            environment.get("MAX_ORDER_SIZE", str(target_order_size)),
            "MAX_ORDER_SIZE",
        )
        if max_order_size < target_order_size:
            raise ValueError("MAX_ORDER_SIZE must cover TARGET_ORDER_SIZE.")
        return cls(
            api_key=_optional_value(environment.get("BINANCE_API_KEY")),
            secret=_optional_value(environment.get("BINANCE_SECRET")),
            symbol=environment.get("BINANCE_SYMBOL", "BTC/USDT:USDT"),
            target_order_size=target_order_size,
            max_order_size=max_order_size,
            loop_interval_seconds=_positive_float(
                environment.get("LOOP_INTERVAL_SECONDS", "30.0"),
                "LOOP_INTERVAL_SECONDS",
            ),
            request_timeout_seconds=_positive_float(
                environment.get("REQUEST_TIMEOUT_SECONDS", "10.0"),
                "REQUEST_TIMEOUT_SECONDS",
            ),
            max_retries=_non_negative_int(
                environment.get("MAX_RETRIES", "3"),
                "MAX_RETRIES",
            ),
            use_ccxt_pro=_boolean(environment.get("USE_CCXT_PRO", "true")),
            sandbox=_boolean(environment.get("BINANCE_SANDBOX", "false")),
            allow_synthetic_fallback=_boolean(
                environment.get("ALLOW_SYNTHETIC_FALLBACK", "true")
            ),
            persist_directory=Path(
                environment.get(
                    "RAG_PERSIST_DIRECTORY",
                    str(PROJECT_ROOT / "data" / "chroma_db"),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ExecutionSimulation:
    """Fail-safe result for one direct or split paper-execution plan."""

    status: str
    estimates: tuple[SlippageEstimate, ...]
    slice_count: int
    reason: str
    message: str

    @property
    def is_blocked(self) -> bool:
        return self.status == "Blocked"


class QuantRAGRuntime:
    """Own the lifecycle and heartbeat-monitored decision loop."""

    def __init__(self, config: RuntimeConfig) -> None:
        self._config = config
        self._stop_event = asyncio.Event()
        self._heartbeat_at: datetime | None = None
        self._iteration = 0

    async def run(self) -> None:
        """Initialize engines, warm context, and run until shutdown."""

        self._install_signal_handlers()
        ingestor = BinanceIngestor(
            api_key=self._config.api_key,
            secret=self._config.secret,
            symbol=self._config.symbol,
            sandbox=self._config.sandbox,
            use_pro=self._config.use_ccxt_pro,
            allow_synthetic_fallback=self._config.allow_synthetic_fallback,
            timeout_seconds=self._config.request_timeout_seconds,
            max_retries=self._config.max_retries,
        )
        rag_engine = MarketSentimentRAGEngine(
            persist_directory=self._config.persist_directory
        )
        math_engine = MicrostructureEngine(
            volatility_24h=0.02,
            max_order_size=self._config.max_order_size,
        )

        LOGGER.info(
            "runtime_initialized symbol=%s target_order_size=%.8f "
            "max_order_size=%.8f rag_storage=%s exchange_mode=%s",
            self._config.symbol,
            self._config.target_order_size,
            self._config.max_order_size,
            rag_engine.storage_mode,
            self._exchange_mode(),
        )
        self._seed_macro_context(rag_engine)

        try:
            async with ingestor:
                while not self._stop_event.is_set():
                    await self._run_iteration(ingestor, rag_engine, math_engine)
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self._config.loop_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        continue
        except asyncio.CancelledError:
            LOGGER.info("runtime_cancelled cleaning_up=true")
            raise
        finally:
            await self._shutdown()

    async def stop(self, reason: str = "operator_request") -> None:
        """Request an orderly shutdown."""

        LOGGER.info("runtime_stop_requested reason=%s", reason)
        self._stop_event.set()

    async def _run_iteration(
        self,
        ingestor: BinanceIngestor,
        rag_engine: MarketSentimentRAGEngine,
        math_engine: MicrostructureEngine,
    ) -> None:
        self._iteration += 1
        self._heartbeat_at = datetime.now(UTC)
        snapshot = await ingestor.fetch_snapshot()
        sentiment = rag_engine.analyze_macro_sentiment(
            DEFAULT_MACRO_PHRASES[(self._iteration - 1) % len(DEFAULT_MACRO_PHRASES)]
        )
        volatility_proxy = self._volatility_from_sentiment(sentiment)
        volume_proxy = self._top_book_volume(snapshot)
        regime = math_engine.evaluate_market_regime(
            volatility_proxy=volatility_proxy,
            volume_proxy=volume_proxy,
        )
        execution = self._simulate_order(
            math_engine,
            snapshot,
            regime,
        )
        self._log_iteration(snapshot, sentiment, regime, execution)
        self._persist_iteration(snapshot, sentiment, regime, execution)

    def _simulate_order(
        self,
        math_engine: MicrostructureEngine,
        snapshot: BinanceMarketSnapshot,
        regime: MarketRegimeAssessment,
    ) -> ExecutionSimulation:
        try:
            slippage = math_engine.calculate_expected_slippage(
                self._config.target_order_size,
                snapshot.order_book,
            )
            return ExecutionSimulation(
                status="Executed",
                estimates=(slippage,),
                slice_count=1,
                reason="simulation accepted within dynamic boundary",
                message="Direct execution simulation completed.",
            )
        except InvalidOrderSizeError as exc:
            LOGGER.info(
                "execution_simulation twap_candidate=true symbol=%s "
                "target_order_size=%.8f allowed_max_order_size=%.8f "
                "max_participation=%.6f reason=%s",
                snapshot.symbol,
                self._config.target_order_size,
                regime.allowed_max_order_size,
                regime.max_participation_rate,
                str(exc),
            )
            return self._simulate_twap(
                math_engine,
                snapshot,
                regime,
            )

    def _simulate_twap(
        self,
        math_engine: MicrostructureEngine,
        snapshot: BinanceMarketSnapshot,
        regime: MarketRegimeAssessment,
    ) -> ExecutionSimulation:
        target_size = self._config.target_order_size
        available_volume = self._top_book_volume(snapshot)
        participation_capacity = (
            available_volume * regime.max_participation_rate
        )
        slice_capacity = min(
            regime.allowed_max_order_size,
            participation_capacity,
        )
        if slice_capacity < MIN_TWAP_SLICE_SIZE:
            return self._blocked_execution(
                "TWAP slice capacity is below the 0.01 BTC dust threshold.",
            )

        slice_count = max(2, math.ceil(target_size / slice_capacity))
        slice_size = target_size / slice_count
        if slice_size < MIN_TWAP_SLICE_SIZE:
            return self._blocked_execution(
                "Derived TWAP slice size is below the 0.01 BTC dust threshold.",
            )

        estimates: list[SlippageEstimate] = []
        for slice_number in range(slice_count):
            try:
                estimates.append(
                    math_engine.calculate_expected_slippage(
                        slice_size,
                        snapshot.order_book,
                    )
                )
            except InvalidOrderSizeError as exc:
                LOGGER.warning(
                    "execution_simulation blocked=true twap=true slice=%d "
                    "slice_count=%d reason=%s",
                    slice_number + 1,
                    slice_count,
                    str(exc),
                )
                return self._blocked_execution(
                    "TWAP slice failed deterministic execution limits.",
                )

        message = (
            "TWAP ROUTING ACTIVE: Order split into "
            f"{slice_count} slices to bypass market impact limits safely."
        )
        LOGGER.info(
            "execution_simulation twap=true status=Executed_Split "
            "slice_count=%d slice_size=%.8f message=%s",
            slice_count,
            slice_size,
            message,
        )
        return ExecutionSimulation(
            status="Executed_Split",
            estimates=tuple(estimates),
            slice_count=slice_count,
            reason="target exceeded dynamic boundary; routed through TWAP slices",
            message=message,
        )

    @staticmethod
    def _blocked_execution(reason: str) -> ExecutionSimulation:
        LOGGER.warning("execution_simulation blocked=true twap=false reason=%s", reason)
        return ExecutionSimulation(
            status="Blocked",
            estimates=(),
            slice_count=0,
            reason=reason,
            message="Execution blocked by deterministic risk controls.",
        )

    def _seed_macro_context(self, rag_engine: MarketSentimentRAGEngine) -> None:
        seed_documents = (
            (
                "macro-regulatory-bearish",
                "Regulatory uncertainty increases liquidation risk and weakens "
                "crypto liquidity. Volatility is elevated and market participants "
                "are reducing exposure.",
            ),
            (
                "macro-institutional-bullish",
                "Institutional adoption brings strong spot inflows, deeper "
                "liquidity, and optimistic demand. Market momentum is bullish "
                "with measured accumulation.",
            ),
            (
                "macro-network-neutral",
                "Network telemetry is stable. Transaction throughput and fees "
                "remain within normal ranges with no clear directional signal.",
            ),
            (
                "macro-volatility-risk",
                "Rapid price swings and thin order-book depth increase execution "
                "risk. Traders should expect wider slippage during the event.",
            ),
        )
        seeded = 0
        for document_id, text in seed_documents:
            rag_engine.ingest_text(
                text,
                source="runtime_seed",
                document_id=document_id,
            )
            seeded += 1
        LOGGER.info("rag_context_seeded documents=%d", seeded)

    def _log_iteration(
        self,
        snapshot: BinanceMarketSnapshot,
        sentiment: SentimentAnalysis,
        regime: MarketRegimeAssessment,
        execution: ExecutionSimulation,
    ) -> None:
        impact_bps, book_slippage_bps, total_slippage_bps = (
            self._aggregate_slippage_values(execution.estimates)
        )
        slippage_block = (
            f"status={execution.status}\n"
            f"  reason={execution.reason}\n"
            f"  target_order_size={self._config.target_order_size:.8f} BTC\n"
            f"  allowed_max_order_size={regime.allowed_max_order_size:.8f} BTC\n"
            f"  slice_count={execution.slice_count}\n"
            f"  message={execution.message}"
            if not execution.estimates
            else (
                f"status={execution.status}\n"
                f"  slice_count={execution.slice_count}\n"
                f"  book_slippage_bps={book_slippage_bps:.4f}\n"
                f"  expected_impact_bps={impact_bps:.4f}\n"
                f"  total_expected_slippage_bps={total_slippage_bps:.4f}\n"
                f"  message={execution.message}\n"
                f"  participation_rate={self._average_participation_rate(execution.estimates):.6f}"
            )
        )
        LOGGER.info(
            "market_decision iteration=%d captured_at=%s source=%s\n"
            "  regime=%s\n"
            "  sentiment_score=%.4f confidence=%.4f\n"
            "  volatility_proxy=%.6f volume_proxy=%.6f\n"
            "  risk_scalar=%.4f max_order_size=%.8f\n"
            "  safety_data_source=%s\n"
            "  safety_rag_context=%s\n"
            "  safety_execution=\n%s",
            self._iteration,
            snapshot.captured_at.isoformat(),
            snapshot.source,
            regime.regime,
            sentiment.score,
            sentiment.confidence,
            regime.volatility_proxy,
            regime.volume_proxy,
            regime.risk_scalar,
            regime.allowed_max_order_size,
            snapshot.source.value,
            sentiment.source_mode,
            slippage_block,
        )

    def _persist_iteration(
        self,
        snapshot: BinanceMarketSnapshot,
        sentiment: SentimentAnalysis,
        regime: MarketRegimeAssessment,
        execution: ExecutionSimulation,
    ) -> None:
        """Persist the dashboard read model after each completed iteration."""

        captured_at = snapshot.captured_at.isoformat()
        slippage = execution.estimates
        impact_bps, book_slippage_bps, total_slippage_bps = (
            self._aggregate_slippage_values(slippage)
        )
        state: dict[str, object] = {
            "regime": regime.regime.value,
            "sentiment_score": sentiment.score,
            "volatility_proxy": regime.volatility_proxy,
            "volume_proxy": regime.volume_proxy,
            "dynamic_max_order_size": regime.allowed_max_order_size,
            "risk_scalar": regime.risk_scalar,
            "rag_storage_mode": sentiment.source_mode,
            "data_source": snapshot.source.value,
            "target_order_size": self._config.target_order_size,
            "baseline_max_order_size": self._config.max_order_size,
            "baseline_participation_rate": DEFAULT_MAX_PARTICIPATION_RATE,
            "expected_impact_bps": impact_bps,
            "book_slippage_bps": book_slippage_bps,
            "total_expected_slippage_bps": total_slippage_bps,
            "execution_status": execution.status,
            "twap_slice_count": execution.slice_count,
            "twap_message": execution.message,
            "captured_at": captured_at,
        }
        record: dict[str, object] = {
            "iteration": self._iteration,
            "timestamp": captured_at,
            "regime": regime.regime.value,
            "sentiment_score": sentiment.score,
            "volatility_proxy": regime.volatility_proxy,
            "dynamic_max_order_size": regime.allowed_max_order_size,
            "target_order_size": self._config.target_order_size,
            "execution_status": execution.status,
            "book_slippage_bps": book_slippage_bps,
            "expected_impact_bps": impact_bps,
            "total_expected_slippage_bps": total_slippage_bps,
            "participation_rate": (
                self._average_participation_rate(slippage)
            ),
            "twap_slice_count": execution.slice_count,
            "slice_label": (
                f"Slice 1 of {execution.slice_count}"
                if execution.slice_count > 1
                else "Single slice"
            ),
            "twap_message": execution.message,
            "reason": execution.reason,
        }
        try:
            os.makedirs(RUNTIME_DIRECTORY, exist_ok=True)
            self._write_atomic_json(LATEST_STATE_PATH, state)
            with EXECUTION_RECORDS_PATH.open("a", encoding="utf-8") as records_file:
                records_file.write(json.dumps(record, ensure_ascii=True) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.exception(
                "runtime_state_persist_failed iteration=%d state_path=%s "
                "records_path=%s",
                self._iteration,
                LATEST_STATE_PATH,
                EXECUTION_RECORDS_PATH,
            )
            raise RuntimeError(
                f"Unable to persist runtime iteration {self._iteration}."
            ) from exc
        LOGGER.info(
            "runtime_state_persisted iteration=%d state_path=%s records_path=%s",
            self._iteration,
            LATEST_STATE_PATH,
            EXECUTION_RECORDS_PATH,
        )

    @staticmethod
    def _aggregate_slippage_values(
        estimates: tuple[SlippageEstimate, ...],
    ) -> tuple[float, float, float]:
        """Return volume-weighted bps values for all simulated slices."""

        if estimates:
            total_volume = sum(abs(item.order_size) for item in estimates)
            return (
                sum(
                    item.expected_impact_bps * abs(item.order_size)
                    for item in estimates
                )
                / total_volume,
                sum(
                    item.book_slippage_bps * abs(item.order_size)
                    for item in estimates
                )
                / total_volume,
                sum(
                    item.total_expected_slippage_bps * abs(item.order_size)
                    for item in estimates
                )
                / total_volume,
            )
        LOGGER.info(
            "slippage_observability fallback=true expected_impact_bps=%.2f "
            "book_slippage_bps=%.2f total_execution_cost_bps=%.2f",
            DEFAULT_BLOCKED_IMPACT_BPS,
            DEFAULT_BLOCKED_BOOK_SLIPPAGE_BPS,
            DEFAULT_BLOCKED_TOTAL_EXECUTION_COST_BPS,
        )
        return (
            DEFAULT_BLOCKED_IMPACT_BPS,
            DEFAULT_BLOCKED_BOOK_SLIPPAGE_BPS,
            DEFAULT_BLOCKED_TOTAL_EXECUTION_COST_BPS,
        )

    @staticmethod
    def _average_participation_rate(
        estimates: tuple[SlippageEstimate, ...],
    ) -> float:
        if not estimates:
            return 0.0
        return sum(item.participation_rate for item in estimates) / len(estimates)

    @staticmethod
    def _write_atomic_json(path: Path, payload: Mapping[str, object]) -> None:
        """Atomically replace a JSON file within its destination directory."""

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(payload, temporary_file, ensure_ascii=True, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _volatility_from_sentiment(sentiment: SentimentAnalysis) -> float:
        """Map bearish/uncertain sentiment to a conservative volatility proxy."""

        directional_risk = (1.0 - sentiment.score) / 2.0
        uncertainty_risk = 1.0 - sentiment.confidence
        return 0.01 + 0.04 * min(
            1.0,
            max(0.0, 0.7 * directional_risk + 0.3 * uncertainty_risk),
        )

    @staticmethod
    def _top_book_volume(snapshot: BinanceMarketSnapshot) -> float:
        return sum(level.quantity for level in snapshot.order_book.asks)

    def _exchange_mode(self) -> str:
        if not self._config.api_key or not self._config.secret:
            return "synthetic_fallback"
        return "sandbox" if self._config.sandbox else "live_read_only"

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    signal_name,
                    self._stop_event.set,
                )
            except (NotImplementedError, RuntimeError):
                LOGGER.debug(
                    "signal_handler_unavailable signal=%s",
                    signal_name,
                )

    async def _shutdown(self) -> None:
        LOGGER.info(
            "runtime_shutdown_complete iterations=%d last_heartbeat=%s",
            self._iteration,
            self._heartbeat_at.isoformat() if self._heartbeat_at else "never",
        )


def load_dotenv(path: Path = ENV_PATH) -> dict[str, str]:
    """Load simple KEY=VALUE entries without overriding process environment."""

    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
            if key:
                values[key] = os.environ.get(key, value)
    except OSError as exc:
        raise RuntimeError(f"Unable to read environment file: {path}") from exc
    return values


async def async_main() -> None:
    """Application entry point with explicit cancellation handling."""

    file_values = load_dotenv()
    environment = {**file_values, **os.environ}
    config = RuntimeConfig.from_environment(environment)
    runtime = QuantRAGRuntime(config)
    try:
        await runtime.run()
    except KeyboardInterrupt:
        await runtime.stop(reason="keyboard_interrupt")
    except asyncio.CancelledError:
        await runtime.stop(reason="task_cancelled")
        raise


def main() -> None:
    """Start the runtime and guarantee asyncio cleanup on Ctrl+C."""

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("runtime_interrupted cleanup_complete=true")


def _optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _positive_float(value: str, field: str) -> float:
    parsed = float(value)
    if not parsed > 0.0:
        raise ValueError(f"{field} must be positive.")
    return parsed


def _non_negative_int(value: str, field: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{field} must not be negative.")
    return parsed


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


if __name__ == "__main__":
    main()
