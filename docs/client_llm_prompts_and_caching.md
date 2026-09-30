# Market Pulse: full LLM prompts and sample input payloads

This document is for reviewing what the application sends to the LLM. It contains the **complete system and human prompt templates** for all five LLM stages, followed by a representative input payload for each template variable.

The payloads below were reconstructed from one completed local run using the same project functions that prepare LLM requests. They are examples of actual plan and analysis data, not newly generated model answers. The JSON is pretty-printed here for reading; the application usually inserts it into the human message as a serialized JSON string. Prompt text is shown verbatim apart from surrounding blank lines.

Each call has a system message and a human message. The application replaces the `{...}` placeholder in the human template with the corresponding JSON payload shown beneath it. Output schemas are supplied separately in the model request.

## 1. Competitor plan classification

### System message

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

### Human message template

```text
Classify this telecom competitor plan:

{plan_json}
```

### Sample `plan_json` payload

```json
{
  "plan_name": "Hala+ OMR 26",
  "category": "prepaid",
  "price_omr": 26,
  "data_gb": 60,
  "social_pass_gb": 25,
  "entertainment_gb": null,
  "validity_days": 28,
  "contract_months": null,
  "voice_minutes": null,
  "flexi_minutes": null,
  "intl_minutes": null,
  "roaming_data_gb": null,
  "roaming_included": true,
  "unlimited_calls": true,
  "unlimited_sms": null,
  "bonus_data_gb": null,
  "is_promo": false,
  "extra_benefits": "Data Rollover on successful renewal; Roam Like Home on data and voice (FUP applies)",
  "operator": "ooredoo",
  "source_url": "https://shop.ooredoo.om/prepaid-plan-page/",
  "plan_id": "f6b8c29802a9",
  "type": "Master",
  "product_type": "COMBO",
  "validity_bucket": "MONTHLY",
  "price_band": "20_30",
  "market_segment_rule": "CONSUMER",
  "has_social_data": true,
  "has_bonus_data": false,
  "has_roaming": true,
  "has_idd": false,
  "has_entertainment": false,
  "data_gb_per_omr": 2.308
}
```

## 2. Omantel semantic enrichment

### System message

```text
You are a telecom product-normalization component
inside Omantel's Market Pulse Agent.

You are receiving an Omantel source-of-truth product.

Your role is ONLY semantic enrichment.

RULES:

1. Never modify numeric facts.
2. Never invent price, data, validity, minutes or SMS.
3. Never change plan_role.
4. Trust the supplied structured source fields.
5. Use product name and campaign messages only to understand semantics.
6. If evidence is insufficient, return UNKNOWN.
7. benefit_tags must only represent benefits clearly supported by the source.
8. Do not perform competitor comparison.
9. Do not calculate gap analysis.
10. Do not calculate risk.
11. Do not recommend actions.

Examples of normalized benefit tags:

DATA_ROLLOVER
SOCIAL_DATA
UNLIMITED_DATA
UNLIMITED_VOICE
IDD
ROAMING
FLEXI_MINUTES
ENTERTAINMENT
BONUS_DATA
SMS
VOICE

Do not invent a tag merely because it is common in telecom.
```

### Human message template

```text
Enrich this Omantel product:

{plan_json}
```

### Sample `plan_json` payload

```json
{
  "operator": "omantel",
  "category": "prepaid",
  "plan_name": "Marhaba World 13Minutes to SUDAN",
  "plan_id": "USG_1120530",
  "plan_role": "ADDON",
  "product_type": "IDD",
  "source_product_type": "IDD",
  "price_omr": 1.0,
  "validity_days": 7.0,
  "validity_bucket": "WEEKLY",
  "price_band": "0_5",
  "data_gb": 0.0,
  "social_pass_gb": null,
  "unlimited_data": false,
  "voice_minutes": 13.0,
  "flexi_minutes": null,
  "intl_minutes": 13.0,
  "unlimited_calls": false,
  "sms_count": 0.0,
  "unlimited_sms": false,
  "source_offer_type": "ATL",
  "source_status": "active",
  "message_english": "\"Unlock the world with Marhaba World bundle at just 1.0 RO! Enjoy 7 days of global connectivity with 13.0 min IDD calls. Dial *171# now! #Omantel\"",
  "message_arabic": "استمتع بعرض استثنائي! حزمة مرحبا العالم بسعر 1.0 ريال عماني لمدة 7 أيام. 13 دقيقة للمكالمات الدولية. اتصل عبر *171#. احجز العرض الآن من Omantel.",
  "quality_flags": [],
  "data_gb_per_omr": 0.0
}
```

## 3. Comparable-plan matching

### System message

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

### Human message template

```text
COMPETITOR PLAN:

{competitor}

OMANTEL CANDIDATES:

{candidates}
```

### Sample `competitor` payload

```json
{
  "plan_id": "f6b8c29802a9",
  "plan_name": "Hala+ OMR 26",
  "category": "prepaid",
  "plan_role": "MASTER",
  "product_type": "COMBO",
  "price_omr": 26.0,
  "data_gb": 60.0,
  "voice_minutes": null,
  "intl_minutes": null,
  "validity_days": 28.0,
  "unlimited_data": false,
  "unlimited_calls": true
}
```

### Sample `candidates` payload

```json
[
  {
    "omantel_plan_id": "USG_1171510",
    "omantel_plan_name": "Hayyak Plus 21",
    "category": "prepaid",
    "plan_role": "MASTER",
    "product_type": "COMBO",
    "price_omr": 21.0,
    "data_gb": 40.0,
    "voice_minutes": 650.0,
    "intl_minutes": null,
    "validity_days": 28.0,
    "unlimited_data": false,
    "unlimited_calls": false,
    "similarity_score": 0.616,
    "price_similarity": 0.8076923076923077,
    "data_similarity": 0.6666666666666667,
    "voice_similarity": 0.0,
    "idd_similarity": null,
    "sms_similarity": null,
    "validity_similarity": 1.0
  },
  {
    "omantel_plan_id": "USG_1170840",
    "omantel_plan_name": "Hayyak Plus 18",
    "category": "prepaid",
    "plan_role": "MASTER",
    "product_type": "COMBO",
    "price_omr": 18.0,
    "data_gb": 30.0,
    "voice_minutes": 600.0,
    "intl_minutes": null,
    "validity_days": 28.0,
    "unlimited_data": false,
    "unlimited_calls": false,
    "similarity_score": 0.5173,
    "price_similarity": 0.6923076923076923,
    "data_similarity": 0.5,
    "voice_similarity": 0.0,
    "idd_similarity": null,
    "sms_similarity": null,
    "validity_similarity": 1.0
  },
  {
    "omantel_plan_id": "USG_1171500",
    "omantel_plan_name": "Hayyak Plus 16",
    "category": "prepaid",
    "plan_role": "MASTER",
    "product_type": "COMBO",
    "price_omr": 16.0,
    "data_gb": 23.0,
    "voice_minutes": 450.0,
    "intl_minutes": null,
    "validity_days": 28.0,
    "unlimited_data": false,
    "unlimited_calls": false,
    "similarity_score": 0.4496,
    "price_similarity": 0.6153846153846154,
    "data_similarity": 0.3833333333333333,
    "voice_similarity": 0.0,
    "idd_similarity": null,
    "sms_similarity": null,
    "validity_similarity": 1.0
  }
]
```

## 4. Gap narrative

### System message

```text
You are the reporting explanation layer for
Omantel's Market Pulse Agent.

You receive factual, already-calculated results
comparing one Ooredoo plan with one matched
Omantel plan.

Your job is ONLY to explain the findings clearly
for a telecom product/campaign team.

STRICT RULES:

1. Use only the facts provided.
2. Do not recalculate scores.
3. Do not change risk levels.
4. Do not invent missing benefits.
5. Do not invent internal Omantel business reasons.
6. Do not claim why Omantel originally designed
   or priced a product in a certain way.
7. You may explain WHAT measurable factors drive
   the competitive gap.
8. Mention Omantel advantages as well as
   competitor advantages.
9. Explain why the issue matters using customer
   or revenue exposure when provided.
10. A separate addon does not mean the matched
    product has native parity.
11. Keep each field concise and business-friendly.

Definitions:

gap_summary:
One short overall comparison.

key_issue:
The main measurable competitive issue.

business_explanation:
A short explanation of what drives the gap and
why it matters commercially.
```

### Human message template

```text
FACTUAL ANALYSIS:

{facts}
```

### Sample `facts` payload

```json
{
  "competitor_plan": "Hala+ OMR 26",
  "omantel_plan": "Hayyak Plus 21",
  "product_type": "COMBO",
  "metric_gaps": {
    "price": {
      "competitor": 26.0,
      "omantel": 21.0,
      "gap_pct": -19.23,
      "position": "OMANTEL_ADVANTAGE"
    },
    "data": {
      "competitor": 60.0,
      "omantel": 40.0,
      "gap_pct": -33.33,
      "position": "COMPETITOR_ADVANTAGE"
    },
    "voice": {
      "competitor": "UNLIMITED",
      "omantel": 650.0,
      "gap_pct": null,
      "position": "COMPETITOR_ADVANTAGE"
    },
    "idd": {
      "competitor": null,
      "omantel": null,
      "gap_pct": null,
      "position": "NOT_SCORED"
    },
    "sms": {
      "competitor": null,
      "omantel": 0.0,
      "gap_pct": null,
      "position": "NOT_SCORED"
    },
    "validity": {
      "competitor": 28.0,
      "omantel": 28.0,
      "gap_pct": 0.0,
      "position": "PARITY"
    }
  },
  "competitor_advantages": [
    "DATA",
    "VOICE"
  ],
  "omantel_advantages": [
    "PRICE"
  ],
  "primary_attention_area": "VOICE",
  "capability_gaps": [],
  "commercial_position_score": -26.92,
  "commercial_position": "COMPETITOR_ADVANTAGE",
  "competitive_threat_score": 26.92,
  "customer_exposure_score": 4.15,
  "revenue_exposure_score": 13.05,
  "business_exposure_score": 7.71,
  "risk_score": 2.08,
  "risk_level": "LOW"
}
```

## 5. Executive portfolio advice

### System message

```text
You are the executive decision-support layer for Omantel's Market Pulse report.

You receive already-calculated comparisons between multiple competitor packs
and the Omantel plans they matched. One request covers one category and product
type and can contain several Omantel plans.

Return one recommendation for every omantel_plan_id in the input, exactly once.

STRICT RULES:
1. Use only the supplied facts. Never invent plans, competitors, features,
   customer behavior, internal constraints, financial outcomes, or numbers.
2. Do not recalculate or change similarity, gap, exposure, risk scores, or risk
   levels.
3. Treat low similarity as weak evidence. Prefer INVESTIGATE over a confident
   product change when comparability is weak or evidence is incomplete.
4. Explain material customer/revenue exposure when supplied. Do not imply that
   a positive but very small risk is urgent.
5. Preserve Omantel advantages as well as competitor advantages.
6. Suggested actions are options for business review, not guaranteed outcomes.
7. Keep suggested_action concise and business-friendly.
8. Use only these decisions: KEEP, MONITOR, ENHANCE, REPRICE, REPACKAGE,
   INVESTIGATE.
9. Do not mention a competitor or plan name unless it appears in the facts for
   that omantel_plan_id.
10. Do not predict or claim market-share loss, churn, retention, uptake,
    migration, revenue change, or customer behavior. Those outcomes are not in
    the supplied facts.
11. If a plan name appears to contain a quantity that conflicts with a
    structured metric_gaps value, trust metric_gaps and do not extract the
    quantity from the plan name.
12. Use a number only with its exact supplied field meaning. In particular,
    never describe one exposure score as another type of exposure.
13. Mention a percentage only when it is the exact supplied gap_pct, or an
    exact similarity_score converted to a percentage.
```

### Human message template

```text
RISKY SEGMENT FACTS:

{facts}
```

### Sample `facts` payload

```json
{
  "segment_key": "prepaid:COMBO",
  "category": "prepaid",
  "product_type": "COMBO",
  "plans": [
    {
      "omantel_plan_id": "USG_1171450",
      "omantel_plan": "Hayyak Plus 5",
      "headline_risk_score": 12.98,
      "headline_risk_level": "LOW",
      "evidence_confidence": "HIGH",
      "main_gaps": [
        "DATA",
        "VOICE"
      ],
      "comparisons": [
        {
          "competitor": "Ooredoo",
          "competitor_plan_id": "bb6a86695699",
          "competitor_plan": "Hala+ OMR 5 (4 weeks)",
          "similarity_score": 0.8562,
          "risk_score": 12.98,
          "risk_level": "LOW",
          "primary_attention_area": "VOICE",
          "competitor_advantages": "DATA, VOICE",
          "omantel_advantages": "",
          "capability_gaps": "",
          "customer_exposure_score": 100.0,
          "revenue_exposure_score": 62.38,
          "business_exposure_score": 84.95,
          "avg_active_users_6m": 75890.83,
          "avg_monthly_revenue_6m": 324077.47,
          "metric_gaps": {
            "price": {
              "competitor": 5.0,
              "omantel": 5.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            },
            "data": {
              "competitor": 4.0,
              "omantel": 3.5,
              "gap_pct": -12.5,
              "position": "COMPETITOR_ADVANTAGE"
            },
            "voice": {
              "competitor": 100.0,
              "omantel": 50.0,
              "gap_pct": -50.0,
              "position": "COMPETITOR_ADVANTAGE"
            },
            "idd": {
              "competitor": null,
              "omantel": null,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "sms": {
              "competitor": null,
              "omantel": 0.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "validity": {
              "competitor": 28.0,
              "omantel": 28.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            }
          }
        },
        {
          "competitor": "Ooredoo",
          "competitor_plan_id": "0a88dae29546",
          "competitor_plan": "Hala OMR 3.5",
          "similarity_score": 0.845,
          "risk_score": 0.0,
          "risk_level": "LOW",
          "primary_attention_area": "PRICE",
          "competitor_advantages": "PRICE",
          "omantel_advantages": "DATA",
          "capability_gaps": "",
          "customer_exposure_score": 100.0,
          "revenue_exposure_score": 62.38,
          "business_exposure_score": 84.95,
          "avg_active_users_6m": 75890.83,
          "avg_monthly_revenue_6m": 324077.47,
          "metric_gaps": {
            "price": {
              "competitor": 3.5,
              "omantel": 5.0,
              "gap_pct": 42.86,
              "position": "COMPETITOR_ADVANTAGE"
            },
            "data": {
              "competitor": 3.0,
              "omantel": 3.5,
              "gap_pct": 16.67,
              "position": "OMANTEL_ADVANTAGE"
            },
            "voice": {
              "competitor": 50.0,
              "omantel": 50.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            },
            "idd": {
              "competitor": null,
              "omantel": null,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "sms": {
              "competitor": null,
              "omantel": 0.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "validity": {
              "competitor": 28.0,
              "omantel": 28.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            }
          }
        }
      ]
    },
    {
      "omantel_plan_id": "USG_1171510",
      "omantel_plan": "Hayyak Plus 21",
      "headline_risk_score": 2.08,
      "headline_risk_level": "LOW",
      "evidence_confidence": "LOW",
      "main_gaps": [
        "DATA",
        "VOICE"
      ],
      "comparisons": [
        {
          "competitor": "Ooredoo",
          "competitor_plan_id": "f6b8c29802a9",
          "competitor_plan": "Hala+ OMR 26",
          "similarity_score": 0.616,
          "risk_score": 2.08,
          "risk_level": "LOW",
          "primary_attention_area": "VOICE",
          "competitor_advantages": "DATA, VOICE",
          "omantel_advantages": "PRICE",
          "capability_gaps": "",
          "customer_exposure_score": 4.15,
          "revenue_exposure_score": 13.05,
          "business_exposure_score": 7.71,
          "avg_active_users_6m": 3151.67,
          "avg_monthly_revenue_6m": 67783.13,
          "metric_gaps": {
            "price": {
              "competitor": 26.0,
              "omantel": 21.0,
              "gap_pct": -19.23,
              "position": "OMANTEL_ADVANTAGE"
            },
            "data": {
              "competitor": 60.0,
              "omantel": 40.0,
              "gap_pct": -33.33,
              "position": "COMPETITOR_ADVANTAGE"
            },
            "voice": {
              "competitor": "UNLIMITED",
              "omantel": 650.0,
              "gap_pct": null,
              "position": "COMPETITOR_ADVANTAGE"
            },
            "idd": {
              "competitor": null,
              "omantel": null,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "sms": {
              "competitor": null,
              "omantel": 0.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "validity": {
              "competitor": 28.0,
              "omantel": 28.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            }
          }
        },
        {
          "competitor": "Ooredoo",
          "competitor_plan_id": "a1afb562acd2",
          "competitor_plan": "Hala OMR 25",
          "similarity_score": 0.7842,
          "risk_score": 0.0,
          "risk_level": "LOW",
          "primary_attention_area": "DATA",
          "competitor_advantages": "DATA",
          "omantel_advantages": "PRICE",
          "capability_gaps": "",
          "customer_exposure_score": 4.15,
          "revenue_exposure_score": 13.05,
          "business_exposure_score": 7.71,
          "avg_active_users_6m": 3151.67,
          "avg_monthly_revenue_6m": 67783.13,
          "metric_gaps": {
            "price": {
              "competitor": 25.0,
              "omantel": 21.0,
              "gap_pct": -16.0,
              "position": "OMANTEL_ADVANTAGE"
            },
            "data": {
              "competitor": 60.0,
              "omantel": 40.0,
              "gap_pct": -33.33,
              "position": "COMPETITOR_ADVANTAGE"
            },
            "voice": {
              "competitor": null,
              "omantel": 650.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "idd": {
              "competitor": null,
              "omantel": null,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "sms": {
              "competitor": null,
              "omantel": 0.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "validity": {
              "competitor": 28.0,
              "omantel": 28.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            }
          }
        }
      ]
    },
    {
      "omantel_plan_id": "USG_1170840",
      "omantel_plan": "Hayyak Plus 18",
      "headline_risk_score": 0.03,
      "headline_risk_level": "LOW",
      "evidence_confidence": "MEDIUM",
      "main_gaps": [
        "ROAMING",
        "VOICE"
      ],
      "comparisons": [
        {
          "competitor": "Ooredoo",
          "competitor_plan_id": "bc8f58ce6ab9",
          "competitor_plan": "Hala+ OMR 19",
          "similarity_score": 0.7816,
          "risk_score": 0.03,
          "risk_level": "LOW",
          "primary_attention_area": "VOICE",
          "competitor_advantages": "VOICE",
          "omantel_advantages": "PRICE",
          "capability_gaps": "ROAMING (missing natively; separate addon exists); SOCIAL_DATA (missing natively; no addon found)",
          "customer_exposure_score": 0.09,
          "revenue_exposure_score": 0.24,
          "business_exposure_score": 0.15,
          "avg_active_users_6m": 67.67,
          "avg_monthly_revenue_6m": 1243.29,
          "metric_gaps": {
            "price": {
              "competitor": 19.0,
              "omantel": 18.0,
              "gap_pct": -5.26,
              "position": "OMANTEL_ADVANTAGE"
            },
            "data": {
              "competitor": 30.0,
              "omantel": 30.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            },
            "voice": {
              "competitor": "UNLIMITED",
              "omantel": 600.0,
              "gap_pct": null,
              "position": "COMPETITOR_ADVANTAGE"
            },
            "idd": {
              "competitor": null,
              "omantel": null,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "sms": {
              "competitor": null,
              "omantel": 0.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "validity": {
              "competitor": 28.0,
              "omantel": 28.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            }
          }
        },
        {
          "competitor": "Ooredoo",
          "competitor_plan_id": "40a6f1716cb9",
          "competitor_plan": "Hala OMR 18",
          "similarity_score": 1.0,
          "risk_score": 0.0,
          "risk_level": "LOW",
          "primary_attention_area": "BALANCED",
          "competitor_advantages": "",
          "omantel_advantages": "",
          "capability_gaps": "",
          "customer_exposure_score": 0.09,
          "revenue_exposure_score": 0.24,
          "business_exposure_score": 0.15,
          "avg_active_users_6m": 67.67,
          "avg_monthly_revenue_6m": 1243.29,
          "metric_gaps": {
            "price": {
              "competitor": 18.0,
              "omantel": 18.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            },
            "data": {
              "competitor": 30.0,
              "omantel": 30.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            },
            "voice": {
              "competitor": null,
              "omantel": 600.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "idd": {
              "competitor": null,
              "omantel": null,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "sms": {
              "competitor": null,
              "omantel": 0.0,
              "gap_pct": null,
              "position": "NOT_SCORED"
            },
            "validity": {
              "competitor": 28.0,
              "omantel": 28.0,
              "gap_pct": 0.0,
              "position": "PARITY"
            }
          }
        }
      ]
    }
  ]
}
```
