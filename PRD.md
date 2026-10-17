# Production Requirements Document

## Project: Agentic-QuantRAG-Binance

**Document status:** Production-ready baseline  
**Version:** 1.0  
**Date:** 2026-10-17  
**Primary users:** Quant recruiters, quantitative researchers, execution engineers, and risk owners  
**Deployment posture:** Research-first, paper-trading by default, controlled promotion to live trading

> **Risk notice:** This product is an engineering and research system, not financial advice. It must not imply guaranteed returns. Live trading must remain disabled until risk, compliance, operational, and performance gates are explicitly approved.

## 1. Overview

### 1.1 Product summary

Agentic-QuantRAG-Binance is an autonomous, evidence-grounded quantitative trading research and execution platform for Binance market data. It combines:

1. A Python Retrieval-Augmented Generation (RAG) sentiment framework that retrieves, ranks, and cites market-relevant documents and social/news signals.
2. A market-regime classifier that converts price, volatility, liquidity, and sentiment evidence into an interpretable regime state.
3. A scale-adaptation policy that changes target exposure and order aggressiveness according to the regime and confidence.
4. A layered execution-risk engine based on the Square Root Market Impact Law to estimate expected price impact and constrain participation.
5. A deterministic execution gateway with hard risk limits, observability, reconciliation, and kill switches.

The system is intended to demonstrate production-grade quantitative thinking: separating signal generation from portfolio intent, portfolio intent from execution, and execution from exchange connectivity.

### 1.2 Problem statement

Market sentiment is noisy, stale, and vulnerable to narrative shifts. A static position-sizing or execution rule can therefore overtrade during unstable conditions, underreact to material information, or incur avoidable market impact. The project addresses this by:

- grounding sentiment outputs in retrieved source evidence;
- making regime classification explicit, versioned, and measurable;
- scaling risk rather than treating model output as an unconditional trade instruction;
- estimating execution cost before sending orders;
- preventing an agent or model from bypassing deterministic controls.

### 1.3 Goals

- Produce timestamped, source-cited sentiment and regime assessments.
- Adapt exposure, order size, participation rate, and execution urgency to regime conditions.
- Estimate market impact using a calibrated square-root relationship:

  \[
  \text{Impact}_{t} = Y \cdot \sigma_{t} \cdot \left(\frac{Q_{t}}{V_{t}}\right)^{\alpha}
  \]

  where \(Y\) is a calibrated impact coefficient, \(\sigma_t\) is volatility, \(Q_t\) is proposed quantity, \(V_t\) is reference traded volume, and \(\alpha\) is initially constrained near \(0.5\) and empirically estimated.
- Keep all decisions reproducible through model, prompt, retrieval-index, feature, and configuration versioning.
- Provide a safe path from historical replay to paper trading and then to tightly bounded live trading.
- Give quant recruiters a clear, inspectable demonstration of research quality, engineering discipline, and risk awareness.

### 1.4 Non-goals

- Guaranteeing profitability or eliminating market risk.
- High-frequency trading, co-location, latency arbitrage, or market making in the initial release.
- Autonomous withdrawal, transfer, or account-management actions.
- Trading assets or venues outside the explicitly configured Binance scope.
- Allowing an LLM to submit an order directly or override deterministic controls.
- Treating generated text as a trade signal without structured features, confidence, provenance, and validation.

### 1.5 Product principles

1. **Evidence before inference:** sentiment must link to retrieved source documents and timestamps.
2. **Risk before action:** every intended order must pass independent pre-trade controls.
3. **Determinism at the boundary:** LLM and agent outputs are advisory; order validation and routing are deterministic.
4. **Fail closed:** stale, missing, contradictory, or unverifiable inputs reduce risk or block trading.
5. **Reproducibility:** every decision must be replayable from immutable inputs and versions.
6. **Human accountability:** live trading requires explicit configuration, approvals, and operational ownership.

## 2. Target Audience (Quant Recruiters)

This project is optimized as a portfolio-grade system that lets quant recruiters evaluate both research depth and production judgment.

### 2.1 What recruiters should be able to assess

- **Quantitative reasoning:** regime definitions, feature construction, calibration, impact modeling, and uncertainty handling.
- **Market microstructure awareness:** volume participation, spread, volatility, slippage, order-book depth, and adverse selection.
- **ML/RAG competence:** retrieval quality, temporal leakage prevention, sentiment calibration, model evaluation, and source attribution.
- **Software engineering:** typed Python modules, clear interfaces, testability, configuration management, and reliable exchange adapters.
- **Risk discipline:** exposure caps, notional limits, stale-data behavior, circuit breakers, reconciliation, and kill switches.
- **Research integrity:** chronological splits, realistic fees and slippage, walk-forward evaluation, ablations, and out-of-sample reporting.
- **Communication:** dashboards and reports that explain why a decision occurred, not just whether it made money.

### 2.2 Demonstration scenarios

The product should include replayable scenarios for:

- a calm, liquid trend where scale increases only when sentiment and market evidence agree;
- a high-volatility regime where exposure and participation are reduced;
- conflicting sentiment sources where the system defers or holds a smaller position;
- a liquidity shock where the impact estimate blocks an order;
- stale market data or exchange disconnection where the system enters safe mode;
- a model or prompt version change where results are compared against a baseline before promotion.

## 3. Core Features

### 3.1 Binance market-data ingestion

**Requirements**

- Support configurable Binance Spot and/or USDⓈ-M Futures scopes; the initial deployment must explicitly select one.
- Ingest trades, klines, ticker, order-book snapshots/deltas where available, funding/open interest for futures where permitted, and exchange metadata.
- Normalize timestamps to UTC and preserve exchange event time, receive time, and processing time.
- Persist raw immutable events and normalized analytical tables.
- Track sequence numbers, duplicates, gaps, late events, and reconnects.
- Enforce symbol filters, tick size, step size, minimum notional, precision, and trading-status constraints from exchange metadata.
- Use rate-limit-aware clients with retries, backoff, and bounded queues.

**Acceptance criteria**

- A replay can reconstruct the normalized market state from raw events.
- Data-quality checks report missing intervals, duplicate events, clock skew, and schema violations.
- No order can be generated from data outside a configurable freshness window.

### 3.2 RAG sentiment framework

**Requirements**

- Ingest permitted news, official announcements, research notes, and configured public sentiment sources.
- Normalize, deduplicate, language-tag, timestamp, and hash every document.
- Store document metadata including source, publication time, ingestion time, asset/topic entities, and licensing/retention status.
- Chunk documents with stable identifiers and generate embeddings using a versioned model.
- Retrieve using hybrid lexical and vector search, with recency and source-quality weighting.
- Require the generation layer to return structured output:
  - asset or topic;
  - sentiment direction: bullish, bearish, neutral, or mixed;
  - sentiment score in `[-1, 1]`;
  - confidence in `[0, 1]`;
  - time horizon;
  - novelty/materiality;
  - cited chunk identifiers;
  - contradictions or missing evidence.
- Reject or downgrade outputs without citations, with impossible values, or with unsupported claims.
- Detect duplicate narratives, prompt injection in retrieved content, and source concentration.
- Keep LLM prompts, model identifiers, retrieval settings, and output schemas versioned.

**Acceptance criteria**

- Every sentiment event is traceable to source chunks and model versions.
- Retrieval and sentiment quality are evaluated on a labeled, time-aware holdout set.
- The system falls back to neutral/low-confidence behavior when retrieval is empty, stale, contradictory, or unavailable.

### 3.3 Market-regime detection

The first version should use an interpretable, configurable regime taxonomy:

- low-volatility trend;
- high-volatility trend;
- range-bound/mean-reverting;
- liquidity-stressed;
- event/shock;
- unknown or insufficient-data.

**Candidate features**

- realized volatility across multiple windows;
- volatility-of-volatility;
- trend and momentum measures;
- return autocorrelation;
- spread and order-book imbalance;
- volume and volume anomaly;
- funding and basis where applicable;
- drawdown and gap measures;
- aggregated sentiment, confidence, novelty, and disagreement;
- market-wide risk proxies.

**Requirements**

- Use chronological train/validation/test splits and walk-forward evaluation.
- Prevent look-ahead leakage: only data available at decision time may enter a feature.
- Produce regime probabilities and an uncertainty/unknown state.
- Version regime definitions, thresholds, models, and feature windows.
- Support a transparent baseline, such as rule-based regimes, alongside any learned classifier.
- Log feature values and the regime decision for every portfolio decision.

**Acceptance criteria**

- Regime labels are stable enough to avoid excessive flip-flopping, with configurable hysteresis or minimum dwell time.
- Performance is reported by regime, not only in aggregate.
- Unknown or low-confidence regimes reduce risk and never silently map to a high-risk state.

### 3.4 Adaptive trading-scale policy

The policy converts signal and regime information into a bounded target scale. It must not directly choose arbitrary order quantities.

**Inputs**

- regime probabilities and confidence;
- sentiment score, confidence, novelty, and disagreement;
- volatility and liquidity state;
- current exposure, open orders, realized/unrealized PnL, and drawdown;
- impact estimate and expected fees;
- portfolio and account risk budgets.

**Outputs**

- target position or target risk, bounded by configuration;
- maximum participation rate;
- maximum child-order size;
- execution urgency;
- preferred order type;
- expiry time and cancellation policy;
- rationale and rule identifiers.

**Policy requirements**

- Scale up only when signal confidence, regime suitability, liquidity, and risk budget all pass.
- Scale down on increased volatility, adverse impact, declining confidence, drawdown, or data-quality degradation.
- Support a no-trade/hold state.
- Apply hard caps before any optimization or agent reasoning.
- Include cooldowns and hysteresis to limit churn.
- Separate alpha confidence from execution confidence.
- Make the policy explainable through structured reason codes.

### 3.5 Execution-risk layer using the Square Root Market Impact Law

The execution layer estimates the cost and risk of completing a proposed order.

**Core calculations**

- Estimate volatility on a documented horizon and sampling frequency.
- Estimate reference volume using a robust rolling window, with outlier handling.
- Compute participation \(q = Q/V\) and reject invalid or extreme inputs.
- Estimate expected impact:

  \[
  I(Q) = Y \cdot \sigma \cdot \left(\frac{Q}{V}\right)^{\alpha}
  \]

- Calculate expected implementation shortfall as:

  \[
  \text{Shortfall} =
  \text{fees} + \text{spread cost} + I(Q) + \text{delay/adverse-selection allowance}
  \]

- Return confidence intervals or conservative bounds where calibration supports them.

**Risk layers**

1. **Input validity:** freshness, completeness, units, symbol rules, and numerical sanity.
2. **Marketability:** spread, depth, volatility, and liquidity-stress checks.
3. **Impact budget:** maximum expected impact in basis points and maximum participation.
4. **Order shaping:** split parent orders into child orders using TWAP/VWAP or a bounded adaptive schedule.
5. **Pre-trade limits:** notional, position, leverage, concentration, rate, and loss limits.
6. **Exchange safety:** reduce-only/close-only constraints where appropriate, client-order identifiers, and idempotency.
7. **Post-trade controls:** fills, slippage, realized impact, remaining quantity, and reconciliation.

**Calibration requirements**

- Calibrate \(Y\) and \(\alpha\) by symbol, market type, and liquidity bucket where sample size permits.
- Use only historical information available before each evaluation period.
- Compare predicted versus realized impact and report MAE, bias, tail error, and calibration by participation bucket.
- Use conservative fallback parameters when calibration confidence is inadequate.
- Never use the square-root model as a guarantee; it is an estimate used to constrain risk.

**Acceptance criteria**

- Orders exceeding impact, participation, or notional budgets are blocked or resized deterministically.
- Realized execution cost is attributed to spread, fees, impact, delay, and residual unexplained slippage.
- The system can replay a decision and show the pre-trade impact estimate beside realized outcomes.

### 3.6 Portfolio risk and controls

Required configurable controls include:

- maximum gross and net exposure;
- maximum symbol exposure and concentration;
- maximum order notional and daily notional;
- leverage and margin thresholds for futures;
- maximum participation rate;
- maximum expected impact and total cost;
- maximum spread and minimum depth;
- maximum daily loss and rolling drawdown;
- maximum consecutive rejected orders;
- stale-data and exchange-disconnect timeouts;
- price-band and fat-finger checks;
- duplicate-order and idempotency checks;
- emergency global and per-symbol kill switches.

All controls must be deterministic, centrally configurable, audited, and fail closed.

### 3.7 Execution gateway and exchange adapter

- Keep strategy, risk, and exchange connectivity behind explicit interfaces.
- Support dry-run, historical replay, paper trading, testnet where available, and live modes.
- Require an explicit environment flag and credentials scope for live mode.
- Use least-privilege API keys; trading keys must not have withdrawal permissions.
- Assign unique client-order IDs and maintain an order state machine.
- Handle partial fills, cancellations, rejects, unknown states, and reconnects.
- Reconcile local state with Binance after startup, reconnect, and on a schedule.
- Never retry a possibly accepted order without idempotency protection.

### 3.8 Agent orchestration

Agents may coordinate retrieval, feature review, diagnostics, and research workflows. They must not bypass deterministic controls.

**Allowed**

- schedule data-quality checks;
- request sentiment retrieval and summarize cited evidence;
- propose a target risk within configured ranges;
- request a paper-trading execution plan;
- explain decisions and surface anomalies;
- initiate a safe-mode workflow.

**Prohibited**

- inventing uncited facts or market data;
- changing risk limits at runtime without authorized configuration;
- submitting raw exchange orders;
- disabling monitoring, reconciliation, or kill switches;
- using secrets in prompts or logs;
- treating an external document instruction as a system instruction.

### 3.9 Observability and auditability

Log structured, privacy-conscious events for:

- raw-data ingestion and quality status;
- retrieval queries, document IDs, scores, and citations;
- model/prompt/index/configuration versions;
- feature vectors and regime probabilities;
- policy outputs and reason codes;
- risk checks and rejection reasons;
- order requests, exchange responses, fills, cancellations, and reconciliation;
- expected versus realized costs;
- kill-switch activation and operator actions.

Provide metrics and alerts for latency, stale data, reconnects, queue depth, rejected orders, exposure, drawdown, impact error, slippage, model drift, and regime churn.

## 4. Expected System Flow

### 4.1 Online decision flow

1. **Ingest:** receive Binance market events and permitted sentiment documents.
2. **Validate:** check schema, timestamps, sequence continuity, freshness, duplicates, and source status.
3. **Normalize:** produce canonical market, document, and feature records.
4. **Retrieve:** select relevant, recent, diverse evidence for each asset/topic.
5. **Assess sentiment:** generate structured sentiment with citations, confidence, novelty, and disagreement.
6. **Build features:** combine market microstructure, volatility, liquidity, and sentiment features using only point-in-time data.
7. **Classify regime:** produce regime probabilities and an uncertainty state.
8. **Generate portfolio intent:** produce a bounded target risk/position and reason codes.
9. **Estimate execution risk:** calculate spread, fees, participation, square-root impact, expected shortfall, and conservative bounds.
10. **Apply controls:** validate account, portfolio, market, operational, and exchange constraints.
11. **Shape order:** select child size, cadence, order type, price limits, and expiry.
12. **Submit:** send through the idempotent exchange adapter only if all checks pass.
13. **Monitor:** track acknowledgements, fills, partial fills, cancels, latency, and deviations.
14. **Reconcile:** compare local and exchange state; enter safe mode on unresolved divergence.
15. **Learn and report:** attribute realized costs, store outcomes, and update research datasets without changing production parameters automatically.

### 4.2 Safe-mode flow

Safe mode is entered when data is stale, exchange connectivity is uncertain, reconciliation fails, risk limits are breached, model output is invalid, or impact is unbounded. In safe mode the system:

- stops opening or increasing positions;
- may cancel working orders according to a configured policy;
- may permit only approved reduce-only/close-only actions;
- emits an operator alert;
- records the triggering condition and recovery steps;
- requires explicit recovery criteria before resuming.

## 5. Data and Technical Requirements

### 5.1 Suggested Python architecture

```text
src/
  ingestion/        # Binance and document connectors
  normalization/    # schemas, timestamps, quality checks
  retrieval/        # chunking, indexing, hybrid retrieval
  sentiment/        # structured RAG inference and validation
  features/         # point-in-time feature generation
  regimes/          # classifiers and rule-based baselines
  policy/            # bounded target-risk decisions
  execution/        # impact model, order shaping, adapter
  risk/             # deterministic pre/post-trade controls
  orchestration/    # scheduling and agent workflows
  observability/    # metrics, audit events, reports
tests/
  unit/
  integration/
  replay/
configs/
notebooks/
```

The exact framework may vary, but boundaries and contracts must remain explicit.

### 5.2 Canonical entities

- `MarketEvent`: exchange event time, receive time, symbol, event type, values, sequence metadata.
- `Document`: immutable ID, source, publication time, ingestion time, text hash, licensing status, entities.
- `RetrievalEvidence`: document/chunk ID, score, rank, source quality, recency, query version.
- `SentimentAssessment`: asset, score, direction, confidence, horizon, novelty, disagreement, citations.
- `RegimeAssessment`: probabilities, selected regime, uncertainty, feature version, model version.
- `PortfolioIntent`: target exposure, maximum risk, expiry, rationale, policy version.
- `ImpactEstimate`: quantity, volume, volatility, participation, \(Y\), \(\alpha\), expected impact, confidence.
- `RiskDecision`: pass/block/resize, checks, limits, observed values, reason codes.
- `OrderLifecycle`: client ID, exchange ID, request, acknowledgements, fills, cancels, final status.

### 5.3 Security and privacy

- Store secrets in a secret manager or secure environment mechanism, never in source, prompts, notebooks, or logs.
- Use separate credentials and accounts for development, testnet/paper, and production.
- Restrict production access by role and require MFA where supported.
- Redact API keys, account identifiers, and sensitive payloads from logs.
- Maintain a documented retention and deletion policy for retrieved content.
- Treat retrieved text as untrusted input and isolate it from system/developer instructions.

## 6. Evaluation and Validation

### 6.1 Research protocol

- Use chronological splits and walk-forward evaluation for all temporal modeling.
- Fit preprocessing only on the training period; never leak future statistics.
- Compare against naive and simple baselines:
  - buy-and-hold or no-trade;
  - fixed-scale strategy;
  - rule-based regime strategy;
  - sentiment-only and market-only ablations.
- Model realistic fees, spread, latency, partial fills, rejected orders, and impact.
- Report results by symbol, regime, volatility bucket, liquidity bucket, and time period.
- Include confidence intervals or bootstrap intervals for key metrics.

### 6.2 Key metrics

**Signal and RAG**

- retrieval precision/recall or hit rate on labeled queries;
- citation validity and source coverage;
- sentiment direction F1 and calibration;
- abstention rate and contradiction detection quality;
- inference latency and cost.

**Regime**

- balanced accuracy/F1 where labels exist;
- regime persistence and transition stability;
- calibration error;
- downstream performance and drawdown by regime.

**Trading and execution**

- net return after fees and impact;
- Sharpe/Sortino, maximum drawdown, turnover, hit rate, and exposure;
- implementation shortfall;
- realized spread, realized impact, slippage, fill rate, cancel rate;
- impact prediction bias and tail error;
- risk-limit breach count, blocked-order rate, and safe-mode recovery time.

**Operations**

- data freshness and completeness;
- decision and order latency;
- reconciliation mismatch rate;
- mean time to detect and resolve incidents.

### 6.3 Promotion gates

No component may promote to live execution without:

1. Passing unit, integration, replay, failure-injection, and security tests.
2. Demonstrating no material look-ahead leakage.
3. Meeting agreed data freshness, latency, and reconciliation SLOs.
4. Showing stable out-of-sample behavior against the fixed-scale baseline.
5. Passing execution-cost and impact-calibration review.
6. Completing paper-trading observation for a defined period.
7. Receiving explicit risk-owner approval and an operational rollback plan.

## 7. Reliability and Operational Requirements

- **Availability:** graceful degradation is preferred to unsafe availability; trading may stop when dependencies fail.
- **Recovery:** restart and reconnect must reconcile before resuming.
- **Idempotency:** every order command must be safely retryable or explicitly marked non-retryable.
- **Backpressure:** ingestion and inference queues must have bounded capacity and observable overflow behavior.
- **Configuration:** versioned, reviewed, environment-specific, and validated at startup.
- **Deployments:** use canary or shadow mode for model, prompt, retrieval, and policy changes.
- **Rollback:** revert to the last approved model/configuration without code redeployment where safe.
- **Clock:** use synchronized UTC clocks and monitor clock skew.

## 8. User Experience and Deliverables

### 8.1 Operator dashboard

The dashboard should show:

- current mode: replay, paper, testnet, or live;
- account exposure, open orders, drawdown, and risk budget;
- current regime and probabilities;
- sentiment score, confidence, citations, and source freshness;
- intended versus executed scale;
- impact estimate versus realized impact;
- active limits, blocked actions, and kill-switch status;
- data-quality and exchange-connectivity health.

### 8.2 Recruiter-facing package

The repository should include:

- architecture diagram and sequence diagram;
- reproducible setup instructions;
- a short demo using deterministic replay data;
- a model card for the sentiment/regime components;
- an execution-cost and impact-calibration report;
- test and coverage summary;
- sample audit trail showing one accepted, one resized, and one blocked order;
- explicit limitations and known failure modes.

## 9. Milestones

### Phase 0: Foundations

- Define schemas, configuration, symbol scope, environments, and audit events.
- Build raw/normalized market-data ingestion and deterministic replay.
- Implement risk-control interfaces and a no-trade default.

### Phase 1: Research baseline

- Implement rule-based regimes, fixed-scale strategy, fee/spread simulator, and baseline reports.
- Establish chronological evaluation datasets and leakage checks.

### Phase 2: RAG sentiment

- Add document pipeline, hybrid retrieval, structured sentiment output, citations, and evaluation set.
- Add abstention, contradiction, source-quality, and prompt-injection defenses.

### Phase 3: Adaptive policy and impact

- Add regime-conditioned scale policy.
- Implement square-root impact estimation, calibration, order shaping, and cost attribution.

### Phase 4: Paper trading

- Add exchange adapter, reconciliation, dashboard, alerts, failure injection, and paper-trading soak test.

### Phase 5: Controlled live pilot

- Enable only approved symbols and bounded notional.
- Require operator approval, live kill switch, daily review, and automatic rollback to paper/safe mode on gate failure.

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Sentiment hallucination or unsupported claim | Bad signal | Structured schema, citations, abstention, source validation |
| Retrieval poisoning or prompt injection | Unsafe reasoning | Treat documents as untrusted data; isolate instructions; sanitize and audit |
| Look-ahead leakage | False backtest confidence | Point-in-time stores, chronological splits, leakage tests |
| Regime whipsaw | Excess turnover and cost | Hysteresis, minimum dwell time, confidence thresholds |
| Impact model misspecification | Excess slippage | Conservative bounds, bucketed calibration, realized-cost monitoring |
| Thin liquidity or sudden shock | Large loss | Participation/impact caps, shock regime, kill switches, reduce-only mode |
| Exchange/API failure | Unreconciled exposure | Idempotency, retries, reconciliation, safe mode |
| Credential compromise | Account loss | Least privilege, secret manager, no withdrawal permission, rotation |
| Model drift | Degraded decisions | Drift monitoring, shadow evaluation, promotion gates, rollback |
| Agent overreach | Control bypass | Deterministic gateway; agents cannot submit or alter orders/limits |

## 11. Definition of Done

The project is production-ready for a controlled pilot when:

- all core entities and interfaces are implemented and documented;
- deterministic risk controls are enforced independently of the agent/LLM;
- RAG outputs are cited, versioned, evaluated, and safely abstaining;
- regime and scale decisions are reproducible and evaluated out of sample;
- square-root impact is calibrated, monitored, and used to bound execution;
- replay, paper, and safe-mode paths are demonstrated;
- reconciliation and kill switches have passed failure-injection tests;
- dashboards, alerts, audit logs, and runbooks are available;
- promotion gates are met and a named risk owner approves the pilot;
- no live credentials or unrestricted live execution are enabled by default.

