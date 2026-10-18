# Agentic QuantRAG Binance

Agentic QuantRAG Binance is a research-first quantitative trading workstation that combines live Binance market data, retrieval-augmented sentiment analysis, market-regime adaptation, and deterministic execution-risk modeling.

The project is designed to demonstrate how an AI-assisted research layer can work with strict quantitative and risk controls. The RAG and sentiment components can provide evidence and context, but they cannot bypass the mathematical execution boundary or submit an order directly.

> **Important:** This project is currently a paper-trading and execution-simulation system. It does not promise returns and should not be used with production trading credentials without completing the missing exchange, risk, compliance, and operational controls.

## What the project does

For every runtime heartbeat, the system:

1. Fetches an order-book snapshot from Binance USDⓈ-M Futures.
2. Falls back to bounded synthetic market data when the public endpoint is unavailable.
3. Retrieves market context through the RAG engine.
4. Converts sentiment and market conditions into a volatility and liquidity-aware regime.
5. Calculates square-root market impact and order-book slippage using NumPy-based math.
6. Applies dynamic account and participation limits.
7. Simulates direct execution, TWAP splitting, or a blocked decision.
8. Estimates the cost avoided by splitting a large order instead of executing it in one shot.
9. Publishes the latest state and an append-only execution history for the dashboards.

News and sentiment context run automatically as part of the backend heartbeat.
The operator dashboard does not expose a manual news-injection control; it is
focused on monitoring and bounded execution parameters.

## Core features

- **Binance market-data ingestion**
  - Supports `BTCUSDT`, `ETHUSDT`, and `SOLUSDT`.
  - Uses typed immutable market-data contracts.
  - Includes timeout, retry, validation, and synthetic fallback behavior.

- **Retrieval-augmented sentiment**
  - Prefers `all-MiniLM-L6-v2` embeddings through LangChain/Hugging Face.
  - Stores vectors in ChromaDB when available.
  - Falls back to a local JSON store and deterministic embeddings when optional dependencies are unavailable.

- **Market-regime adaptation**
  - Combines volatility, liquidity, and sentiment context.
  - Calculates a risk scalar and dynamic maximum order capacity.

- **Execution-risk modeling**
  - Uses the Square Root Market Impact Law.
  - Models order-book depth, participation rate, expected impact, and aggregate execution cost.
  - Keeps the core math isolated from the dashboard and RAG layers.

- **TWAP execution simulation**
  - Splits orders that exceed the current dynamic capacity.
  - Persists slice counts, execution status, and weighted slippage.
  - Uses a minimum slice-size safety threshold.

- **Live runtime controls**
  - The dashboard writes operator controls to `data/runtime/runtime_controls.json`.
  - The backend reloads the selected instrument, baseline account cap, and target order size during each heartbeat.
  - Supports `BTC/USDT`, `ETH/USDT`, and `SOL/USDT` instrument switching.
  - Controls are validated before they reach the math engine.

- **Cost-savings estimate**
  - Compares the simulated TWAP cost with a higher single-shot baseline.
  - Recalculates estimated USD cost avoidance using the active asset price, selected instrument, and live target size.
  - This is an execution-cost estimate, not realized trading profit.

- **Premium operator interfaces**
  - Streamlit workstation with a warm-gold and matte-charcoal visual system.
  - React + Tailwind frontend foundation with polling and runtime read-model support.
  - TradingView chart integration in the Streamlit diagnostic dashboard.

## Architecture

```text
Binance market data
        |
        v
src/data_loader.py
        |
        v
Market snapshot validation
        |
        +----------------------+
        |                      |
        v                      v
src/rag_engine.py       src/math_engine.py
        |                      |
        v                      v
Sentiment context       Regime and impact model
        |                      |
        +----------+-----------+
                   v
             src/main.py
       Runtime and TWAP simulation
                   |
                   v
          data/runtime/*.json*
             |            |
             v            v
    src/dashboard.py   frontend/
       Streamlit       React + Vite
```

## Repository structure

```text
.
├── PRD.md
├── Architecture.md
├── Rules.md
├── src/
│   ├── data_loader.py       # Binance ingestion and typed snapshots
│   ├── math_engine.py       # NumPy impact, slippage, and regime math
│   ├── rag_engine.py        # Retrieval, embeddings, and sentiment
│   ├── main.py              # Async runtime heartbeat and persistence
│   ├── dashboard.py         # Streamlit operator workstation
│   └── news_injector.py     # Synthetic news/context producer
├── data/
│   └── runtime/             # Ignored local state and operator controls
└── frontend/
    ├── src/
    │   └── App.jsx
    ├── tailwind.config.js
    ├── vite.config.js
    └── package.json
```

## Runtime read models

The backend owns the output files:

- `data/runtime/latest_state.json` is atomically replaced after a completed heartbeat.
- `data/runtime/execution_records.jsonl` stores one append-only execution record per iteration.

The dashboard writes only to the separate control file:

```json
{
  "baseline_max_order_size": 100.0,
  "target_order_size": 20.0,
  "active_symbol": "BTCUSDT",
  "updated_at": "2026-10-19T00:00:00+00:00"
}
```

This separation prevents dashboard controls from accidentally overwriting live market and execution metrics.
The backend normalizes the selected symbol, updates the market-data ingestor,
and publishes the actual active symbol in the next state snapshot.

## Local setup

### Python environment

Use the project virtual environment or another isolated Python environment. Install the dependencies defined by the project's dependency management setup before running the backend.

The runtime uses Python 3.11-compatible syntax and expects the project to be executed from the repository root.

### Environment variables

Common runtime settings include:

```text
ASSET_SYMBOL=BTCUSDT
TARGET_ORDER_SIZE=5.0
MAX_ORDER_SIZE=5.0
LOOP_INTERVAL_SECONDS=30.0
REQUEST_TIMEOUT_SECONDS=10.0
MAX_RETRIES=3
ALLOW_SYNTHETIC_FALLBACK=true
USE_CCXT_PRO=true
BINANCE_SANDBOX=false
```

API credentials are optional for the public market-data fallback, but credentials must never be committed to the repository.

### Run the Python runtime

From the repository root:

```powershell
python -m src.main
```

The runtime will continuously update the files under `data/runtime/`.

### Run the Streamlit workstation

```powershell
streamlit run src/dashboard.py
```

The Streamlit dashboard is a local diagnostic/operator interface. It refreshes live runtime content while keeping the TradingView chart outside the fast refresh fragment.

### Run the React frontend

```powershell
cd frontend
npm install
npm run dev
```

For a production build:

```powershell
npm run build
```

## Current status

### Working

- Typed Binance ingestion boundary.
- Public Futures order-book retrieval with safe fallback.
- RAG provider and storage fallbacks.
- NumPy microstructure calculations.
- Dynamic runtime baseline and target-size controls.
- Regime-aware capacity calculation.
- TWAP execution simulation.
- Atomic runtime state persistence.
- Append-only execution records.
- Dynamic BTC, ETH, and SOL instrument switching.
- Automatic backend sentiment context without manual news controls in the dashboard.
- Streamlit and React dashboard foundations.

### Still required before live trading

- A fully authenticated and tested Binance execution adapter.
- Order reconciliation and unknown-order handling.
- Portfolio, margin, leverage, fee, and funding controls.
- Production-grade kill switches and operator authentication.
- Realized PnL accounting from actual fills.
- A production RSS/news ingestion service with source allowlists and freshness checks.
- Cross-platform runtime paths and deployment configuration.
- Comprehensive automated integration and replay tests.

## Risk and safety posture

The project intentionally follows a fail-closed design:

- Invalid or stale inputs reduce confidence or block execution.
- Network failures use controlled fallback behavior.
- RAG output is advisory and cannot bypass deterministic controls.
- Dashboard controls are validated before affecting the math engine.
- The current execution layer simulates decisions; it is not a live trading gateway.

## Why this is useful as a portfolio project

This project demonstrates more than a trading strategy. It shows the engineering decisions required to connect AI-assisted research with quantitative execution safely:

- evidence-grounded sentiment instead of unsupported model claims;
- explicit market-regime and liquidity logic;
- mathematical impact and slippage modeling;
- runtime configuration with bounded controls;
- asynchronous networking and failure handling;
- atomic state projections and audit-friendly execution records;
- a clear separation between research, decision logic, risk controls, and user interface.

## Documentation

- [Product Requirements Document](./PRD.md)
- [Architecture](./Architecture.md)
- [Coding Rules](./Rules.md)

## Disclaimer

This repository is for research, education, and portfolio demonstration. It is not financial advice. Market data can be delayed or incomplete, simulations may differ materially from real execution, and no result shown by the system represents a guarantee of profit.
