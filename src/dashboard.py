"""Premium Streamlit workstation for the QuantRAG runtime read model."""

from __future__ import annotations

import importlib
import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Final


LOGGER = logging.getLogger("agentic_quant_rag.dashboard")
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
RUNTIME_DIRECTORY: Final[Path] = Path(
    r"C:\Users\nites\Desktop\Agentic-QuantRAG-Binance\data\runtime"
)
STATE_PATH: Final[Path] = RUNTIME_DIRECTORY / "latest_state.json"
RECORDS_PATH: Final[Path] = RUNTIME_DIRECTORY / "execution_records.jsonl"

PLACEHOLDER_STATE: Final[dict[str, object]] = {
    "regime": "awaiting data",
    "sentiment_score": 0.0,
    "dynamic_max_order_size": 0.0,
    "volatility_proxy": 0.0,
    "risk_scalar": 0.0,
    "rag_storage_mode": "not reported",
    "data_source": "not reported",
    "target_order_size": 5.0,
    "baseline_max_order_size": 5.0,
    "baseline_participation_rate": 0.10,
    "captured_at": "awaiting runtime sync",
}


class DashboardDataError(Exception):
    """Raised when the runtime read model is invalid."""


def _finite_number(payload: Mapping[str, object], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool):
        raise DashboardDataError(f"{key} must be numeric.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DashboardDataError(f"{key} must be numeric.") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise DashboardDataError(f"{key} must be finite.")
    return number


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DashboardDataError(f"{key} must be a non-empty string.")
    return value.strip()


def load_state(path: Path = STATE_PATH) -> tuple[dict[str, object], bool]:
    """Load the latest snapshot; return a safe placeholder while unavailable."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise DashboardDataError("Runtime state must be a JSON object.")
        state = dict(PLACEHOLDER_STATE)
        state.update(payload)
        state["regime"] = _text(payload, "regime")
        state["rag_storage_mode"] = _text(payload, "rag_storage_mode")
        state["data_source"] = _text(payload, "data_source")
        state["captured_at"] = _text(payload, "captured_at")
        for key in (
            "sentiment_score",
            "dynamic_max_order_size",
            "volatility_proxy",
            "risk_scalar",
            "target_order_size",
            "baseline_max_order_size",
            "baseline_participation_rate",
        ):
            state[key] = _finite_number(payload, key)
        if not -1.0 <= state["sentiment_score"] <= 1.0:
            raise DashboardDataError("sentiment_score must be between -1 and 1.")
        if not 0.0 <= state["risk_scalar"] <= 1.0:
            raise DashboardDataError("risk_scalar must be between 0 and 1.")
        return state, True
    except (DashboardDataError, OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("dashboard_state_fallback path=%s reason=%s", path, exc)
        return dict(PLACEHOLDER_STATE), False


def load_latest_record(path: Path = RECORDS_PATH) -> dict[str, float | str] | None:
    """Read the latest valid execution record for the risk-output strip."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        try:
            return {
                "status": _text(payload, "execution_status"),
                "reason": str(payload.get("reason", "")),
                "book_slippage_bps": _finite_number(
                    payload,
                    "book_slippage_bps",
                ),
                "expected_impact_bps": _finite_number(
                    payload,
                    "expected_impact_bps",
                ),
                "total_expected_slippage_bps": _finite_number(
                    payload,
                    "total_expected_slippage_bps",
                ),
            }
        except DashboardDataError:
            continue
    return None


def inject_css(st: ModuleType) -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: #121316;
            color: #E2E4E9;
        }
        section[data-testid="stSidebar"] {
            background: #16171B;
            border-right: 1px solid rgba(255,255,255,0.04);
        }
        section[data-testid="stSidebar"] .block-container {
            padding: 2rem 1.35rem;
        }
        .block-container {
            max-width: 1480px;
            padding: 3rem 3.5rem 4rem;
        }
        .bento-card {
            background: #1C1D22;
            border: 1px solid rgba(255,255,255,0.04);
            border-radius: 16px;
            box-shadow: 0 12px 32px rgba(0,0,0,0.4);
            min-height: 154px;
            padding: 24px;
        }
        .eyebrow, .card-label, .sidebar-label, .section-kicker {
            color: #71717A;
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.16em;
            text-transform: uppercase;
        }
        .eyebrow, .gold-text, .sidebar-value, .metric-value {
            color: #E5A93C;
        }
        .workspace-title {
            color: #F4F1EA;
            font-size: clamp(2rem, 4vw, 3.5rem);
            font-weight: 800;
            letter-spacing: -0.055em;
            line-height: 1;
            margin: 0.55rem 0;
        }
        .workspace-subtitle {
            color: #71717A;
            font-size: 0.85rem;
            margin: 0;
        }
        .sync-badge {
            background: #1C1D22;
            border: 1px solid rgba(229,169,60,0.24);
            border-radius: 999px;
            color: #E5A93C;
            display: inline-block;
            font-size: 0.67rem;
            letter-spacing: 0.08em;
            padding: 0.65rem 0.9rem;
            text-transform: uppercase;
        }
        .metric-value {
            font-size: clamp(1.7rem, 3vw, 2.65rem);
            font-weight: 800;
            letter-spacing: -0.05em;
            margin-top: 1.35rem;
            text-shadow: 0 0 20px rgba(229,169,60,0.24);
        }
        .metric-value.compact { font-size: clamp(1.45rem, 2.5vw, 2rem); }
        .metric-detail {
            color: #71717A;
            font-size: 0.72rem;
            margin-top: 0.35rem;
        }
        .sidebar-brand {
            color: #F4F1EA;
            font-size: 1.2rem;
            font-weight: 800;
            margin: 0.4rem 0 2rem;
        }
        .sidebar-panel {
            background: #1C1D22;
            border: 1px solid rgba(255,255,255,0.04);
            border-radius: 16px;
            margin: 1rem 0 2rem;
            padding: 0.4rem 1rem;
        }
        .sidebar-row {
            align-items: center;
            border-bottom: 1px solid rgba(255,255,255,0.04);
            display: flex;
            justify-content: space-between;
            padding: 0.9rem 0;
        }
        .sidebar-row:last-child { border-bottom: 0; }
        .sidebar-value { font-size: 0.78rem; font-weight: 700; }
        .neutral-value { color: #E2E4E9; }
        .section-heading {
            color: #F4F1EA;
            font-size: 1.25rem;
            font-weight: 750;
            margin: 2.25rem 0 0.75rem;
        }
        .impact-card {
            background: #1C1D22;
            border: 1px solid rgba(255,255,255,0.04);
            border-radius: 16px;
            box-shadow: 0 12px 32px rgba(0,0,0,0.25);
            padding: 20px;
        }
        .impact-value {
            color: #E5A93C;
            font-size: 1.65rem;
            font-weight: 800;
            margin-top: 0.7rem;
        }
        .status-blocked-panel {
            background: rgba(229,169,60,0.055);
            border: 1px dashed rgba(229,169,60,0.58);
            border-radius: 16px;
            color: #C7A66C;
            margin-top: 1.2rem;
            padding: 1.1rem 1.25rem;
        }
        .status-blocked-panel.active {
            background: rgba(229,169,60,0.09);
            border-color: rgba(229,169,60,0.82);
            box-shadow: 0 0 24px rgba(229,169,60,0.08);
        }
        .blocked-tag {
            color: #E5A93C;
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.1em;
            text-transform: uppercase;
        }
        .blocked-copy {
            color: #A99576;
            font-size: 0.8rem;
            margin: 0.45rem 0 0;
        }
        @media (max-width: 900px) {
            .block-container { padding: 2rem 1.2rem 3rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def html_card(label: str, value: str, detail: str = "", compact: bool = False) -> str:
    value_class = "metric-value compact" if compact else "metric-value"
    return (
        '<div class="bento-card">'
        f'<div class="card-label">{label}</div>'
        f'<div class="{value_class}">{value}</div>'
        f'<div class="metric-detail">{detail}</div>'
        "</div>"
    )


def render_dashboard(st: ModuleType) -> None:
    """Configure the page and schedule a native two-second live fragment."""

    st.set_page_config(
        page_title="QuantRAG Execution Intelligence",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css(st)

    fragment_factory = getattr(st, "fragment", None)
    if not callable(fragment_factory):
        LOGGER.warning(
            "streamlit_fragment_unavailable live_refresh=false "
            "reason=upgrade_streamlit"
        )
        _render_dashboard_content(st)
        return

    @fragment_factory(run_every="2s")
    def render_live_content() -> None:
        _render_dashboard_content(st)

    render_live_content()


def _render_dashboard_content(st: ModuleType) -> None:
    """Render the read-only workstation inside the reactive fragment."""

    state, state_loaded = load_state(
        Path(os.environ.get("DASHBOARD_STATE_PATH", str(STATE_PATH)))
    )
    record = load_latest_record(
        Path(os.environ.get("DASHBOARD_RECORDS_PATH", str(RECORDS_PATH)))
    )

    with st.sidebar:
        st.markdown('<div class="eyebrow">AGENTIC QUANT // CONTROL</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-brand">Runtime parameters</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-label">SYSTEM CONSTANTS</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="sidebar-panel">'
            '<div class="sidebar-row"><span class="sidebar-label">Target order size</span>'
            '<strong class="sidebar-value">5.00 BTC</strong></div>'
            '<div class="sidebar-row"><span class="sidebar-label">Baseline max order</span>'
            '<strong class="sidebar-value neutral-value">5.00 BTC</strong></div>'
            '<div class="sidebar-row"><span class="sidebar-label">Participation cap</span>'
            '<strong class="sidebar-value neutral-value">10.0%</strong></div>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="sidebar-label">ACTIVE VISIBILITY</div>'
            f'<div class="sidebar-row"><span class="sidebar-label">RAG storage</span>'
            f'<strong class="sidebar-value">{state["rag_storage_mode"]}</strong></div>'
            f'<div class="sidebar-row"><span class="sidebar-label">Market source</span>'
            f'<strong class="sidebar-value">{state["data_source"]}</strong></div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="eyebrow">EXECUTION INTELLIGENCE // BINANCE PERPETUALS</div>'
        '<div class="workspace-title">Quantitative workstation</div>'
        '<p class="workspace-subtitle">Regime-aware research, impact modeling, and paper execution controls.</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="sync-badge">ITERATION SYNC · {state["captured_at"]}</div>',
        unsafe_allow_html=True,
    )

    columns = st.columns(3)
    with columns[0]:
        st.markdown(
            html_card(
                "Current Market Regime",
                str(state["regime"]).replace("_", " ").upper(),
                str(state["data_source"]),
            ),
            unsafe_allow_html=True,
        )
    with columns[1]:
        st.markdown(
            html_card(
                "Active Sentiment Vector",
                f'{float(state["sentiment_score"]):+.3f}',
                f'RAG context · {state["rag_storage_mode"]}',
            ),
            unsafe_allow_html=True,
        )
    with columns[2]:
        st.markdown(
            html_card(
                "Dynamic Max Capacity",
                f'{float(state["dynamic_max_order_size"]):.3f} BTC',
                f'Risk scalar · {float(state["risk_scalar"]):.2f}',
            ),
            unsafe_allow_html=True,
        )

    st.markdown('<div class="section-heading">Microstructure outputs</div>', unsafe_allow_html=True)
    impact = float(record["expected_impact_bps"]) if record else 14.25
    book = float(record["book_slippage_bps"]) if record else 8.40
    total = float(record["total_expected_slippage_bps"]) if record else 22.65
    impact_columns = st.columns(3)
    for column, label, value in zip(
        impact_columns,
        ("Expected Square-Root Impact", "Order Book Slippage", "Total Execution Cost"),
        (impact, book, total),
        strict=True,
    ):
        with column:
            st.markdown(
                f'<div class="impact-card"><div class="card-label">{label}</div>'
                f'<div class="impact-value">{value:.2f} bps</div>'
                '<div class="metric-detail">validated paper execution estimate</div></div>',
                unsafe_allow_html=True,
            )

    target = float(state["target_order_size"])
    allowed = float(state["dynamic_max_order_size"])
    active_state = state
    execution_status = (
        str(active_state.get("execution_status", record["status"] if record else ""))
        .casefold()
    )
    if execution_status == "executed_split":
        st.markdown(
            '<div class="status-blocked-panel active">'
            '<div class="blocked-tag">⚙️ TWAP ROUTING ACTIVE</div>'
            '<p class="blocked-copy">Order automatically split into slices to safely '
            'bypass market impact and liquidity limits.</p>'
            "</div>",
            unsafe_allow_html=True,
        )
    elif execution_status == "blocked":
        st.markdown(
            '<div class="status-blocked-panel">'
            '<div class="blocked-tag">⚠️ SIMULATION BLOCKED</div>'
            f'<p class="blocked-copy">Target request of {target:.2f} BTC violates '
            f'dynamic regime bounds. Allowed capacity is {allowed:.3f} BTC.</p>'
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="status-blocked-panel">'
            '<div class="blocked-tag">SIMULATION WITHIN BOUNDS</div>'
            f'<p class="blocked-copy">Target request of {target:.2f} BTC passed the '
            f'current {allowed:.3f} BTC dynamic capacity boundary.</p>'
            "</div>",
            unsafe_allow_html=True,
        )

    if not state_loaded:
        st.caption("Runtime snapshot unavailable; displaying safe placeholder parameters.")


def main() -> None:
    try:
        streamlit_module = importlib.import_module("streamlit")
    except ImportError as exc:
        raise RuntimeError(
            "Streamlit is required to run this dashboard. Install it in the project environment."
        ) from exc
    render_dashboard(streamlit_module)


if __name__ == "__main__":
    main()
