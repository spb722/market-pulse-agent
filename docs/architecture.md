# Market Pulse – Production Architecture

## 1. Purpose

This document defines how the existing Market Pulse reference implementation (`reference/step_1.py` through `reference/step_6.py`) must be converted into a production API application.

The objective is to productionize the existing logic, not redesign it.

The reference implementation remains the source of truth for:
- normalization logic
- plan classification logic
- matching logic
- similarity rules
- gap formulas
- weights
- commercial position logic
- threat / exposure / risk logic
- LLM prompts and narrative behavior

Do not change those business rules unless explicitly requested.

---

## 2. High-Level Processing Flow

The existing processing flow is:

```text
STEP 1
Competitor normalization and enrichment
        ↓
STEP 2
Omantel normalization and enrichment
        ↓
STEP 3
Competitor ↔ Omantel comparable-plan matching
        ↓
STEP 4
Competitive gap analysis
        ↓
STEP 5
Threat / business exposure / risk analysis
        ↓
STEP 6
LLM-generated business narratives
```

In production, these should become internal services/modules.

Do not expose them as six unrelated public APIs.

The public API should be organized around:
- Runs
- Competitor runs
- Status
- Stage results

---

## 3. Core Execution Model

### 3.1 Run

A `run_id` represents one complete Market Pulse analysis cycle.

Example:

```text
RUN-1001
```

One run can contain multiple competitors.

Example:

```text
RUN-1001
├── Ooredoo
├── Vodafone
├── Friendi
└── Renna
```

All competitors submitted for the same market-analysis cycle must use the same `run_id`.

---

### 3.2 Competitor Run

Each competitor submitted under a run receives its own `competitor_run_id`.

Example:

```text
RUN-1001
├── CR-001 → Ooredoo
├── CR-002 → Vodafone
└── CR-003 → Friendi
```

The `competitor_run_id` is required because:
- one competitor can fail without affecting the others
- one competitor can be retried independently
- the UI can track each competitor separately
- intermediate outputs can be stored independently
- debugging is easier

---

## 4. Multi-Competitor Processing

The preferred Version 1 approach is:

1. Create one run.
2. Prepare/load the Omantel reference once.
3. Submit competitors one by one using the same `run_id`.
4. Process each competitor independently.
5. Store every intermediate stage result.
6. Mark the overall run complete when all expected competitors are complete.

Example:

```text
Create RUN-1001
        ↓
Prepare Omantel reference
        ↓
POST Ooredoo
        ↓
Step 1 → Step 3 → Step 4 → Step 5 → Step 6
        ↓
POST Vodafone using RUN-1001
        ↓
Step 1 → Step 3 → Step 4 → Step 5 → Step 6
        ↓
POST Friendi using RUN-1001
        ↓
Step 1 → Step 3 → Step 4 → Step 5 → Step 6
        ↓
RUN-1001 COMPLETED
```

The caller may loop through competitors and call the API repeatedly using the same `run_id`.

The backend should not require all competitors to be submitted in one request.

A future batch endpoint may be added later, but it is not required for Version 1.

---

## 5. Important Step 2 Rule

Step 2 prepares the normalized/enriched Omantel source-of-truth catalogue.

It is shared reference data for competitor analysis.

Do not unnecessarily rerun Step 2 separately for every competitor if the same Omantel reference applies.

Preferred behavior:

```text
RUN-1001
        ↓
Omantel reference prepared once
        ↓
Ooredoo ─┐
Vodafone ├── use the same prepared Omantel reference
Friendi ─┘
```

The implementation may:
- prepare Step 2 once per run, or
- reuse a previously prepared valid Omantel reference version

The exact persistence mechanism may evolve, but the business behavior above must remain.

---

## 6. Public API Design

Use a run-oriented API.

### 6.1 Create Run

```http
POST /runs
```

Example response:

```json
{
  "run_id": "RUN-1001",
  "status": "CREATED"
}
```

---

### 6.2 Submit Competitor

```http
POST /runs/{run_id}/competitors
```

The request should support either:

#### Option A – path/location

```json
{
  "competitor": "ooredoo",
  "data_path": "/input/ooredoo.json"
}
```

#### Option B – inline JSON

```json
{
  "competitor": "ooredoo",
  "data": {
    "...": "competitor crawler output"
  }
}
```

At least one supported input source must be provided.

Do not require both.

Example response:

```json
{
  "run_id": "RUN-1001",
  "competitor_run_id": "CR-001",
  "competitor": "ooredoo",
  "status": "PROCESSING"
}
```

---

### 6.3 Get Run Status

```http
GET /runs/{run_id}
```

Example:

```json
{
  "run_id": "RUN-1001",
  "status": "PROCESSING",
  "competitors": [
    {
      "competitor_run_id": "CR-001",
      "competitor": "ooredoo",
      "status": "COMPLETED"
    },
    {
      "competitor_run_id": "CR-002",
      "competitor": "vodafone",
      "status": "PROCESSING"
    }
  ]
}
```

---

### 6.4 List Competitors in a Run

```http
GET /runs/{run_id}/competitors
```

Return all competitor runs belonging to the overall run.

---

### 6.5 Get Competitor Status

```http
GET /runs/{run_id}/competitors/{competitor_run_id}
```

Example:

```json
{
  "run_id": "RUN-1001",
  "competitor_run_id": "CR-001",
  "competitor": "ooredoo",
  "status": "PROCESSING",
  "stages": {
    "competitor_normalization": "COMPLETED",
    "plan_matching": "COMPLETED",
    "gap_analysis": "COMPLETED",
    "risk_analysis": "PROCESSING",
    "narrative_generation": "PENDING"
  }
}
```

---

### 6.6 Get Stage Result

```http
GET /runs/{run_id}/competitors/{competitor_run_id}/results/{stage}
```

Examples:

```text
/results/competitor_normalization
/results/plan_matching
/results/gap_analysis
/results/risk_analysis
/results/narrative_generation
```

This endpoint exists so the UI can show intermediate outputs as soon as they are available.

---

## 7. Internal Stage Mapping

Use production-friendly internal names.

Suggested mapping:

| Reference Step | Internal stage |
|---|---|
| Step 1 | `competitor_normalization` |
| Step 2 | `omantel_normalization` |
| Step 3 | `plan_matching` |
| Step 4 | `gap_analysis` |
| Step 5 | `risk_analysis` |
| Step 6 | `narrative_generation` |

Step 2 is shared and may be represented at the run/reference level rather than inside every competitor run.

---

## 8. Intermediate Result Storage

Every meaningful stage output must be stored.

Logical hierarchy:

```text
Run
└── Competitor Run
    └── Stage Results
```

Example logical structure:

```text
runs/
└── RUN-1001/
    ├── omantel/
    │   └── normalized.json
    │
    ├── ooredoo/
    │   ├── normalized.json
    │   ├── matches.json
    │   ├── gaps.json
    │   ├── risk.json
    │   └── narratives.json
    │
    └── vodafone/
        ├── normalized.json
        ├── matches.json
        ├── gaps.json
        ├── risk.json
        └── narratives.json
```

This is a logical model only.

The implementation may use:
- database storage
- object storage
- file storage

Do not tightly couple business logic to one storage technology.

Use a repository/storage abstraction where practical.

---

## 9. Suggested Data Records

### Run

Suggested minimum fields:

```text
run_id
status
created_at
started_at
completed_at
error
```

Optional:

```text
expected_competitor_count
completed_competitor_count
omantel_reference_id
```

---

### Competitor Run

Suggested minimum fields:

```text
competitor_run_id
run_id
competitor
status
input_type
input_location
created_at
started_at
completed_at
error
```

---

### Stage Result

Suggested minimum fields:

```text
run_id
competitor_run_id
stage
status
result_location or result_payload
started_at
completed_at
error
```

---

## 10. Status Values

Keep statuses simple.

### Run / Competitor Run

```text
CREATED
PROCESSING
COMPLETED
FAILED
PARTIAL
```

### Stage

```text
PENDING
PROCESSING
COMPLETED
FAILED
```

Do not silently convert failures into successful empty results.

---

## 11. Failure and Retry Behavior

A competitor failure must not automatically fail every competitor in the same overall run.

Example:

```text
RUN-1001

Ooredoo   → COMPLETED
Vodafone  → FAILED
Friendi   → COMPLETED

Run status → PARTIAL
```

A failed competitor should be retryable independently.

Do not rerun already completed competitors unless explicitly requested.

Do not overwrite successful historical outputs without preserving traceability.

---

## 12. Project Structure

Use this structure as the target unless a small adjustment is clearly necessary:

```text
market-pulse/
│
├── CLAUDE.md
│
├── reference/
│   ├── step_1.py
│   ├── step_2.py
│   ├── step_3.py
│   ├── step_4.py
│   ├── step_5.py
│   └── step_6.py
│
├── docs/
│   └── architecture.md
│
├── .claude/
│   └── agents/
│       ├── implementer.md
│       └── verifier.md
│
├── src/
│   └── market_pulse/
│       ├── api/
│       ├── schemas/
│       ├── services/
│       ├── orchestration/
│       ├── storage/
│       ├── llm/
│       └── config/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── pyproject.toml
├── .env.example
└── README.md
```

---

## 13. Module Responsibilities

### `api/`
Public FastAPI routes/controllers.

Responsibilities:
- validate API requests
- call orchestration/application services
- return API responses
- never contain business formulas

### `schemas/`
Pydantic request/response/domain models.

### `services/`
Productionized business logic corresponding to Steps 1–6.

Examples:

```text
competitor_normalization_service.py
omantel_normalization_service.py
plan_matching_service.py
gap_analysis_service.py
risk_analysis_service.py
narrative_service.py
```

### `orchestration/`
Controls stage order and run/competitor processing.

It should know:

```text
Step 1
→ Step 3
→ Step 4
→ Step 5
→ Step 6
```

for each competitor after the Omantel reference is available.

### `storage/`
Persistence for:
- runs
- competitor runs
- stage results
- reference/result locations

### `llm/`
Groq / GPT-OSS integration and LLM-specific adapters.

Do not spread LLM client initialization throughout business modules.

### `config/`
Environment/configuration loading.

Secrets must not be hard-coded.

---

## 14. Reference Implementation Rules

Everything under `reference/` is read-only.

Do not modify those files.

Before implementing a step:

1. Read the relevant reference step.
2. Identify its required inputs.
3. Identify its produced outputs.
4. Identify deterministic calculations.
5. Identify LLM calls.
6. Identify useful validations/tests.
7. Implement equivalent production behavior.
8. Verify compatibility with the next step.

Do not:
- change formulas
- change weights
- change thresholds
- change matching rules
- change risk logic
- invent new business classifications
- introduce embeddings or BM25
- combine base plans and add-ons into synthetic products
- replace deterministic code with LLM reasoning
- remove existing meaningful output fields without explicit approval

---

## 15. Notebook Code Handling

The reference files originate from Jupyter development.

Do not copy notebook-only behavior into production.

Remove/replace:
- `print()` debugging
- `display()`
- `df.head()` used only for inspection
- sample/manual verification cells
- exploratory code
- temporary local test datasets
- notebook-specific path assumptions

Preserve useful validations by converting them into automated tests.

Examples:
- score range validation
- required output fields
- selected match must belong to candidate set
- no invalid NaN JSON
- expected status/position values
- formula regression tests

---

## 16. Testing Strategy

For each step:

```text
Implement
    ↓
Unit tests
    ↓
Reference/regression comparison
    ↓
Verifier review
    ↓
PASS
    ↓
Next step
```

Do not proceed to the next step until the verifier returns PASS.

Where deterministic output exists, tests should compare against known expected values.

Where LLM output is involved:
- mock LLM calls in unit tests where practical
- validate schema and factual consistency
- do not require exact wording unless the reference requires it

---

## 17. UI / Analytics Requirement

The UI must be able to observe progress and retrieve intermediate results.

Therefore the backend must store and expose:
- run status
- competitor status
- per-stage status
- per-stage result

The UI should not need to rerun the pipeline just to display previously computed results.

Analytics/read endpoints must read stored results.

---

## 18. Version 1 Non-Goals

Do not introduce these unless explicitly requested:

- microservices
- Kafka/event streaming
- Celery/distributed queues
- vector databases
- embeddings
- BM25
- synthetic base-plan + add-on bundles
- automatic business-rule redesign
- unnecessary agent frameworks inside the API
- separate public API endpoint for every internal Python function

Keep Version 1 simple and modular.

---

## 19. Required Development Workflow

Implement Steps 1–6 sequentially.

For each step:

```text
Reference Step
      ↓
Implementer Agent
      ↓
Production Code
      ↓
Automated Tests
      ↓
Verifier Agent
      ↓
PASS?
  ├── NO → Implementer fixes → Verify again
  └── YES → Continue to next step
```

Do not implement multiple reference steps at once unless explicitly requested.

---

## 20. Definition of Done

A step is complete only when:

1. Production code exists under `src/`.
2. Relevant tests exist and pass.
3. Inputs/outputs are compatible with the pipeline.
4. Reference business behavior is preserved.
5. No reference file was modified.
6. The verifier returns `PASS`.

The full Market Pulse productionization is complete only when Steps 1–6 pass verification and the run-oriented API can process multiple competitors under the same `run_id`.
