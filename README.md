# Market Pulse

Market Pulse is Omantel's competitive-analysis agent. Give it a competitor's telecom
plans (Ooredoo, Vodafone, ...), and it automatically:

1. **Classifies** each competitor plan (data, voice, combo, etc.)
2. **Matches** it to the closest comparable Omantel plan
3. **Compares** them metric-by-metric (price, data, voice, validity...) and scores who's ahead
4. **Scores the business risk** of that gap, using real Omantel subscriber/ARPU data
5. **Writes a plain-English narrative** explaining the gap for a product/campaign team

It's exposed as a run-oriented HTTP API (`Run → Competitor Run → Stage Results`) so
multiple competitors can be analyzed under the same review cycle, each independently
tracked and retryable.

---

## Project status

Steps 1–6 of the original reference implementation have been fully productionized,
independently verified against the reference notebooks, and tested end-to-end against
real data and a real LLM (see `docs/integration_test_report.md`). The full run-oriented
API, storage, and orchestration layer described in `docs/architecture.md` is built and
working.

---

## Quick start

### 1. Set up the environment

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

### 2. Configure your LLM endpoint

```bash
cp .env.example .env
```

Edit `.env` and set `OPENAI_API_KEY` (and `OPENAI_BASE_URL`/`OPENAI_MODEL` if you're
pointing at something other than OpenAI directly — a local Ollama instance, Groq's
OpenAI-compatible endpoint, etc.). See `.env.example` for every configurable setting
(storage location, CSV paths, logging, formula config).

### 3. Run the tests

```bash
./.venv/bin/python -m pytest tests -m "not slow" -q   # fast suite, LLM mocked
./.venv/bin/python -m pytest tests -m "slow" -q        # real LLM calls, needs a working endpoint
```

### 4. Start the API

```bash
./.venv/bin/uvicorn market_pulse.api.app:app --host 0.0.0.0 --port 8000
```

### 5. Run your first analysis

```bash
# Create a run
curl -X POST http://localhost:8000/runs

# Submit a competitor (use the run_id from above)
curl -X POST http://localhost:8000/runs/RUN-XXXX/competitors \
  -H "Content-Type: application/json" \
  -d '{
    "competitor": "ooredoo",
    "data_path": {
      "prepaid": "/full/path/to/prepaid_ooredoo.json",
      "postpaid": "/full/path/to/postpaid_ooredoo.json"
    }
  }'

# Poll status, then pull results per stage
curl http://localhost:8000/runs/RUN-XXXX/competitors/CR-YYYY
curl http://localhost:8000/runs/RUN-XXXX/competitors/CR-YYYY/results/risk_analysis
```

**For the full walkthrough** — every endpoint, real example responses, what each stage
actually means, and the current known limitations — see **[`docs/usage_guide.md`](docs/usage_guide.md)**.

---

## Generating the business review report

A self-contained, interactive HTML report (KPIs, cross-competitor risk comparison,
portfolio breakdowns, a sortable/filterable prioritized action table, CSV export) can
be generated from any completed run:

```bash
./.venv/bin/python scripts/generate_business_report.py
```

Open `reports/market_pulse_business_report.html` directly in a browser — no server
required.

---

## Tuning the risk/gap-analysis formulas

Step 4 (gap analysis) and Step 5 (risk scoring) weights and thresholds are configurable
in **[`config/risk_scoring.yaml`](config/risk_scoring.yaml)** — every value is commented
with what raising or lowering it actually does to the calculation. Defaults reproduce
the original reference implementation exactly; edit the file and restart the server to
apply changes.

---

## Project layout

```
src/market_pulse/
├── config/         # Settings, formula config (risk_scoring.yaml loader), logging
├── schemas/        # Pydantic domain models per step + run/competitor/stage models
├── services/       # Deterministic business logic for Steps 1-6
├── llm/            # All LLM calls, isolated per step (OpenAI-compatible client)
├── orchestration/  # Per-competitor pipeline, shared Omantel-reference prep
├── api/            # FastAPI routes (the run-oriented public API)
└── storage/        # File-based run/competitor-run/stage-result persistence

config/risk_scoring.yaml   # Configurable gap-analysis & risk-scoring weights/thresholds
data/                      # Real competitor + Omantel reference + performance data
docs/                      # Architecture spec, usage guide, integration test report
scripts/                   # Business report generator
reports/                   # Generated HTML business report
tests/                     # Unit + integration tests (fast, mocked-LLM by default)
```

- **`docs/architecture.md`** — the run-oriented API contract this was built against.
- **`docs/usage_guide.md`** — plain-English guide to every endpoint, with real example responses.
- **`docs/integration_test_report.md`** — real end-to-end test results (real data, real LLM, no mocking).
- **`CLAUDE.md`** — development instructions and the production-hardening changelog (section 17).

---

## Notes

- `reference/` (the original notebooks) is intentionally not version-controlled — it's
  the read-only source of truth kept locally, per `CLAUDE.md`.
- There is currently no API endpoint to *upload* performance data — it's read from
  `data/omantel/PRODUCT_PERFORMANCE.csv` (path configurable). See `CLAUDE.md` section 17
  for this and other known limitations.
