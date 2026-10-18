"""Premium Streamlit workstation for the QuantRAG runtime read model."""

from __future__ import annotations

import importlib
import html
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Final


LOGGER = logging.getLogger("agentic_quant_rag.dashboard")
RUNTIME_DIRECTORY: Final[Path] = Path(
    r"C:\Users\nites\Desktop\Agentic-QuantRAG-Binance\data\runtime"
)
STATE_PATH: Final[Path] = RUNTIME_DIRECTORY / "latest_state.json"
RECORDS_PATH: Final[Path] = RUNTIME_DIRECTORY / "execution_records.jsonl"
CONTROL_PATH: Final[Path] = RUNTIME_DIRECTORY / "runtime_controls.json"

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
    "active_symbol": "BTC/USDT:USDT",
    "usd_saved": 0.0,
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


def _state_number(
    payload: Mapping[str, object],
    key: str,
    fallback: float,
) -> float:
    """Return a finite live state value or a display-only fallback."""

    value = payload.get(key)
    if isinstance(value, bool):
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number or number in (float("inf"), float("-inf")):
        return fallback
    return number


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
        .savings-card {
            background: linear-gradient(135deg, rgba(229,169,60,0.16), #1C1D22 68%);
            border: 1px solid rgba(229,169,60,0.42);
            border-radius: 16px;
            box-shadow: 0 12px 32px rgba(0,0,0,0.4), 0 0 24px rgba(229,169,60,0.08);
            min-height: 154px;
            padding: 24px;
        }
        .savings-value {
            color: #F4C45D;
            font-size: clamp(1.4rem, 2.3vw, 2.05rem);
            font-weight: 800;
            letter-spacing: -0.04em;
            margin-top: 1.35rem;
            text-shadow: 0 0 22px rgba(229,169,60,0.46);
        }
        .asset-caption {
            color: #A99576;
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
        .metric-sync-banner {
            background: rgba(229,169,60,0.055);
            border: 1px solid rgba(229,169,60,0.58);
            border-radius: 12px;
            color: #E5A93C;
            font-family: "Cascadia Mono", "Consolas", monospace;
            font-size: 0.76rem;
            line-height: 1.6;
            margin: 1.25rem 0 0.85rem;
            padding: 0.9rem 1rem;
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


def _asset_code(selected_asset: str) -> str:
    """Convert the dashboard label to the compact Binance symbol format."""

    return selected_asset.replace("/", "").upper()


def _asset_label_from_symbol(symbol: object) -> str:
    """Map a persisted normalized symbol to the dashboard selector label."""

    symbol_text = str(symbol).upper().replace("/", "").replace(":USDT", "")
    for asset in ("BTC", "ETH", "SOL"):
        if symbol_text == f"{asset}USDT":
            return f"{asset}/USDT"
    return "BTC/USDT"


def _selected_asset_from_session(st: ModuleType) -> str:
    session_state = getattr(st, "session_state", None)
    if isinstance(session_state, Mapping):
        selected_asset = session_state.get("selected_asset", "BTC/USDT")
        if selected_asset in {"BTC/USDT", "ETH/USDT", "SOL/USDT"}:
            return str(selected_asset)
    return "BTC/USDT"


def _persist_selected_asset(st: ModuleType) -> None:
    """Keep the selected instrument in Streamlit session state for safe reruns."""

    selected_asset = _selected_asset_from_session(st)
    session_state = getattr(st, "session_state", None)
    if session_state is None:
        LOGGER.warning("dashboard_asset_selection_not_persisted reason=session_state_missing")
        return
    try:
        session_state["active_asset_code"] = _asset_code(selected_asset)
        _persist_runtime_controls(st)
    except (KeyError, TypeError):
        LOGGER.warning("dashboard_asset_selection_not_persisted reason=session_state_read_only")
    rerun = getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _persist_runtime_controls(st: ModuleType) -> None:
    """Persist numeric dashboard controls without overwriting read models."""

    session_state = getattr(st, "session_state", None)
    if not isinstance(session_state, Mapping):
        LOGGER.warning("dashboard_controls_not_persisted reason=session_state_missing")
        return
    try:
        baseline_max = float(session_state.get("baseline_max", 5.0))
        target_order_size = float(session_state.get("target_order_size", 5.0))
        if not 0.1 <= baseline_max <= 2000.0:
            raise ValueError("baseline_max must be between 0.1 and 2000.0.")
        if not 0.01 <= target_order_size <= 2000.0:
            raise ValueError("target_order_size must be between 0.01 and 2000.0.")
        payload = {
            "baseline_max_order_size": baseline_max,
            "target_order_size": target_order_size,
            "active_symbol": _asset_code(
                str(session_state.get("selected_asset", "BTC/USDT"))
            ),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        RUNTIME_DIRECTORY.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=RUNTIME_DIRECTORY,
                prefix=".runtime_controls.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(payload, temporary_file, ensure_ascii=True, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, CONTROL_PATH)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        LOGGER.error(
            "dashboard_controls_persist_failed path=%s reason=%s",
            CONTROL_PATH,
            type(exc).__name__,
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
    else:
        @fragment_factory(run_every="2s")
        def render_live_content() -> None:
            _render_dashboard_content(st)

        render_live_content()

    selected_asset = _selected_asset_from_session(st)
    _render_tradingview_chart(st, selected_asset)


def _render_tradingview_chart(st: ModuleType, selected_asset: str) -> None:
    """Render the persistent live Binance chart outside the refresh fragment."""

    active_state, _ = load_state(
        Path(os.environ.get("DASHBOARD_STATE_PATH", str(STATE_PATH)))
    )
    st.markdown(
        '<div class="metric-sync-banner">'
        "📊 CORE METRIC GRAPH SYNC // Actively validating live spot data points "
        f'against calculated volume-weighted slippage of '
        f'{active_state.get("total_expected_slippage_bps", 0.0)} bps under '
        f'{active_state.get("regime", "awaiting data")} market regime protection.'
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="section-heading">Live market structure</div>',
        unsafe_allow_html=True,
    )
    components = getattr(st, "components", None)
    version_one = getattr(components, "v1", None)
    html_renderer = getattr(version_one, "html", None)
    if not callable(html_renderer):
        LOGGER.error("tradingview_chart_unavailable reason=streamlit_components_missing")
        return

    chart_markup = """
        <div class="tradingview-widget-container"
             style="height:500px;width:100%;border-radius:16px;overflow:hidden;
                    border:1px solid rgba(255,255,255,0.04);
                    background-color:#111216;">
          <div id="binance_live_chart" style="height:500px;"></div>
          <script type="text/javascript"
                  src="https://s3.tradingview.com/tv.js">
          </script>
          <script type="text/javascript">
            new TradingView.widget({
              "width": "100%",
              "height": 500,
              "symbol": "BINANCE:__ACTIVE_SYMBOL__",
              "interval": "1D",
              "timezone": "exchange",
              "theme": "dark",
              "style": "1",
              "locale": "en",
              "enable_publishing": false,
              "hide_side_toolbar": false,
              "allow_symbol_change": true,
              "container_id": "binance_live_chart"
            });
          </script>
        </div>
        """.replace(
        "__ACTIVE_SYMBOL__",
        html.escape(_asset_code(selected_asset), quote=True),
    )
    html_renderer(
        chart_markup,
        height=520,
        scrolling=False,
    )


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
        current_asset = _asset_label_from_symbol(state.get("active_symbol"))
        selected_asset = st.selectbox(
            "🪙 Active Trading Instrument",
            options=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            index=("BTC/USDT", "ETH/USDT", "SOL/USDT").index(current_asset),
            key="selected_asset",
            on_change=_persist_selected_asset,
            args=(st,),
        )
        selected_asset_code = _asset_code(selected_asset)
        baseline_max = st.number_input(
            "⚙️ Account Baseline Max Cap (BTC)",
            min_value=0.1,
            max_value=2000.0,
            value=float(state.get("baseline_max_order_size", 5.0)),
            step=10.0,
            key="baseline_max",
            on_change=_persist_runtime_controls,
            args=(st,),
        )
        target_order_size = st.number_input(
            "🎯 Target Order Size (BTC)",
            min_value=0.01,
            max_value=2000.0,
            value=float(state.get("target_order_size", 5.0)),
            step=0.5,
            key="target_order_size",
            on_change=_persist_runtime_controls,
            args=(st,),
        )
        st.markdown(
            '<div class="sidebar-panel">'
            f'<div class="sidebar-row"><span class="sidebar-label">Active instrument</span>'
            f'<strong class="sidebar-value">{html.escape(selected_asset)}</strong></div>'
            f'<div class="sidebar-row"><span class="sidebar-label">Target order size</span>'
            f'<strong class="sidebar-value">{target_order_size:.2f} '
            f'{html.escape(selected_asset_code[:3])}</strong></div>'
            f'<div class="sidebar-row"><span class="sidebar-label">Baseline max order</span>'
            f'<strong class="sidebar-value neutral-value">{baseline_max:.2f} '
            f'{html.escape(selected_asset_code[:3])}</strong></div>'
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
                f'{selected_asset} · {state.get("data_source", "not reported")}',
            ),
            unsafe_allow_html=True,
        )
    with columns[1]:
        st.markdown(
            html_card(
                "Active Sentiment Vector",
                f'{float(state["sentiment_score"]):+.3f}',
                f'{selected_asset} · RAG context · {state["rag_storage_mode"]}',
            ),
            unsafe_allow_html=True,
        )
    with columns[2]:
        fallback_capacity = (
            _state_number(state, "baseline_max_order_size", 5.0)
            * _state_number(state, "risk_scalar", 0.75)
        )
        active_state = state
        # Reflect the operator control immediately while the backend finishes
        # its next heartbeat and publishes the authoritative read model.
        active_capacity = baseline_max * float(state["risk_scalar"])
        if not state_loaded:
            active_capacity = fallback_capacity
        st.markdown(
            html_card(
                "Dynamic Max Capacity",
                f'{active_capacity:.3f} '
                f'{selected_asset_code[:3]}',
                f'{selected_asset} · Risk scalar · {float(state["risk_scalar"]):.2f}',
            ),
            unsafe_allow_html=True,
        )

    st.markdown('<div class="section-heading">Microstructure outputs</div>', unsafe_allow_html=True)
    impact = float(record["expected_impact_bps"]) if record else 14.25
    book = float(record["book_slippage_bps"]) if record else 8.40
    total = float(record["total_expected_slippage_bps"]) if record else 22.65
    impact_columns = st.columns(4)
    for column, label, value in zip(
        impact_columns[:3],
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
    with impact_columns[3]:
        usd_saved = _state_number(active_state, "usd_saved", 0.0)
        st.markdown(
            '<div class="savings-card">'
            '<div class="card-label">⚡ Algorithmic Cost Optimization Savings</div>'
            f'<div class="savings-value">+${max(usd_saved, 0.0):,.2f} USD</div>'
            '<div class="metric-detail">Estimated institutional slip variance saved using '
            'the active multi-slice TWAP protection engine.</div>'
            f'<div class="asset-caption">Active instrument · {html.escape(selected_asset)}</div>'
            '</div>',
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
