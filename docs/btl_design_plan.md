# BTL Support – Design Plan

**Status:** Proposed. Needs approval before any code changes.
**Date:** 2026-10-07
**Branch at time of writing:** `langfuse-run-grouping`

## Related documents

| Document | Purpose |
|---|---|
| `docs/vodafone_btl_data_issues.md` | Issues found in the raw Vodafone BTL extraction |
| `docs/btl_json_format.md` | Required JSON format for competitor BTL files |
| `docs/vodafone_btl_fix_log.md` | Fixes applied to produce the Vodafone `_btl_v2.json` files |
| `docs/omantel_btl_build_log.md` | How the Omantel BTL catalogue and usage CSVs were built |

---

## 1. Goal

Market Pulse currently compares competitor **ATL** (public) offers with Omantel **ATL** offers. This change adds **BTL** (targeted) offers.

```text
RUN-1001  (one run ID per market cycle — ATL and BTL together)
├── CR-001  vodafone  offer_scope=ATL  → compared with Omantel ATL   (unchanged)
├── CR-002  vodafone  offer_scope=BTL  → compared with Omantel BTL   (new)
├── CR-003  ooredoo   offer_scope=ATL  → compared with Omantel ATL
└── ...
```

The business rules from `reference/` stay the same: formulas, weights, thresholds, the matching rules and the LLM responsibilities. The only differences for BTL are which Omantel products it is compared with (decision D2) and how many months of usage it uses (decision D7).

---

## 2. Decisions (agreed with the business owner)

| # | Decision |
|---|---|
| D1 | ATL and BTL for the same cycle use **one run ID**. There are no separate runs. |
| D2 | Competitor BTL offers are compared only with **Omantel BTL** products. |
| D3 | If a competitor BTL main plan has no Omantel BTL main plan to compare with, the result is "**Omantel has no targeted plan here**". It stays `NO_DIRECT_MATCH` and the matching rule is not relaxed. |
| D4 | The API takes an optional `offer_scope` field: `"BTL"`, otherwise **`ATL` by default**. Existing callers and stored runs keep working. |
| D5 | The API **rejects** BTL files that don't follow `docs/btl_json_format.md`. |
| D6 | Every result record has an **`offer_scope` column** (and the scope of the Omantel product). How the HTML presents ATL and BTL can be decided later; the data will already be there. |
| D7 | BTL risk uses a **3-month average** (2026-07 to 2026-09). ATL stays at **6 months**. Both values become settings so they can be changed later. |
| D8 | A report can be generated with **only ATL, only BTL, or both**. |
| D9 | Omantel BTL products: only products **with subscription data** are used. Test, complaint and training products are removed. |
| D10 | Omantel BTL **free offers (price 0) are listed separately** and not scored. |
| D11 | Mapping from Omantel BTL `PRODUCT_GROUP_NAME` to product type: Data → DATA, Combo/Hybrid → COMBO, Voice → VOICE, IDD/ILD → IDD, Social Media/Youtube → DATA. All other groups are excluded. |
| D12 | Omantel BTL role: **COMBO is a main plan**; every other type is an add-on. |
| D13 | Postpaid BTL: there is no usable Omantel postpaid BTL product yet, and this is **accepted for now**. Every competitor postpaid BTL plan shows "Omantel has no targeted offer here". |
| D14 | Portfolio advice: how to show ATL and BTL is decided later. For now the data carries `offer_scope`. |
| D15 | Risk scale: the same LOW/MEDIUM/HIGH cutoffs for ATL and BTL. Because BTL usage is a separate file (D7), BTL exposure is measured against the **largest BTL product** (`NBE_Prepaid_RO4_10GB_150MIN_28D`, about 36,317 average customers over Jul–Sep) rather than the largest ATL product (about 75,891). This is the default because no change was requested. |

---

## 3. Input data (already prepared — no code needed)

### 3.1 Competitor BTL (Vodafone)

| File | Contents |
|---|---|
| `data/competitors/vodafone/prepaid_vodafone_btl_v2.json` | 34 plans (15 main plans, 19 add-ons) plus 10 `recharge_offers` |
| `data/competitors/vodafone/postpaid_vodafone_btl_v2.json` | 21 plans (2 main plans, 19 add-ons) |

Both are built by `scripts/fix_vodafone_btl.py`, follow `docs/btl_json_format.md`, and pass the current API payload check.

### 3.2 Omantel BTL

Built by `scripts/build_omantel_btl.py`. The raw exports are not modified.

| File | Contents |
|---|---|
| `data/omantel/btl/PREPAID_BTL_PRODUCT_CATALOG.csv` | **49 paid prepaid BTL products**: 32 COMBO main plans, 9 IDD add-ons, 8 DATA add-ons. Same column layout as `PREPAID_PRODUCT_CATALOG.csv`. |
| `data/omantel/btl/PRODUCT_PERFORMANCE_BTL.csv` | Their usage for 2026-07, 2026-08 and 2026-09: 141 rows, same columns as `PRODUCT_PERFORMANCE.csv`. 46 of the 49 have all 3 months. |
| `data/omantel/btl/BTL_FREE_OFFERS.csv` | 81 free BTL products (listed, not scored) |
| `data/omantel/btl/BTL_FREE_OFFERS_PERFORMANCE.csv` | Their usage, Jul–Sep: 203 rows |

The existing Step 2 code and the Step 5 usage loader were both run against these files. Both read them without errors, and every Omantel row normalises to the expected role and type.

**Known false positive.** Step 2 flags 3 products as `NAME_DATA_MISMATCH` (`NBO_VF_RO1_1Dot5GB_15MIN_28D`, `NBO_RO1_1Dot5GB_25MIN_28D`, `NBE_PREPAID_RO1_1Dot5GB_15MIN_28D`). Its name parser reads "1Dot5GB" as 5 GB. The data itself is correct (1.5 GB), and the flag is information only.

### 3.3 What to expect from this data

| Competitor BTL plan | Omantel BTL candidates | Expected result |
|---|---|---|
| Prepaid COMBO main plans (13) | 32 COMBO main plans | **Matched and scored** |
| Prepaid DATA add-ons (15) | 8 DATA add-ons | Matched and scored |
| Prepaid IDD (6) | 9 IDD add-ons (Pakistan, India, Bangladesh) | Add-ons can match. The 1 IDD *main plan* (Egypt) → `NO_DIRECT_MATCH` |
| All postpaid (21) | **0** | `NO_DIRECT_MATCH`: "Omantel has no targeted offer here" (D13) |

---

## 4. Phased implementation plan

Each phase follows the CLAUDE.md workflow: implementer agent → tests → verifier agent → **PASS** → next phase.

**The baseline rule for every phase:** an ATL-only run must give exactly the same output as today (a regression test checks this against stored runs).

### Phase 1 — API, data model, validation

**Files:** `api/schemas.py`, `api/routes.py`, `schemas/runs.py`

- `CompetitorSubmitRequest.offer_scope: Literal["ATL","BTL"] = "ATL"`.
- `CompetitorRun.offer_scope`, defaulting to `"ATL"` when reading old `competitor_run.json` files that don't have the field.
- Every `GET` response includes `offer_scope`. `GET /runs/{id}/competitors` gets an optional `?offer_scope=` filter.
- **BTL file validation** (D5), returning 422 with a clear message:

  | Check | Rejects when |
  |---|---|
  | Envelope | The shape is wrong (same check as today) |
  | Scope agreement | The file's root `offer_scope` doesn't match the request |
  | `plan_id` | Missing, or duplicated within the competitor's prepaid + postpaid plans |
  | `product_type` | Not one of COMBO, DATA, VOICE, IDD, ROAMING, SMS, OTHER |

  These only produce a warning (kept in the record, never rejected): `validity_days` is null, `data_quality_flags` is present.
- ATL validation is **unchanged**.

### Phase 2 — Omantel reference per scope

**Files:** `services/omantel_normalization_service.py`, `orchestration/pipeline.py`, `storage/file_repository.py`, `config/settings.py`

- New settings:
  - `omantel_btl_prepaid_csv_path = data/omantel/btl/PREPAID_BTL_PRODUCT_CATALOG.csv`
  - `omantel_btl_postpaid_csv_path = None`, meaning no postpaid BTL catalogue yet, so the postpaid BTL reference is empty
  - `omantel_btl_performance_csv_path = data/omantel/btl/PRODUCT_PERFORMANCE_BTL.csv`
- Add a `filter_prepaid_btl` function: `offer_type == "BTL"` and `product_status == "active"`. The existing ATL filters stay as they are.
- Normalised **BTL** plans get `"offer_scope": "BTL"`. ATL plans get **no** new field (a plan without `offer_scope` means ATL), so ATL stored output and LLM request payloads — and therefore ATL LLM cache keys — stay byte-identical.
- Storage: `runs/<RUN>/omantel/stage_result.json` stays as the **ATL** reference, so existing runs need no migration. The BTL reference goes in `runs/<RUN>/omantel/BTL/stage_result.json`.
- `Run` gets `omantel_reference_status_btl`, alongside the existing ATL status.
- `ensure_omantel_reference(run_id, offer_scope)` reuses the stored reference for its own scope. It keeps the global lock.

### Phase 3 — Pipeline: scope through every stage

**Files:** `orchestration/pipeline.py`, `services/plan_matching_service.py`, `gap_analysis_service.py`, `risk_analysis_service.py`, `narrative_service.py`, `llm/narrative_generator.py`, `llm/langfuse_metrics.py`

- `process_competitor` reads `offer_scope` from the competitor run and passes it on.
- Step 3 compares with the Omantel reference **for that scope**. Matching logic is unchanged.
- Every Step 3–6 record gets `offer_scope` and `omantel_offer_scope`.
- **Step 5 window per scope (D7):**
  - Today the window is hard-coded at `risk_analysis_service.py:269` (`_shift_months(latest_month, -5)`) and `:474` (`months_used < 6`). These become `config/risk_scoring.yaml → performance_window_months: {ATL: 6, BTL: 3}`.
  - BTL loads `PRODUCT_PERFORMANCE_BTL.csv` and ATL loads `PRODUCT_PERFORMANCE.csv`. **The two files are never merged**, because the latest month in each file sets that file's window.
  - The `*_6m` output fields keep their names (renaming them would break the existing output). A new `performance_window_months` field records the window that was actually used.
- Step 6: for **BTL rows only**, the LLM facts JSON includes `"offer_scope": "BTL"`, so the narrative describes a targeted offer. The prompt template and prompt version are **not** changed, so ATL prompts, payloads and cache entries stay identical.
- Langfuse traces get an `offer_scope:<scope>` tag and metadata.

### Phase 4 — Report data and portfolio data

**Files:** `services/business_report_service.py`, `services/portfolio_analysis_service.py`

- Competitors are identified by `competitor_run_id`. Each gets `offer_scope` and a display label (`Vodafone · ATL`, `Vodafone · BTL`).
- When a competitor+scope was submitted more than once, the **latest completed** submission is used.
- Every record, no-match row and portfolio comparison carries `offer_scope` and `omantel_offer_scope` (D6, D14).
- Omantel product counts are shown for each scope, replacing the single `omantel_atl_products` value.
- `discover_run_specs` accepts ATL-only, BTL-only or mixed runs (D8).
- The free-offers list (D10) is included as an information-only block in the report data.

### Phase 5 — HTML report

**Files:** `scripts/report_template.html`

Minimum change, since presentation details are still open (D6):

- an **Offer scope** filter (All / ATL / BTL)
- an `Offer scope` column in the tables and the CSV export
- competitor labels that include the scope

Further layout changes are a later decision.

### Phase 6 — Tests and verification

| Area | Tests |
|---|---|
| API | `offer_scope` defaults to ATL; scope mismatch → 422; missing/duplicate `plan_id` → 422; invalid `product_type` → 422 |
| Step 2 | The BTL filter selects exactly the 49 products; every role/type is mapped |
| Step 5 | A BTL product with 3 months is scored; one with 2 months → `REVIEW_REQUIRED`; the ATL 6-month behaviour is unchanged |
| Pipeline | ATL and BTL competitors in one run both complete, and their stage outputs are kept separate |
| Report | ATL-only, BTL-only and mixed runs all generate; every record carries `offer_scope` |
| Regression | Re-processing a stored ATL run gives identical stage output |

---

## 5. Known limitations (accepted)

1. **No Omantel postpaid BTL products**, so postpaid BTL is entirely `NO_DIRECT_MATCH` (D13).
2. **3 of the 49 paid products** have fewer than 3 months of usage → `REVIEW_REQUIRED` for risk.
3. **BTL and ATL risk scores are each relative to their own largest product** (D15). A BTL risk of 8 and an ATL risk of 8 are not directly comparable in absolute business size.
4. **The 3-month window is a deviation** from the reference's 6 months. It is configurable and intended to change later (D7).
5. **Free offers aren't analysed** (D10). Vodafone `recharge_offers` aren't analysed either.
6. **The Vodafone BTL files still contain values flagged `NEEDS_SOURCE_VERIFICATION`** (`docs/vodafone_btl_fix_log.md`).
7. **The old BTL rows in the ATL catalogue files are not used for BTL.** 32 of their IDs are reused in the new export with different names (`docs/omantel_btl_build_log.md`).

## 6. Open items (not blocking)

- Postpaid BTL catalogue and usage data.
- How the HTML should present ATL vs BTL (D6), and portfolio advice per scope (D14).
- Whether to recalibrate the BTL risk scale or use separate risk cutoffs (D15).
- Replacing the 3-month BTL window with 6 months once enough data exists (D7).
