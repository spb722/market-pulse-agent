# Market Pulse — How to Use This API (Plain-English Guide)

This is a walkthrough for whoever is calling this API (your orchestrator, a script, Postman, whatever) — what to call first, what you get back, and what each piece of the response actually means. Every example below is **real output** captured from an actual run against real Ooredoo/Omantel data (see `docs/integration_test_report.md` for the full test log).

---

## 1. What This Thing Actually Does

You give it one competitor's telecom plans (e.g. Ooredoo). It:

1. Reads and classifies each competitor plan (what kind of plan is it — data, voice, combo, etc.)
2. Finds the closest matching Omantel plan for each one
3. Compares them line-by-line (price, data, voice, validity...) and scores who's ahead
4. Works out how risky each gap is to Omantel's business, using real subscriber/ARPU numbers (see section 6 for where that data lives)
5. Writes a plain-English explanation of each gap, in business language

You get all five results back through the API — you don't have to wait for everything to finish to see the first pieces.

---

## 2. The Mental Model: Run → Competitor → Stages

```
A Run                          (one analysis cycle, e.g. "this month's competitor check")
 └── A Competitor Run          (one competitor inside that run, e.g. "Ooredoo")
      └── 5 Stage Results      (the 5 steps above, each stored separately)
```

- One **Run** can hold **multiple competitors** (Ooredoo, Vodafone, Friendi...) — you just keep submitting competitors to the same `run_id`.
- Each competitor gets processed **independently** — if one fails, the others keep going.
- Omantel's own product catalogue is only prepared **once per run** and reused for every competitor you submit to that run (so the 2nd, 3rd, etc. competitor submission is faster).

---

## 3. Before You Start

Start the server (from the repo root, with the project's virtualenv):

```bash
./.venv/bin/uvicorn market_pulse.api.app:app --host 0.0.0.0 --port 8000
```

Make sure `.env` is set up with your LLM endpoint (see `.env.example`) — this is what actually classifies plans and writes narratives. All the examples below used a local Ollama endpoint.

All requests below assume the server is at `http://localhost:8000` — swap in your actual host/port.

---

## 4. Step-by-Step: Your First Real Call

### Step 1 — Create a Run

```bash
curl -X POST http://localhost:8000/runs
```

**Response:**
```json
{"run_id": "RUN-4312FCB6", "status": "CREATED"}
```

Save `run_id` — every following call uses it.

### Step 2 — Submit a Competitor

You give it the competitor's raw prepaid + postpaid plan data — either as **file paths** the server can read, or as **inline JSON** in the request body itself.

```bash
curl -X POST http://localhost:8000/runs/RUN-4312FCB6/competitors \
  -H "Content-Type: application/json" \
  -d '{
    "competitor": "ooredoo",
    "data_path": {
      "prepaid": "/full/path/to/prepaid_ooredoo.json",
      "postpaid": "/full/path/to/postpaid_ooredoo.json"
    }
  }'
```

(Or use `"data": {"prepaid": {...}, "postpaid": {...}}` instead of `data_path` if you want to send the JSON inline rather than as file paths — you only need one of the two, not both.)

**Response (comes back immediately — processing happens in the background):**
```json
{
  "run_id": "RUN-4312FCB6",
  "competitor_run_id": "CR-305AA158",
  "competitor": "ooredoo",
  "status": "PROCESSING"
}
```

Save `competitor_run_id` too. **This call returns right away** — it doesn't make you wait 20+ minutes for the real analysis to finish. You poll for progress instead (next step).

### Step 3 — Check Progress

```bash
curl http://localhost:8000/runs/RUN-4312FCB6/competitors/CR-305AA158
```

**Response while it's working:**
```json
{
  "run_id": "RUN-4312FCB6",
  "competitor_run_id": "CR-305AA158",
  "competitor": "ooredoo",
  "status": "PROCESSING",
  "stages": {
    "competitor_normalization": "COMPLETED",
    "plan_matching": "PROCESSING",
    "gap_analysis": "PENDING",
    "risk_analysis": "PENDING",
    "narrative_generation": "PENDING"
  }
}
```

**Response once fully done:**
```json
{
  "...": "...",
  "status": "COMPLETED",
  "stages": {
    "competitor_normalization": "COMPLETED",
    "plan_matching": "COMPLETED",
    "gap_analysis": "COMPLETED",
    "risk_analysis": "COMPLETED",
    "narrative_generation": "COMPLETED"
  }
}
```

Just poll this every 30-60 seconds until `status` is `COMPLETED` (or `FAILED`). Note: **each stage's result is readable as soon as that individual stage finishes** — you don't have to wait for all 5 before looking at, say, the classification results.

### Step 4 — Pull the Actual Results, Stage by Stage

```bash
curl http://localhost:8000/runs/RUN-4312FCB6/competitors/CR-305AA158/results/competitor_normalization
curl http://localhost:8000/runs/RUN-4312FCB6/competitors/CR-305AA158/results/plan_matching
curl http://localhost:8000/runs/RUN-4312FCB6/competitors/CR-305AA158/results/gap_analysis
curl http://localhost:8000/runs/RUN-4312FCB6/competitors/CR-305AA158/results/risk_analysis
curl http://localhost:8000/runs/RUN-4312FCB6/competitors/CR-305AA158/results/narrative_generation
```

What each one actually gives you, in plain words, with a real example from an actual run:

---

#### `competitor_normalization` — "Here's what we understood about their plans"

Every competitor plan, tagged with what kind of plan it is (data-only, voice-heavy, combo, etc.), price bands, and flags like "has roaming" / "has unlimited data".

```json
{
  "enriched_plans": [ /* 37 plans, each with an llm_enrichment block like: */
    {
      "plan_role": "MASTER",
      "product_type": "COMBO",
      "market_segment": "CONSUMER",
      "primary_value_driver": "DATA",
      "benefit_tags": ["DATA_ROLLOVER", "ROAM_LIKE_HOME"],
      "classification_confidence": 0.95
    }
  ],
  "errors": []
}
```

`errors` is a list of plans that failed to classify (rare) — it's a list, not a single value, because one bad plan never blocks the other 36.

---

#### `plan_matching` — "Here's Omantel's closest equivalent to each of their plans"

```json
{
  "competitor_plan_name": "Hala+ OMR 26",
  "match_status": "MATCHED",
  "match_confidence": 0.616,
  "selected_match": {
    "omantel_plan_name": "Hayyak Plus 21",
    "similarity_score": 0.616
  },
  "selection_reason": "Highest overall similarity score and closest price/data/validity match."
}
```

`match_status` tells you what happened:
- `MATCHED` — found a genuinely comparable Omantel plan
- `NO_GOOD_MATCH` — some candidates existed, but none were actually comparable (e.g. real example: *"None of the candidates match the 90-day validity and 25GB data of the competitor plan."*)
- `NO_DIRECT_MATCH` — Omantel doesn't have anything in the same category (no candidates at all)

Only `MATCHED` plans move on to a full gap analysis.

---

#### `gap_analysis` — "Here's exactly where Omantel is ahead or behind, and by how much"

```json
{
  "gap_analysis_status": "ANALYZED",
  "weighted_position": {
    "commercial_position_score": -26.92,
    "overall_position": "COMPETITOR_ADVANTAGE"
  },
  "metric_gaps": {
    "price": {"competitor": 26.0, "omantel": 21.0, "gap_pct": -19.23, "position": "OMANTEL_ADVANTAGE"},
    "data": {"competitor": 60.0, "omantel": 40.0, "gap_pct": -33.33, "position": "COMPETITOR_ADVANTAGE"}
  }
}
```

`overall_position` is the one-line verdict: is Omantel ahead (`OMANTEL_ADVANTAGE`), behind (`COMPETITOR_ADVANTAGE`), or roughly even (`BALANCED`) on this plan overall. `metric_gaps` breaks that down field-by-field (price, data, voice, IDD, SMS, validity) so you can see exactly what's driving it.

---

#### `risk_analysis` — "Here's how much this gap actually threatens the business"

This now uses **real Omantel usage/revenue data** (`data/omantel/PRODUCT_PERFORMANCE.csv` — subscriber counts and ARPU per plan per month), so matched plans get a real score:

```json
{
  "risk_status": "SCORED",
  "months_used": 6,
  "avg_active_users_6m": 3151.67,
  "avg_product_arpu_6m": 21.484,
  "risk_score": 2.08,
  "risk_level": "LOW",
  "competitive_threat_score": 26.92,
  "business_exposure_score": 7.71,
  "risk_reasons": ["Competitor advantage in: DATA, VOICE"]
}
```

If a matched Omantel plan happens to have no performance data on file (not currently possible for these 178 products, but would be if a new plan appeared without a corresponding performance-data row), you'd instead see:

```json
{
  "risk_status": "REVIEW_REQUIRED",
  "reason": "No product performance data found for the matched Omantel plan."
}
```

The performance data is read from a configured file (`OMANTEL_PERFORMANCE_CSV_PATH`, defaulting to `data/omantel/PRODUCT_PERFORMANCE.csv`) — not submitted through the API. To refresh it, replace that file with updated numbers (same columns: `product_id, product_name, price, month, number_of_purchases, unique_customers, total_revenue, arpu`).

---

#### `narrative_generation` — "Here's the plain-English explanation a product manager can actually read"

Generated for every plan that made it to a `SCORED` risk result — which, with real performance data now wired in, is every plan that matched and analyzed cleanly:

```json
{
  "gap_summary": "Omantel's Hayyak Plus 21 is cheaper by 19% but lags in data (40 GB vs 60 GB) and voice (650 OMR vs unlimited), resulting in an overall competitor advantage.",
  "key_issue": "VOICE – competitor offers unlimited voice while Omantel charges 650 OMR for voice minutes.",
  "business_explanation": "Unlimited voice is a high-value feature for customers; Omantel's 650 OMR voice cost makes the plan less attractive despite the lower price. The data shortfall further weakens the offer. Commercially, this gap explains the 13.05 OMR revenue exposure and the negative commercial position score, indicating potential loss of market share if not addressed.",
  "narrative_source": "LLM_GENERATED"
}
```

`narrative_source` tells you whether this came from the real LLM (`LLM_GENERATED`) or a safe deterministic backup sentence if the LLM call failed (`DETERMINISTIC_FALLBACK`) — either way you always get *something* readable, never a blank field.

The `narrative_generation` result also includes two extra summaries for free:
- `no_match_report` — every competitor plan that didn't get a good Omantel match, so you can see portfolio gaps at a glance
- `executive_summary` — top-line counts: how many plans analyzed, how many matched, how many high/medium/low risk, etc.

---

## 5. Submitting a Second Competitor to the Same Run

Just repeat Step 2 with the same `run_id` and a different competitor name/data. Omantel's catalogue is already prepared from the first submission, so this one skips straight to classifying the new competitor's plans — no need to wait through Omantel prep again.

```bash
curl -X POST http://localhost:8000/runs/RUN-4312FCB6/competitors \
  -d '{"competitor": "vodafone", "data_path": {...}}'
```

`GET /runs/RUN-4312FCB6` now shows both competitors, each independently trackable:

```json
{
  "run_id": "RUN-4312FCB6",
  "status": "PARTIAL",
  "competitors": [
    {"competitor_run_id": "CR-305AA158", "competitor": "ooredoo", "status": "COMPLETED"},
    {"competitor_run_id": "CR-9F2B1A00", "competitor": "vodafone", "status": "FAILED"}
  ]
}
```

`status: "PARTIAL"` on the run means "some competitors finished, one failed" — the failure never took down the ones that succeeded, and you can find out exactly what went wrong for the failed one via its own `GET .../competitors/{competitor_run_id}` call (it carries an `error` field).

---

## 6. One Thing to Know Before You Rely on This in Production

**Real Omantel performance data (subscriber counts, ARPU) is now wired in** — `risk_analysis` and `narrative_generation` give you real, complete answers for every matched plan today, confirmed by a full real run (see `docs/integration_test_report.md` section 4).

The one thing still worth knowing: that data comes from a **file**, not an API call. It's read from `data/omantel/PRODUCT_PERFORMANCE.csv` (path configurable via `OMANTEL_PERFORMANCE_CSV_PATH`) fresh on every competitor's risk-analysis stage. There's no `POST` endpoint to submit/update it — to refresh it, you (or whoever owns that data) replace the file on disk. If you want a proper upload endpoint for this later, that's a natural next step, not something currently blocking real use.

---

## 7. Quick Reference

| # | Call | What it's for |
|---|---|---|
| 1 | `POST /runs` | Start a new analysis cycle. Do this once per cycle. |
| 2 | `POST /runs/{run_id}/competitors` | Submit one competitor's data. Repeat per competitor, same `run_id`. Returns immediately. |
| 3 | `GET /runs/{run_id}` | Overall run status + all its competitors at a glance. |
| 4 | `GET /runs/{run_id}/competitors` | Full list of competitor runs for this run. |
| 5 | `GET /runs/{run_id}/competitors/{competitor_run_id}` | One competitor's status, broken down by stage. Poll this. |
| 6 | `GET /runs/{run_id}/competitors/{competitor_run_id}/results/{stage}` | The actual data for one stage. `{stage}` is one of `competitor_normalization`, `plan_matching`, `gap_analysis`, `risk_analysis`, `narrative_generation`. |

**Status values you'll see:**
- Run / Competitor: `CREATED` → `PROCESSING` → `COMPLETED` / `FAILED` / `PARTIAL` (run-level only, means "mixed results")
- Stage: `PENDING` → `PROCESSING` → `COMPLETED` / `FAILED`

**Error responses:**
- `404` — the run/competitor you asked for doesn't exist
- `422` — your request was malformed (missing data, bad file path, unknown stage name)
- `409` — you asked for a stage's results before that stage finished (check the `status` field in the error body and poll again)

---

## 8. Test Cases Already Run (So You Know It Works)

Full detail in **`docs/integration_test_report.md`**. Summary:

| What was tested | How | Result |
|---|---|---|
| All 9 API error/validation scenarios (404s, 422s, 409, listing, etc.) | Real calls against a live server | **9/9 passed** |
| Full 6-stage pipeline, real data (37 Ooredoo plans vs. 145 real Omantel products), real LLM (no mocking) — run #1, before real performance data existed | End-to-end via the actual API | **Passed** — 0 unexpected errors, ~29 minutes real run time |
| Risk scoring formulas with hand-built realistic performance data | Real functions, real gap-analysis input | **Passed** — correct scores on 5 real matched pairs |
| Narrative generation with hand-built realistic performance data | Same 5 pairs, real LLM | **Passed** — 5/5 real, business-appropriate narratives, 0 fallbacks needed |
| A live bug found while testing (run status not updating on submission) | — | **Found and fixed**, verified, no regressions |
| **Full 6-stage pipeline, run #2 — real Omantel performance data (1,068 rows) wired in and used for real** | End-to-end via the actual API | **Passed** — 26/26 matched plans got real `SCORED` risk results and real LLM narratives, 0 unexpected errors, ~31 minutes real run time |

Everything above used **real production data files** (`data/competitors/ooredoo/...`, `data/omantel/...`, including your real performance CSV) and a **real local LLM** — nothing in the integration test report was mocked.
