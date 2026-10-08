# Competitor BTL JSON – Required Format

This is the format Market Pulse needs for a competitor's BTL (targeted) offers.

It is the same envelope and plan fields as the competitor ATL files, plus a few BTL fields. A file in this format can be submitted to `POST /runs/{run_id}/competitors` without errors.

Reference examples:

- `data/competitors/vodafone/prepaid_vodafone_btl_v2.json`
- `data/competitors/vodafone/postpaid_vodafone_btl_v2.json`

---

## 1. File envelope

A JSON **list containing exactly one root object**. Use one file per category.

**Prepaid**

```json
[
  {
    "key": "Normalized_Plans:vodafone:mp_2026-09-28T0707",
    "operator": "vodafone",
    "category": "prepaid",
    "offer_scope": "BTL",
    "total_plan_count": 34,
    "master_plan_count": 15,
    "addon_plan_count": 19,
    "master_plans": [ /* plan objects */ ],
    "addon_plans":  [ /* plan objects */ ],
    "recharge_offers": [ /* optional, see section 4 */ ],
    "recharge_offer_count": 10
  }
]
```

**Postpaid**

The postpaid file is the same, except it uses `basic_plans` and `basic_plan_count` instead of `master_plans` and `master_plan_count`.

**Rules**

| Rule | Why |
|---|---|
| The root must be a list `[ {...} ]`, not a bare object or a flat list of plans | The API rejects anything else with a 422 error |
| Prepaid uses `master_plans`, postpaid uses `basic_plans`. Both use `addon_plans` | Market Pulse only reads these lists |
| The counts must equal the list lengths | Consistency check |
| Don't add extra copies of the plan lists (e.g. `ooredoo_plans`) | They are ignored and only confuse people reading the file |

---

## 2. Plan object

### Required fields

Every plan must have these fields. Use `null` or `false` when a value doesn't apply.

| Field | Type | Rule |
|---|---|---|
| `plan_id` | string | **Required and unique** across prepaid and postpaid. A 12-character hex string (like ATL, e.g. `"182aa46ca622"`). It must stay the same for the same offer in every future extraction. |
| `plan_name` | string | The offer's real name, as the customer sees it. It must not be the name of the parent plan (e.g. not `"RED Advance"` for an add-on). |
| `operator` | string | e.g. `"vodafone"` |
| `category` | string | `"prepaid"` or `"postpaid"`, matching the file |
| `type` | string | `"Master"` (prepaid main plan), `"Basic_Plan"` (postpaid main plan) or `"Addon"` |
| `product_type` | string | **Exactly one of** `COMBO`, `DATA`, `VOICE`, `IDD`, `ROAMING`, `SMS`, `OTHER`. Never `NV` or `UNKNOWN/OTHER`. |
| `market_segment` | string | `"CONSUMER"` or `"BUSINESS"` |
| `price_omr` | number | What the customer pays for the bundle. It must not be a top-up amount (see section 4). |
| `validity_days` | number or null | The bundle validity, e.g. 7 or 30. **Never the contract length.** Use `null` only if the source truly doesn't state it. |
| `contract_months` | number or null | Postpaid contract length |
| `data_gb` | number or null | **Base** general data only. Leave the bonus out of this number. |
| `bonus_data_gb` | number or null | Bonus or free data on top of the base data |
| `social_pass_gb` | number or null | Data that only works for social apps. Don't put it in `data_gb`. |
| `entertainment_gb` | number or null | Data that only works for entertainment apps |
| `voice_minutes` | number or null | **National** minutes only |
| `unlimited_calls` | boolean | `true` if national minutes are unlimited |
| `flexi_minutes` | number or null | Flexi minutes |
| `intl_minutes` | number or null | **International** minutes. Don't put them in `voice_minutes`. |
| `sms_count` | number or null | Number of SMS |
| `unlimited_sms` | boolean | `true` if SMS are unlimited |
| `roaming_data_gb` | number or null | Roaming data |
| `roaming_included` | boolean | `true` if roaming is included |
| `is_promo` | boolean | `true` only for a time-limited campaign. Apply the same rule to every offer. |
| `extra_benefits` | string or null | Free text describing other benefits |
| `source_url` | string or null | Where the offer was captured: a URL, SMS ID or screenshot reference |

### Optional fields

These are recommended where they apply.

| Field | Type | Use |
|---|---|---|
| `unlimited_data` | boolean | `true` when data is unlimited (leave `data_gb` as `null`). |
| `intl_destinations` | list of ISO codes | Required when `product_type` is `IDD`, e.g. `["IN"]`, `["EG"]`, `["BD"]`, `["PK"]`. |

### BTL fields

Add these to every BTL plan.

| Field | Type | Use |
|---|---|---|
| `offer_scope` | string | `"BTL"` |
| `target_segment` | string or null | Who receives the offer, e.g. `STUDENT`, `GRADUATE`, `CHURN_RISK`, `LOW_ARPU` |
| `eligibility` | string or null | The condition in plain words, e.g. `"Valid ISIC student card"` |
| `requires_plan` | string or null | The base plan the customer must have, e.g. `"RED Advance"` |
| `channel` | string or null | How it is delivered: `SMS`, `APP`, `USSD`, `CALL_CENTER` |
| `campaign` | string or null | e.g. `"Eid offer"`, `"Winter offer - car prize draw"` |
| `valid_from`, `valid_to` | ISO date or null | Campaign dates |

### Rules for plan values

1. **One record per real offer.** If the same bundle (same price, data, minutes and validity) appears under several names, keep one record and put the other names in `alias_names`. Keep two records only if they really differ (different eligibility, campaign or bonus), and then they need different `plan_id`s.
2. **International minutes:** use `intl_minutes`, `product_type: "IDD"` and `intl_destinations`.
3. **Social-only data:** use `social_pass_gb`, with `data_gb: null`.
4. **Bonus data:** use `bonus_data_gb`, and never add it to `data_gb`.
5. **Unlimited data:** use `unlimited_data: true`, with `data_gb: null`.
6. **Every bundle needs at least one measurable benefit:** data, minutes, SMS or validity. Records that are only recharge incentives go in `recharge_offers` (section 4).

### Example: master plan

```json
{
  "plan_id": "1f1c38ea7ad8",
  "plan_name": "6GB + 150 Minutes - OMR 5",
  "alias_names": ["Advance New 25", "RED Advance 5", "6GB + 150 mins - RO5", "Marhaba RO5"],
  "operator": "vodafone", "category": "prepaid", "type": "Master", "product_type": "COMBO",
  "market_segment": "CONSUMER", "price_omr": 5, "validity_days": 28, "contract_months": null,
  "data_gb": 6, "bonus_data_gb": null, "social_pass_gb": null, "entertainment_gb": null,
  "voice_minutes": 150, "unlimited_calls": false, "flexi_minutes": null, "intl_minutes": null,
  "sms_count": null, "unlimited_sms": false, "roaming_data_gb": null, "roaming_included": false,
  "is_promo": false, "extra_benefits": null, "source_url": null,
  "offer_scope": "BTL", "target_segment": null, "eligibility": null, "requires_plan": null,
  "channel": null, "campaign": null
}
```

### Example: IDD add-on

```json
{
  "plan_id": "07568e4b0229",
  "plan_name": "Call India! 100 minutes",
  "operator": "vodafone", "category": "postpaid", "type": "Addon", "product_type": "IDD",
  "price_omr": 1, "validity_days": 30,
  "voice_minutes": null, "intl_minutes": 100, "intl_destinations": ["IN"],
  "offer_scope": "BTL"
}
```

The other fields are omitted here for brevity, but a real record must include all the required fields.

---

## 3. Data-quality flags

When a value can't be confirmed from the source, keep the best available value and flag it. Don't delete it.

```json
"data_quality_flags": ["NEEDS_SOURCE_VERIFICATION"],
"data_quality_notes": ["data_gb 59 may include the 30GB bonus"]
```

| Flag | Meaning |
|---|---|
| `NEEDS_SOURCE_VERIFICATION` | The value looks inconsistent and should be checked |
| `POSSIBLE_DUPLICATE` | Same commercial values as another record, but the text suggests a different campaign |

These fields are carried through the pipeline for information only. They don't change any score.

---

## 4. Recharge offers (prepaid, optional)

"Top up X and get Y bonus credit" offers are **not bundles**, so they can't be compared. Put them in `root.recharge_offers`, not in `addon_plans`:

```json
{
  "offer_id": "ba65f20b4ca9",
  "offer_name": "4 OMR Bonus",
  "operator": "vodafone", "category": "prepaid", "offer_scope": "BTL",
  "topup_min_omr": 8, "topup_max_omr": 9, "bonus_credit_omr": 4,
  "is_promo": true, "extra_benefits": "Top up 8-9 OMR"
}
```

Use `topup_max_omr: null` for "or more". Market Pulse does not analyse `recharge_offers` today. They are kept so the information isn't lost.

---

## 5. Checklist before sharing a file

- [ ] The file is `[ { ... } ]`, and the root has the right list names for its category
- [ ] Every plan has a unique `plan_id`
- [ ] No duplicate offers (or the extra names are in `alias_names`)
- [ ] `product_type` uses only the allowed values
- [ ] International minutes are in `intl_minutes`, with `IDD` and `intl_destinations`
- [ ] `data_gb` doesn't include bonus or social data
- [ ] `validity_days` is filled for every bundle
- [ ] Every record has `offer_scope: "BTL"`, plus targeting fields where known
- [ ] Recharge bonuses are in `recharge_offers`, not `addon_plans`
- [ ] The counts match the list lengths
