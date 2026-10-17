"""Continuous synthetic market-news injector for the local RAG store.

Run from the repository root with:

    python -m src.news_injector

The injector only writes market-log documents. It never submits orders or
changes trading configuration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    from .rag_engine import MarketSentimentRAGEngine
except ImportError:  # Direct execution: python src/news_injector.py
    from rag_engine import MarketSentimentRAGEngine


LOGGER = logging.getLogger("agentic_quant_rag.news_injector")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PERSIST_DIRECTORY = PROJECT_ROOT / "data" / "chroma_db"
DEFAULT_MIN_INTERVAL_SECONDS = 20.0
DEFAULT_MAX_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class NewsInjectorConfig:
    """Validated settings for the synthetic news stream."""

    persist_directory: Path
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS
    max_interval_seconds: float = DEFAULT_MAX_INTERVAL_SECONDS

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
    ) -> NewsInjectorConfig:
        min_interval = _positive_float(
            environment.get(
                "NEWS_MIN_INTERVAL_SECONDS",
                str(DEFAULT_MIN_INTERVAL_SECONDS),
            ),
            "NEWS_MIN_INTERVAL_SECONDS",
        )
        max_interval = _positive_float(
            environment.get(
                "NEWS_MAX_INTERVAL_SECONDS",
                str(DEFAULT_MAX_INTERVAL_SECONDS),
            ),
            "NEWS_MAX_INTERVAL_SECONDS",
        )
        if min_interval > max_interval:
            raise ValueError(
                "NEWS_MIN_INTERVAL_SECONDS must not exceed "
                "NEWS_MAX_INTERVAL_SECONDS."
            )
        return cls(
            persist_directory=Path(
                environment.get(
                    "RAG_PERSIST_DIRECTORY",
                    str(DEFAULT_PERSIST_DIRECTORY),
                )
            ),
            min_interval_seconds=min_interval,
            max_interval_seconds=max_interval,
        )


NEWS_POOL: tuple[tuple[str, str], ...] = (
    (
        "highly-bearish-regulatory",
        "BREAKING: Central banks announce strict liquidity regulations and "
        "massive margin cuts for crypto perps.",
    ),
    (
        "highly-bullish-institutional",
        "INSTITUTIONAL ADOPTION: Major spot ETF inflows trigger huge volume "
        "spikes and deep order-book liquidity.",
    ),
    (
        "crash-alert-systemic",
        "SYSTEMIC SHOCK: Unprecedented flash crash across derivative markets "
        "spikes global volatility indices.",
    ),
)


class NewsInjector:
    """Inject one distinct synthetic event on each scheduled iteration."""

    def __init__(
        self,
        config: NewsInjectorConfig,
        *,
        engine: MarketSentimentRAGEngine | None = None,
    ) -> None:
        self._config = config
        self._engine = engine or MarketSentimentRAGEngine(
            persist_directory=config.persist_directory
        )
        self._stop_event = asyncio.Event()
        self._next_index = 0

    async def run(self) -> None:
        """Run until SIGINT, SIGTERM, or task cancellation."""

        self._install_signal_handlers()
        LOGGER.info(
            "news_injector_started storage=%s min_interval=%.1fs "
            "max_interval=%.1fs",
            self._engine.storage_mode,
            self._config.min_interval_seconds,
            self._config.max_interval_seconds,
        )
        try:
            while not self._stop_event.is_set():
                self.inject_next()
                delay_seconds = self._next_delay_seconds()
                LOGGER.info(
                    "news_injector_next_injection_in=%.1fs",
                    delay_seconds,
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=delay_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            LOGGER.info("news_injector_cancelled")
            raise
        finally:
            LOGGER.info("news_injector_stopped injections=%d", self._next_index)

    async def stop(self, reason: str = "operator_request") -> None:
        """Request an orderly stop."""

        LOGGER.info("news_injector_stop_requested reason=%s", reason)
        self._stop_event.set()

    def inject_next(self) -> None:
        """Persist the next distinct news event and log its exact payload."""

        scenario, message = NEWS_POOL[self._next_index % len(NEWS_POOL)]
        timestamp = datetime.now(UTC)
        event_id = f"news-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:12]}"
        document = self._engine.ingest_text(
            message,
            source="synthetic_live_news",
            timestamp=timestamp,
            document_id=event_id,
            metadata={
                "scenario": scenario,
                "stream": "synthetic_live_news",
            },
        )
        self._next_index += 1
        LOGGER.info(
            "news_injected timestamp=%s event_id=%s scenario=%s message=%s",
            document.timestamp.isoformat(),
            document.document_id,
            scenario,
            document.text,
        )

    def _next_delay_seconds(self) -> float:
        """Return a deterministic midpoint interval for reproducible replay."""

        return (
            self._config.min_interval_seconds
            + self._config.max_interval_seconds
        ) / 2.0

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_name, self._stop_event.set)
            except (NotImplementedError, RuntimeError):
                LOGGER.debug(
                    "news_injector_signal_handler_unavailable signal=%s",
                    signal_name,
                )


async def async_main() -> None:
    """Create the injector and run it until a shutdown request."""

    config = NewsInjectorConfig.from_environment(os.environ)
    injector = NewsInjector(config)
    try:
        await injector.run()
    except KeyboardInterrupt:
        await injector.stop(reason="keyboard_interrupt")
    except asyncio.CancelledError:
        await injector.stop(reason="task_cancelled")
        raise


def main() -> None:
    """Configure logging and start the async injector."""

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("news_injector_interrupted cleanup_complete=true")


def _positive_float(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if parsed <= 0.0:
        raise ValueError(f"{field} must be positive.")
    return parsed


if __name__ == "__main__":
    main()
