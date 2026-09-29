# Market Pulse: sample LLM prompts, payloads, and caching

This document describes the LLM requests made by the current Market Pulse application. The examples are **synthetic** and shortened for readability; they contain no credentials, internal endpoint addresses, or customer data. Production requests contain the complete normalized plan or calculated facts. The prompt wording shown below reflects the current code, including the explicit competitor-classification output instructions.

## What repeats during a run?

The **system prompt for a stage is repeated** for each request at that stage. The plan or facts inserted into its human message change. The response cache stores a **validated answer for an exact request**, not a reusable copy of the system prompt alone.

| LLM stage | When it runs | Logical requests before cache hits | Output schema |
| --- | --- | --- | --- |
| Competitor classification | When a competitor is submitted | One per normalized competitor plan | `PlanEnrichment` |
| Omantel semantic enrichment | Once when the shared Omantel reference is first prepared for a run | One per eligible catalogue plan; later competitors in the same run reuse the prepared reference | `OmantelSemanticEnrichment` |
| Comparable-plan matching | After successful competitor classification | At most one per classified competitor plan; skipped when there are no candidates or the best structured similarity is below `0.40` | `MatchDecision` |
| Gap narrative | After gap and risk analysis | At most one per record with `gap_analysis_status=ANALYZED` and `risk_status=SCORED` | `GapNarrative` |
| Portfolio advice | During business-report generation | One per distinct risky `category + product_type` segment, covering all affected Omantel plans in that segment | `PortfolioSegmentAdvice` |

For example, the supplied Ooredoo run contained **37 plans**, so its classification prompt was sent **37 times**, each time with a different plan. All 37 responses failed schema validation. The cache log showed **37 misses and no stores**, and that run stopped before the later LLM stages. As a separate snapshot, the current Omantel CSVs normalize to **145 plans** (99 prepaid, 46 postpaid); a fresh shared reference can therefore require 145 Omantel enrichment requests. These counts change when the source data changes.

For a completed run, the number of logical LLM requests is:

```text
competitor plans
+ Omantel catalogue plans (once per run, if the reference is not already prepared)
+ competitor plans eligible for LLM matching
+ analyzed and scored comparison records
+ distinct risky portfolio segments when a business report is generated
```

Each of those requests can be a **cache hit** instead of a new provider call. The Omantel enrichment implementation batches misses with a maximum concurrency of five; the batch still represents one logical request per plan.

## Sample API submission payload

A client can submit competitor data inline to `POST /runs/{run_id}/competitors`. This synthetic request contains one prepaid plan; the real crawler envelopes may contain many plans and additional fields.

```json
{
  "competitor": "sampletel",
  "data": {
    "prepaid": [
      {
        "master_plans": [
          {
            "plan_id": "COMP-001",
            "plan_name": "Sample Prepaid 5",
            "operator": "sampletel",
            "category": "prepaid",
            "price_omr": 5,
            "data_gb": 10,
            "voice_minutes": 100,
            "validity_days": 28
          }
        ],
        "addon_plans": []
      }
    ]
  }
}
```

## Example 1: competitor plan classification

**System message** (exact prompt template):

```text
You are a telecom product classification component
inside Omantel's Market Pulse Agent.

Your task is ONLY to classify and enrich competitor plans.

IMPORTANT RULES:

1. Never modify factual numeric values.
2. Never invent benefits.
3. Use only the information in the supplied plan JSON.
4. If information is insufficient, classify it as UNKNOWN.
5. A primary PREPAID tariff/bundle is normally MASTER.
6. A primary POSTPAID subscription is normally BASE_PLAN.
7. A product purchased on top of another plan is ADDON.
8. COMBO means it meaningfully combines multiple services,
   for example data + voice.
9. DATA means the principal commercial product is data-only.
10. BUSINESS should only be used when there is evidence that
    the product targets business customers.
11. PROMO should only be used where promotional evidence exists.
12. classification_confidence must represent how certain you are.

Do not perform competitor gap analysis.
Do not calculate risk.
Do not recommend Omantel actions.

OUTPUT FORMAT:

Return exactly one JSON object with these eight fields at the top level:

- plan_role: MASTER, BASE_PLAN, ADDON, or UNKNOWN
- product_type: COMBO, DATA, VOICE, IDD, ROAMING, SMS, or OTHER
- market_segment: CONSUMER, BUSINESS, or UNKNOWN
- primary_value_driver: DATA, VOICE, IDD, ROAMING, SOCIAL,
  ENTERTAINMENT, BALANCED, or OTHER
- promo_status: STANDARD, PROMO, or UNKNOWN
- benefit_tags: a list of short strings
- classification_confidence: a number from 0 to 1
- rationale: one short sentence

Use these exact field names. Do not nest them inside "classification"
or "enrichment". Do not repeat the input plan, and do not add fields
such as plan_id, plan_name, or operator.

Return the JSON object only, with no Markdown fence or other text.
```

**Human message template:**

```text
Classify this telecom competitor plan:

{plan_json}
```

**Illustrative rendered plan payload** inserted for `{plan_json}`:

```json
{
  "plan_id": "COMP-001",
  "plan_name": "Sample Prepaid 5",
  "operator": "sampletel",
  "category": "prepaid",
  "price_omr": 5,
  "data_gb": 10,
  "voice_minutes": 100,
  "validity_days": 28,
  "validity_bucket": "MONTHLY",
  "price_band": "0_5",
  "market_segment_rule": "CONSUMER",
  "has_social_data": false,
  "has_bonus_data": false,
  "has_roaming": false,
  "has_idd": false,
  "has_entertainment": false,
  "data_gb_per_omr": 2.0
}
```

**Illustrative valid response:**

```json
{
  "plan_role": "MASTER",
  "product_type": "COMBO",
  "market_segment": "CONSUMER",
  "primary_value_driver": "BALANCED",
  "promo_status": "STANDARD",
  "benefit_tags": ["DATA", "VOICE"],
  "classification_confidence": 0.9,
  "rationale": "Primary prepaid bundle with data and voice allowances."
}
```

The call also requests a strict JSON schema through `response_format`. The application then extracts one JSON object and validates it against `PlanEnrichment`. A provider response that uses different field names or omits required fields fails validation and is **not cached**. Local validation remains necessary because an OpenAI-compatible endpoint may return an answer that does not follow the requested schema.

## Example 2: comparable-plan matching

**System message** (exact prompt template):

```text
You are the comparable-plan matching component
inside Omantel's Market Pulse Agent.

You receive:

1. One competitor telecom plan.
2. Up to three Omantel candidate plans.

The candidates have already passed strict checks for:

- prepaid/postpaid
- plan role
- product type

They have also been ranked using structured
commercial similarity.

Your task is ONLY to select the most commercially
comparable Omantel candidate.

IMPORTANT RULES:

1. Select ONLY from the candidate IDs supplied.
2. Never create a new product.
3. Never combine a base plan with an add-on.
4. Ignore market segment.
5. Ignore campaign strategy.
6. Do not calculate competitive gaps.
7. Do not calculate risk.
8. Similarity does NOT mean better or worse.
9. Focus on price, data, voice/IDD and validity.
10. If all supplied candidates are clearly poor
    comparisons, return NO_GOOD_MATCH and
    selected_plan_id = null.
11. Keep the reason short.
```

**Human message template:**

```text
COMPETITOR PLAN:

{competitor}

OMANTEL CANDIDATES:

{candidates}
```

**Illustrative values inserted into the human message:**

```json
{
  "competitor": {
    "plan_id": "COMP-001",
    "plan_name": "Sample Prepaid 5",
    "category": "prepaid",
    "plan_role": "MASTER",
    "product_type": "COMBO",
    "price_omr": 5,
    "data_gb": 10,
    "voice_minutes": 100,
    "intl_minutes": 0,
    "validity_days": 28,
    "unlimited_data": false,
    "unlimited_calls": false
  },
  "candidates": [
    {
      "omantel_plan_id": "OM-101",
      "omantel_plan_name": "Sample Omantel 5",
      "category": "prepaid",
      "plan_role": "MASTER",
      "product_type": "COMBO",
      "price_omr": 5,
      "data_gb": 8,
      "voice_minutes": 120,
      "similarity_score": 0.88
    }
  ]
}
```

The `competitor` object and `candidates` array above are **two separate JSON strings** inserted under the two headings; the wrapper only groups them for this example. The real candidates can include more comparison fields. One valid response has this shape:

```json
{
  "selected_plan_id": "OM-101",
  "match_status": "MATCHED",
  "match_confidence": 0.88,
  "reason": "Closest supplied option for price, data, voice, and validity."
}
```

## Example 3: executive portfolio advice

This request is **per risky segment**, not per plan. Its system prompt instructs the model to use only supplied facts, preserve calculated scores and risk levels, avoid invented customer or financial outcomes, and return one recommendation for each supplied Omantel plan ID. The permitted decisions are `KEEP`, `MONITOR`, `ENHANCE`, `REPRICE`, `REPACKAGE`, and `INVESTIGATE`.

**Human message template:**

```text
RISKY SEGMENT FACTS:

{facts}
```

**Illustrative segment facts** inserted for `{facts}` (production facts include further comparison and exposure fields):

```json
{
  "segment_key": "prepaid:COMBO",
  "category": "prepaid",
  "product_type": "COMBO",
  "plans": [
    {
      "omantel_plan_id": "OM-101",
      "omantel_plan": "Sample Omantel 5",
      "headline_risk_score": 28,
      "headline_risk_level": "HIGH",
      "evidence_confidence": "HIGH",
      "main_gaps": ["DATA"],
      "comparisons": [
        {
          "competitor": "sampletel",
          "competitor_plan_id": "COMP-001",
          "competitor_plan": "Sample Prepaid 5",
          "similarity_score": 0.88,
          "risk_score": 28,
          "risk_level": "HIGH"
        }
      ]
    }
  ]
}
```

**Illustrative valid response:**

```json
{
  "segment_summary": "One comparable prepaid bundle has a measured data gap.",
  "recommendations": [
    {
      "omantel_plan_id": "OM-101",
      "decision": "ENHANCE",
      "suggested_action": "Review the data allowance against the compared offer."
    }
  ]
}
```

## The other two LLM requests

| Stage | Human message template | Request payload | Required response fields |
| --- | --- | --- | --- |
| Omantel semantic enrichment | `Enrich this Omantel product:\n\n{plan_json}` | One normalized Omantel catalogue row, serialized as JSON | `semantic_product_type`, `market_segment`, `primary_value_driver`, `benefit_tags`, `classification_confidence`, `rationale` |
| Gap narrative | `FACTUAL ANALYSIS:\n\n{facts}` | Calculated competitor/Omantel metric gaps, advantages, exposure, and risk for one eligible comparison, serialized as JSON | `gap_summary`, `key_issue`, `business_explanation` |

The Omantel prompt requires the model to preserve numeric facts and plan role, and to use benefit tags supported by the source. The narrative prompt requires an explanation of **already calculated** results; it must not recalculate scores or change risk levels. A failed narrative call uses a deterministic text fallback, which is not stored as an LLM response.

## Which responses are cached?

All five stages use the same **exact Redis response cache**, when `LLM_CACHE_ENABLED=true` and Redis is available. Only a response that satisfies its stage's Pydantic output schema is stored. Failures, malformed responses, and deterministic fallbacks are not stored.

All five requests ask the provider for `json_schema` output. Competitor classification additionally sets `strict=true` and validates the raw response locally; the other four use their Pydantic schemas through LangChain without an explicit strict setting.

The cache key includes the stage, exact request payload, configured model, temperature, base URL, prompt version, and output schema. It **does not include a run ID**, so identical requests can be reused in later runs. The prompt text itself is not directly included in the key: when prompt wording changes, its code-level prompt version must be updated. The competitor-classification prompt is currently `competitor-classification-v2`; its previous `v1` answers will not be reused under `v2`.

| Stage | Cache key's prompt version | Base lifetime in `.env.example` |
| --- | --- | --- |
| Competitor classification | `competitor-classification-v2` | 180 days |
| Omantel enrichment | `omantel-semantic-enrichment-v1` | 365 days |
| Plan matching | `plan-matching-v1` | 180 days |
| Gap narrative | `gap-narrative-v1` | 90 days |
| Portfolio advice | `portfolio-advice-v3` | 90 days |

The example configuration applies **10% lifetime jitter**, so actual expiry varies around those base values. Code defaults leave caching disabled until it is enabled in configuration; `.env.example` enables it. With `LLM_CACHE_FAIL_OPEN=true`, Redis errors allow the LLM call to proceed without a cache hit.

**Simple repeat example:** If the same 37 competitor plans are submitted again with unchanged normalized payloads, model settings, schema, prompt version, and an unexpired cache entry for each, classification has **37 logical requests, 37 cache hits, and zero new classification provider calls**. If one plan changes, that plan's exact request gets a new key and needs a new call. A previously failed answer cannot be a hit because it was never stored.

## How to inspect actual counts

- Server logs contain `LLM cache activity` with per-stage `hit`, `miss`, `store`, or `bypass` counts around competitor processing. These are deltas of process-wide counters, so overlapping competitor runs can make a delta less precise.
- When Langfuse is enabled, `scripts/langfuse_run_usage.py RUN-...` reports logical requests, cache hits, provider calls, and token usage for a run. Failed generations can be missing from these usage totals, so check the server warnings and cache activity as well.

## Implementation references

- Prompts and output schemas: `src/market_pulse/llm/` and `src/market_pulse/schemas/`.
- Cache key, validation, and expiry: `src/market_pulse/llm/cache.py`.
- Stage eligibility and run behavior: `src/market_pulse/orchestration/pipeline.py`, `src/market_pulse/services/plan_matching_service.py`, `src/market_pulse/services/narrative_service.py`, and `src/market_pulse/services/portfolio_analysis_service.py`.
- Example configuration: `.env.example`.
