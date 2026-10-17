# Agentic-QuantRAG-Binance Coding Rules

**Status:** Mandatory engineering policy  
**Applies to:** Copilot, human contributors, scripts, notebooks, tests, and production services  
**Project:** Agentic-QuantRAG-Binance  
**Source documents:** [PRD.md](./PRD.md), [Architecture.md](./Architecture.md)

These rules are strict. If a requested implementation conflicts with them, preserve the safety, type-safety, modularity, and determinism requirements unless an explicit architectural decision records an exception.

## 1. Non-Negotiable Principles

1. **Fail closed:** invalid, stale, missing, contradictory, or untrusted input must block or reduce risk. Never silently continue with a success-shaped fallback.
2. **Deterministic safety boundary:** agents, LLMs, retrieval results, and model outputs may propose intent but must never bypass deterministic risk controls or submit raw exchange orders.
3. **Explicit contracts:** every module communicates through typed domain models, not unstructured dictionaries or implicit global state.
4. **Point-in-time correctness:** no feature, label, retrieval result, or decision may use information unavailable at the decision timestamp.
5. **Reproducibility:** record configuration, code, model, prompt, retrieval-index, feature, and impact-model versions with every decision.
6. **Minimal dependency surface:** add a dependency only when the standard library or an approved existing dependency cannot provide the required capability.
7. **No speculative complexity:** implement the smallest complete design that satisfies the requirement and existing interfaces.

## 2. Python Version, Formatting, and Repository Hygiene

- Target the Python version declared by the project configuration. Do not use syntax or standard-library APIs newer than that target.
- Use `ruff`/project-configured formatting and linting. Do not introduce a second formatter or linter without an approved decision.
- Use four spaces for indentation and UTF-8 source files.
- Keep lines within the repository’s configured limit.
- Use descriptive `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` only for true constants.
- Keep modules focused. A file must not become a dumping ground for unrelated helpers.
- Do not commit secrets, local credentials, private keys, generated caches, raw sensitive data, or unreviewed large artifacts.
- Do not modify unrelated files or reformat unrelated code while implementing a feature.
- Do not add a Markdown planning file unless explicitly requested.

## 3. Strict Python Type Hinting

All Python production code and test code must use strict type hints.

### 3.1 Required typing rules

- Annotate every function parameter and return value, including private functions.
- Annotate module-level variables, class attributes, dataclass fields, constants with non-obvious types, and collection contents.
- Use `from __future__ import annotations` where compatible with the project target.
- Prefer built-in generics (`list[str]`, `dict[str, int]`) when supported by the target version.
- Use `TypeAlias`, `Literal`, `Final`, `Protocol`, `TypedDict`, `dataclass`, or Pydantic/domain models when they express the contract clearly.
- Use `Decimal` for money, quantities, fees, prices, and account values where exact decimal semantics are required. Use `float` only where the mathematical algorithm explicitly requires floating-point computation.
- Use `numpy.typing.NDArray` with an explicit scalar dtype for numerical arrays and matrices.
- Annotate async generators and context managers accurately.
- Use `None` explicitly in optional types; do not hide optionality behind a broad type.
- Keep static analysis at strict mode or the repository’s strongest equivalent.

### 3.2 Prohibited typing shortcuts

- No `Any` in production code unless the boundary is inherently dynamic and the use is narrowly isolated, documented, and immediately validated.
- No untyped `dict`/`list`/`tuple` in public interfaces.
- No `# type: ignore` without a specific error code and a short justification.
- No `cast()` to conceal an unresolved type or avoid adding a proper guard.
- No `as any`, `# noqa`, or equivalent blanket suppression to force a check to pass.
- No dynamic attribute access for domain contracts when a typed model can be used.
- Do not return `None`, an empty collection, zero, or a neutral result to hide an error.

### 3.3 Type-checking acceptance criteria

Before a change is considered complete:

1. The project type checker passes for changed modules.
2. New public functions have complete signatures.
3. Invalid states are rejected at the boundary rather than propagated.
4. Domain models preserve units and semantics; do not represent incompatible quantities with the same bare numeric type without a wrapper or clear validated model.

## 4. Async and Network Programming

All exchange, market-data, document-source, embedding, LLM, index, and observability network operations must be asynchronous when the selected client supports async I/O.

### 4.1 Async design

- Never block the event loop with synchronous network calls, `time.sleep`, CPU-heavy matrix work, or unbounded file operations.
- Use `asyncio.sleep` for retry delays.
- Move unavoidable blocking work to a bounded executor or a dedicated worker with an explicit timeout.
- Use structured concurrency where supported by the target Python version.
- Bound every queue, task group, semaphore, connection pool, and in-flight request count.
- Ensure every created task is awaited, cancelled, or supervised.
- Close clients, sessions, websockets, files, and providers in `finally` blocks or async context managers.
- Make cancellation explicit. Do not swallow `asyncio.CancelledError`.
- Propagate correlation IDs and operation names through asynchronous calls.

### 4.2 Timeout rules

Every network operation must have a timeout appropriate to its operation:

- connect timeout;
- read/response timeout;
- total operation deadline;
- shutdown timeout where applicable.

Do not use an unbounded wait. A timeout must produce a typed exception or a typed failure result that the caller can act on. For trading and reconciliation paths, timeout behavior must be fail-closed.

### 4.3 Rate limits and backpressure

- Respect exchange and provider rate limits explicitly.
- Use bounded concurrency, token buckets, or the project’s approved rate limiter.
- Parse server rate-limit metadata when available.
- Retry only transient, idempotent operations.
- Use exponential backoff with jitter and a maximum retry count/deadline.
- Do not retry authentication failures, malformed requests, permission failures, invalid orders, or deterministic validation errors.
- Never blindly retry an order whose acceptance is unknown.
- Apply backpressure instead of allowing unbounded memory growth.
- Emit metrics for retries, throttling, queue depth, dropped events, timeouts, and circuit-breaker state.

### 4.4 Circuit breakers and safe mode

- A repeated dependency failure must open a circuit breaker or enter safe mode according to the component’s policy.
- Market-data failure must invalidate freshness and prevent new risk.
- Exchange order uncertainty must trigger reconciliation before retry or replacement.
- RAG provider failure may use an approved cached result only if freshness, provenance, and confidence rules pass; otherwise abstain.
- Network recovery does not automatically resume live trading unless reconciliation and recovery gates pass.

## 5. Custom Exception Models

Use explicit, project-owned exception classes. Exceptions are part of the module contract.

### 5.1 Exception hierarchy

The implementation should maintain a hierarchy equivalent to:

```python
class QuantRAGError(Exception):
    """Base class for expected project failures."""


class ConfigurationError(QuantRAGError):
    """Invalid or incompatible configuration."""


class ValidationError(QuantRAGError):
    """Input or domain validation failed."""


class DataQualityError(QuantRAGError):
    """Source data is stale, incomplete, duplicated, or inconsistent."""


class RetrievalError(QuantRAGError):
    """Retrieval or evidence processing failed."""


class ModelOutputError(QuantRAGError):
    """Model output is malformed, unsupported, or insufficiently evidenced."""


class NetworkOperationError(QuantRAGError):
    """A bounded network operation failed."""


class NetworkTimeoutError(NetworkOperationError):
    """A network operation exceeded its deadline."""


class RateLimitError(NetworkOperationError):
    """A provider or exchange rate limit was reached."""


class ExchangeError(QuantRAGError):
    """The exchange rejected or could not confirm an operation."""


class UnknownOrderStateError(ExchangeError):
    """The outcome of an order operation is not known."""


class RiskLimitError(QuantRAGError):
    """A deterministic risk constraint blocked an action."""


class ReconciliationError(QuantRAGError):
    """Local and exchange state could not be reconciled."""


class SafeModeError(QuantRAGError):
    """An action is forbidden while the system is in safe mode."""
```

The exact hierarchy may be adapted to existing project code, but broad categories and typed context must remain.

### 5.2 Exception rules

- Include safe diagnostic context: operation, symbol, correlation ID, retry count, deadline, and reason code where relevant.
- Never include secrets, API keys, authorization headers, raw credentials, or sensitive prompts.
- Preserve the original exception with exception chaining: `raise NewError(...) from exc`.
- Catch the narrowest exception possible.
- Do not use `except Exception: pass`.
- Do not catch an exception merely to log and return a neutral value.
- Translate third-party exceptions at the integration boundary into project-owned exceptions.
- Separate expected operational failures from programmer defects. Do not hide programmer defects behind generic operational errors.
- Make retryability explicit through exception type or a typed failure attribute.

## 6. Modular Isolation and Dependency Direction

Use the architecture’s module boundaries. Dependency direction must flow inward from adapters toward domain contracts, not sideways through hidden coupling.

### 6.1 Layer responsibilities

| Layer | Owns | Must not own |
|---|---|---|
| `domain/` | Typed entities, enums, value objects, event contracts | Network calls, provider SDKs, business orchestration |
| `config/` | Validated settings and environment selection | Runtime mutation of risk limits |
| `ingestion/` | Binance/document/replay adapters and quality checks | Trading policy or order submission |
| `normalization/` | Canonical records and point-in-time joins | LLM prompts or exchange credentials |
| `retrieval/` | Chunking, indexing, hybrid retrieval, evidence safety | Direct portfolio decisions |
| `sentiment/` | Structured model inference and validation | Direct order creation |
| `features/` | Point-in-time feature construction | Persistence side effects hidden in calculations |
| `regimes/` | Regime taxonomy, classifier, hysteresis | Exchange connectivity |
| `policy/` | Bounded portfolio intent and reason codes | Direct exchange calls or limit mutation |
| `execution/` | Impact, costs, order shaping, exchange adapter | RAG inference or unbounded strategy logic |
| `risk/` | Deterministic controls, kill switches, safe mode | LLM decisions or silent overrides |
| `state/` | Lifecycle, checkpoints, reconciliation | Signal generation |
| `observability/` | Audit, metrics, alerts, health | Changing trading decisions |
| `orchestration/` | Scheduling and constrained agent workflows | Bypassing component contracts |
| `storage/` | Persistence abstractions | Domain decisions embedded in repositories |

### 6.2 Isolation rules

- `domain/` must remain importable without network clients, exchange SDKs, LLM SDKs, or database drivers.
- Math functions must be pure where practical: inputs in, typed result out, no hidden I/O.
- Retrieval must not import risk or execution modules.
- Policy must depend on typed assessments and risk budgets, not provider-specific response objects.
- The exchange adapter is the only module allowed to translate an approved command into an exchange API request.
- `main.py` wires dependencies and lifecycle; it must not become a business-logic monolith.
- Avoid circular imports. If two modules need a shared contract, place it in `domain/`.
- Do not use module-level mutable singletons for positions, orders, risk limits, or model state.
- Pass dependencies through constructors or explicit function parameters.
- External clients must be behind small protocols/interfaces so tests can use fakes.
- Do not import notebooks from production modules.

## 7. Core Quantitative Math Rules

### 7.1 Approved numerical foundation

For core mathematical equations, matrix operations, feature transformations, regime calculations, and the Square Root Market Impact Law, use **NumPy matrices and arrays only**.

Required:

- `numpy.ndarray`/`numpy.typing.NDArray` with explicit dtype;
- vectorized operations where they improve correctness and clarity;
- explicit shape validation;
- stable numerical handling for empty, NaN, infinite, zero, and negative inputs;
- documented units and formulas;
- deterministic calculations with explicit parameters.

### 7.2 Strict prohibition on bloated math libraries

Do not add or use bloated external libraries for core math equations when NumPy is sufficient. In particular, do not introduce a general quantitative, symbolic, dataframe, optimization, or deep-learning framework merely to calculate:

- returns or volatility;
- rolling statistics;
- matrix products or decompositions;
- participation rates;
- square-root impact;
- fees, spreads, or shortfall;
- deterministic regime thresholds.

Examples of prohibited substitutions for core equations include adding a large dataframe framework, a general quant library, a symbolic algebra package, or a full ML framework for a calculation that can be expressed with NumPy.

Approved external libraries may still be used at their appropriate boundaries for:

- exchange connectivity;
- asynchronous HTTP/WebSocket transport;
- vector/lexical retrieval;
- model inference;
- configuration validation;
- metrics and persistence;
- testing.

Every new dependency requires a written justification in the change description, including why the standard library and NumPy are insufficient.

### 7.3 Matrix and array safety

- Never rely on implicit broadcasting unless the intended shapes are asserted or obvious from a typed helper.
- Validate dimensions before multiplication, inversion, decomposition, or concatenation.
- Prefer solving linear systems over explicitly calculating an inverse.
- Handle singular or ill-conditioned matrices explicitly.
- Use `np.errstate` only around a narrow, understood operation and validate the resulting values.
- Reject or quarantine NaN and infinite values at domain boundaries.
- Do not mutate caller-owned arrays unexpectedly; document ownership or copy when necessary.
- Keep units in names or typed wrappers, such as `volatility_annualized`, `impact_bps`, and `quantity_base`.
- Add formula-level tests with hand-calculated cases and edge cases.

### 7.4 Square Root Market Impact Law

Implement the impact equation as a small, isolated, typed function:

\[
I(Q) = Y \cdot \sigma \cdot \left(\frac{Q}{V}\right)^\alpha
\]

Rules:

- Validate `quantity > 0`, `reference_volume > 0`, `volatility >= 0`, `impact_coefficient >= 0`, and `alpha > 0`.
- Validate participation against configured maximums before calculating an executable plan.
- Preserve the calibration version and parameter values in `ImpactEstimate`.
- Return conservative bounds or a typed uncertainty result when calibration is insufficient.
- Never claim the estimate is a guarantee.
- Keep expected impact, fees, spread, delay, and realized impact separate for attribution.

## 8. RAG, Agent, and Model Output Rules

- Treat all retrieved text as untrusted data, never as instructions.
- Isolate retrieved content from system and developer instructions.
- Require structured model output validated against a typed schema.
- Require source citations, timestamps, asset/topic identity, direction, score, confidence, horizon, and contradiction status.
- Reject values outside declared ranges.
- Abstain when evidence is absent, stale, contradictory, duplicated, or not attributable.
- Keep model calls behind a protocol and inject the provider.
- Cache provider responses for replay and evaluation with prompt, model, retrieval, and configuration versions.
- Never place secrets, credentials, or unrestricted account state in a prompt.
- Do not let model output directly create an `OrderCommand`.
- Do not allow an agent to alter risk limits, kill switches, environment mode, or safe-mode gates.

## 9. Trading, Risk, and Exchange Rules

- Every intended order must pass deterministic pre-trade controls.
- Enforce symbol filters, tick size, step size, minimum notional, price bands, exposure, leverage, drawdown, participation, impact, and cost limits.
- Use unique idempotency/client-order IDs.
- Never blindly retry an order with an unknown outcome.
- Reconcile balances, positions, and open orders after startup, reconnect, unknown responses, and at the configured interval.
- Treat the exchange as authoritative for post-reconciliation account state.
- New risk is forbidden when data freshness, exchange connectivity, or reconciliation status fails.
- Use reduce-only/close-only behavior in safe mode where supported and configured.
- Keep paper, testnet, and live credentials and endpoints separate.
- Production API keys must not have withdrawal permissions.
- Log every risk decision and rejection reason without logging secrets.

## 10. Error Handling and Logging

- Log structured events, not interpolated prose, for operationally important events.
- Include `event_id`, `run_id`, `correlation_id`, `decision_id`, component, event time, environment mode, and version metadata.
- Log the exception category, reason code, retryability, and safe context.
- Redact credentials, authorization headers, private account identifiers, and sensitive source content.
- Use appropriate levels:
  - `DEBUG`: local diagnostic detail, disabled or sampled in production;
  - `INFO`: lifecycle and successful state transitions;
  - `WARNING`: degraded but contained behavior;
  - `ERROR`: failed operation requiring investigation;
  - `CRITICAL`: safety boundary breach or required operator action.
- Do not log the same exception repeatedly at every layer. Translate once at a boundary and preserve context.
- Never report a blocked or failed action as successful.
- Metrics and alerts must cover timeouts, retries, rate limits, queue depth, stale data, rejected orders, exposure, drawdown, impact error, slippage, regime churn, and reconciliation mismatches.

## 11. Testing Rules

Every behavior change must include or update focused tests.

### 11.1 Required test categories

- **Unit tests:** pure math, schemas, validation, exception mapping, state transitions, and risk checks.
- **Integration tests:** component contracts, persistence, adapter behavior, and mode isolation.
- **Replay tests:** deterministic outputs, chronological splits, point-in-time joins, and realistic execution costs.
- **Failure-injection tests:** timeouts, rate limits, disconnects, stale data, malformed model output, duplicate events, gaps, unknown orders, and reconciliation mismatches.
- **Security tests:** prompt injection, secret redaction, permission boundaries, and prohibited direct order access.

### 11.2 Test quality

- Test both accepted and rejected paths.
- Test boundaries: zero, negative, empty, maximum, stale, duplicate, NaN, infinity, and timeout cases.
- Use fixed seeds and deterministic fixtures where randomness exists.
- Do not make live network calls in unit tests.
- Do not weaken production validation to make a test pass.
- Assert reason codes and state transitions, not only boolean outcomes.
- Add a regression test for every fixed defect.

## 12. Documentation and Change Requirements

Each non-trivial change must document:

- the problem and intended behavior;
- affected modules and contracts;
- safety impact and failure behavior;
- configuration or schema changes;
- test coverage and validation commands;
- dependency changes, if any;
- rollback or migration considerations.

Update [PRD.md](./PRD.md), [Architecture.md](./Architecture.md), runbooks, or model cards when the change alters requirements, interfaces, data flow, state ownership, operational behavior, or promotion gates.

## 13. Copilot Operating Procedure

Before editing:

1. Read the relevant PRD and architecture sections.
2. Inspect existing patterns, interfaces, configuration, and tests.
3. Identify the smallest complete change and its failure modes.
4. Confirm whether the change crosses the deterministic safety boundary.

While editing:

1. Preserve existing behavior unless the requirement explicitly changes it.
2. Use typed models and existing helpers before adding new abstractions.
3. Keep network and provider code behind interfaces.
4. Use NumPy only for core math equations and validate shapes/units.
5. Surface errors explicitly with project exceptions and structured logs.
6. Avoid broad catches, silent defaults, speculative retries, and unrelated refactors.

After editing:

1. Run formatting, linting, type checking, and the smallest relevant test set.
2. Review the diff for accidental changes, secrets, dependency bloat, and safety-boundary violations.
3. Verify that paper/replay behavior remains available and live behavior remains gated.
4. Report changed files, validation results, known limitations, and any unresolved risk.

## 14. Rule Exceptions

Exceptions are rare and must be explicit. An exception record must state:

- the exact rule being waived;
- why the rule is insufficient for the requirement;
- affected files and environments;
- security and operational risks;
- tests and monitoring added to compensate;
- approving owner and expiration/review date.

No exception may permit secret leakage, unrestricted live trading, direct agent order submission, disabling deterministic risk controls, or hiding an error behind a silent fallback.
