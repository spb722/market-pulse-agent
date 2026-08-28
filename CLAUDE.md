Market Pulse Development Instructions

1. Objective

Productionize the existing Market Pulse implementation into a clean API application.

The existing business implementation is available under:

reference/step_1.py
reference/step_2.py
reference/step_3.py
reference/step_4.py
reference/step_5.py
reference/step_6.py

The objective is to productionize the existing implementation, not redesign it.

2. Read the Architecture First

Before implementing or modifying production code, read:

docs/architecture.md

docs/architecture.md defines:

the project structure

public API behavior

run_id

competitor_run_id

multi-competitor processing

intermediate stage storage

stage names

shared Omantel reference behavior

status handling

retry/failure behavior

testing expectations

Follow it unless the user explicitly gives a newer instruction.

If this file and docs/architecture.md appear inconsistent, stop and report the conflict instead of silently choosing a different architecture.

3. Source of Truth

Everything under reference/ is the source of truth for existing business behavior.

Treat reference/ as read-only.

Do not modify reference files.

Preserve:

formulas

weights

thresholds

plan matching behavior

classifications

gap calculations

risk calculations

LLM prompts and intended LLM responsibilities

output contracts

Do not invent or "improve" business rules unless explicitly requested.

4. Required Development Workflow

Implement one step at a time.

The required sequence is:

Step 1
→ verify
→ Step 2
→ verify
→ Step 3
→ verify
→ Step 4
→ verify
→ Step 5
→ verify
→ Step 6
→ verify

For every step:

Read the relevant reference/step_N.py.

Inspect the previous step's output contract when relevant.

Inspect docs/architecture.md.

Use the implementer agent to implement the production equivalent.

Put production code under src/.

Convert useful notebook validations into tests under tests/.

Run relevant tests.

Use the verifier agent for an independent review.

If the verifier returns FAIL, send the findings back to the implementer.

Fix the implementation.

Run tests again.

Ask the verifier to review again.

Do not proceed until the verifier returns PASS.

Do not implement later steps prematurely.

5. Agent Responsibilities

Implementer

Use the implementer subagent for:

implementing a reference step

fixing verifier findings

adding/updating tests

production refactoring required to support the reference behavior

The implementer may modify production code and tests.

Verifier

Use the verifier subagent after every implementation step.

The verifier must independently compare:

requirements

docs/architecture.md

reference implementation

production implementation

tests

The verifier should return:

PASS

or:

FAIL

with concrete findings.

The verifier must not fix production code.

6. Production Architecture

The public API is run-oriented.

Do not expose Steps 1–6 as six unrelated public APIs.

The primary model is:

Run
└── Competitor Run
    └── Stage Results

One run_id can contain multiple competitors.

Example:

RUN-1001
├── Ooredoo
├── Vodafone
└── Friendi

Each competitor receives its own competitor_run_id.

Read docs/architecture.md for the full API contract and processing behavior.

7. Multi-Competitor Rule

The same run_id is reused when submitting different competitors belonging to the same Market Pulse cycle.

Example:

RUN-1001
  CR-001 → Ooredoo
  CR-002 → Vodafone
  CR-003 → Friendi

Each competitor must be independently traceable and retryable.

Intermediate results must be stored by competitor and stage so the UI can read them while processing progresses.

8. Omantel Reference Rule

Step 2 prepares the Omantel reference data.

Do not unnecessarily rerun Step 2 independently for every competitor in the same run.

Prepare or reuse the Omantel reference as described in docs/architecture.md.

Competitors should analyze against the same applicable Omantel reference for that run.

9. Project Structure

Follow the structure in docs/architecture.md.

Production code belongs under:

src/market_pulse/

Tests belong under:

tests/

Reference code stays under:

reference/

Do not mix production implementation into reference/.

10. Business Logic Rules

Do not:

change Step 4 weights

change Step 5 risk formulas

change similarity formulas

change gap formulas

change thresholds

invent new product attributes

introduce embeddings

introduce BM25

combine base plans and add-ons into synthetic products

use LLMs for calculations that are deterministic in the reference

silently relax matching rules

silently correct source data

remove capability insights required by the reference

introduce market-segment matching where the reference deliberately ignores it

If a reference behavior appears questionable, preserve it and report it as a possible future improvement.

11. Notebook-to-Production Rules

The reference implementation may contain Jupyter/testing/demo code.

Do not copy notebook-only behavior into production.

Examples to remove:

display()

exploratory df.head()

random sampling used only for manual review

debug print() calls

temporary demo data

notebook-only verification cells

Convert useful validation logic into proper automated tests.

Use application logging instead of debug print statements.

12. Error Handling

Do not silently swallow errors that could change analysis results.

A failed competitor must not automatically invalidate completed competitor runs.

Preserve per-stage and per-competitor failure information.

Use the status model defined in docs/architecture.md.

13. LLM Integration

Keep LLM integration isolated under:

src/market_pulse/llm/

Do not initialize Groq/LLM clients throughout business modules.

Secrets must come from environment/configuration.

LLMs should only perform tasks that require semantic interpretation according to the reference implementation.

Deterministic calculations stay in Python.

14. Testing Expectations

For each step, create focused tests for:

input validation

expected output structure

deterministic calculations

important edge cases

reference/regression behavior

invalid/unsupported cases

For LLM-dependent behavior:

mock the LLM in unit tests where practical

validate schema and factual consistency

avoid exact text matching unless required

Do not proceed to the next step while relevant tests are failing.

15. Before Starting a Step

Before writing code for a step, briefly report:

Reference file:
Inputs:
Outputs:
Production module(s):
Tests to add:
Architecture impact:

Then begin implementation using the implementer agent.

Do not produce a large redesign proposal unless specifically requested.

Keep implementation focused.

16. First Development Task

When the user asks to begin coding:

Read docs/architecture.md.

Read reference/step_1.py.

Inspect the repository.

Briefly explain how Step 1 will map into the production structure.

Invoke the implementer agent for Step 1.

Run tests.

Invoke the verifier agent.

Resolve failures until PASS.

Only then continue to Step 2.

17. Production Hardening Log (added 2026-08-28)

After Steps 1-6 were productionized and the run-oriented API/orchestration/storage layer was built (see docs/architecture.md and docs/usage_guide.md), real end-to-end testing (real data, real LLM, real performance data) surfaced gaps not covered by the original six steps. The following changes were made on top of the verified Steps 1-6 business logic; none of them touch any formula, weight, or threshold in the reference implementations.

Real product performance data:

Real Omantel usage/ARPU data now lives at data/omantel/PRODUCT_PERFORMANCE.csv and is loaded by Step 5 (src/market_pulse/services/risk_analysis_service.py:load_performance_records_from_csv), replacing the dropped mock generator. Step 5/6 now produce real SCORED risk results and real narratives end to end, confirmed against the live API.

Configurable formula weights and thresholds:

Step 4's per-product-type metric weights and parity/position thresholds, and Step 5's competitive-threat threshold, exposure weights, and LOW/MEDIUM/HIGH cutoffs are now read from config/risk_scoring.yaml (loaded via src/market_pulse/config/formula_config.py) instead of hardcoded Python constants. Every default value reproduces the original reference behavior exactly (verified byte-for-byte against real captured run output) — nothing changes until someone edits the file. Every value in that file is commented with what raising/lowering it does. Do not treat this file's defaults as fixed truth the way reference/ is treated — recalibrating these values against real data is expected and encouraged; the reference/ formulas themselves (the math, not the tuned constants) remain the source of truth per section 10.

API/orchestration resilience fixes (src/market_pulse/api/schemas.py, src/market_pulse/api/routes.py, src/market_pulse/orchestration/pipeline.py, src/market_pulse/services/risk_analysis_service.py):

- A competitor may be submitted with only prepaid OR only postpaid data. The missing category is synthesized as an empty (but validly-shaped) envelope and treated as zero plans, not an error.
- The raw crawler payload's shape (not just "is it valid JSON") is validated at submission time, before a competitor_run is created. A malformed file — e.g. a flat list of plan objects instead of the wrapped {"master_plans": [...], "addon_plans": [...]} envelope — is rejected immediately with a clear 422 naming what was expected and what was found, instead of silently producing an empty "successful" analysis.
- If 100% of a competitor's plans fail LLM classification (e.g. the LLM endpoint is unreachable), that stage and the competitor run are marked FAILED with a clear message, instead of silently completing with an empty result. A genuinely empty category (0 plans submitted, 0 errors) is still correctly COMPLETED.
- The performance-data CSV loader validates its expected columns exist before reading any rows, and fails loudly (naming the missing columns) if the file's schema has drifted, instead of silently treating every value as missing.
- Omantel-reference preparation is guarded by a single process-wide lock, so two competitors submitted close together can no longer redundantly re-run the expensive Omantel preparation at the same time.

Known limitations (identified during the same review, not fixed — lower priority / accepted trade-offs):

- A competitor plan missing a stable plan_id in the raw crawler data degrades Step 4's matching accuracy (falls back to name-based matching) rather than failing loudly.
- No crash-recovery: if the API server process dies mid-competitor-run, that run is permanently stuck in PROCESSING with no automatic detection or resume.
- No identity normalization for competitor names (e.g. "Ooredoo" vs "ooredoo" submitted separately are treated as unrelated).
- Duplicate rows in the performance CSV for the same (plan, month) would skew the average rather than being detected or rejected.
- The Omantel-preparation lock is a single global lock (not per-run_id) — a deliberate simplicity trade-off that serializes unrelated runs' first-time Omantel preparation.

Business review report:

reports/market_pulse_business_report.html is a self-contained interactive HTML report for the business team (KPI overview, cross-competitor risk comparison, portfolio/matching breakdowns, a sortable and filterable prioritized action table with CSV export, and a portfolio-gaps table). Regenerate it after new competitor runs with: python scripts/generate_business_report.py