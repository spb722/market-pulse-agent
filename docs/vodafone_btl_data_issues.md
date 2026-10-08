# Vodafone BTL Data – Issues Report

**Files reviewed**

| Category | File | Plans |
|---|---|---|
| Prepaid | `data/competitors/vodafone/prepaid_vodafone_derived_features_updated_btl.json` | 65 (31 `master_plans`, 34 `addon_plans`) |
| Postpaid | `data/competitors/vodafone/postpaid_vodafone_derived_features_updated_btl.json` | 25 (2 `basic_plans`, 23 `addon_plans`) |

Both files come from the same extraction, `"key": "Normalized_Plans:vodafone:mp_2026-09-28T0707"`.

**Reference files used for comparison**

These are the Vodafone ATL files that Market Pulse already processes successfully:

- `data/competitors/vodafone/prepaid_vodafone_v1.txt`
- `data/competitors/vodafone/postpaid_vodafone_v1.txt`

**What is fine**

- The envelope structure is correct.
- The root counts (`total_plan_count`, `master_plan_count`, `basic_plan_count`, `addon_plan_count`) match the actual lists.
- Both files pass the Market Pulse API payload-shape validation.

**How locations are written**

The plans have no ID (see Issue 1), so every plan is referred to by its position in the JSON. The index is 0-based. For example, `prepaid master_plans[13]` means the 14th object inside `master_plans` in the prepaid file.

---

## Summary

| # | Issue | Severity | Plans affected |
|---|---|---|---|
| 1 | No `plan_id` on any plan | 🔴 Critical | 90 / 90 |
| 2 | The same offer is repeated under different names | 🔴 Critical | 50 plans in 19 duplicate groups |
| 3 | Recharge bonuses are listed as products | 🔴 Critical | 10 |
| 4 | International minutes are stored as national voice, with inconsistent `product_type` | 🔴 Critical | 10 international plans |
| 5 | Social-pass data is labelled `DATA` and has no `data_gb` | 🔴 Critical | 9 |
| 6 | The same benefit is offered at very different prices | 🟠 High | 12 |
| 7 | Contradictory or suspicious values in individual plans | 🟠 High | 8 findings (a–h) |
| 8 | `validity_days` is missing | 🟡 Medium | 58 / 90 |
| 9 | `product_type` values outside the allowed list | 🟡 Medium | 13 |
| 10 | `is_promo` is inconsistent for similar offers | 🟡 Medium | see the table |
| 11 | No BTL attributes; eligibility is only in free text | 🔵 BTL-specific | 90 / 90 (19 with rules in text) |
| 12 | `source_url` is null everywhere | ⚪ Low | 90 / 90 |
| 13 | Vodafone file contains a root key named `ooredoo_plans` | ⚪ Low | both files |

---

## Issue 1 – No `plan_id` on any plan 🔴

### What is there right now

The BTL plans have **24 fields**. `plan_id` is not one of them:

```text
plan_name, category, market_segment, price_omr, validity_days, contract_months,
data_gb, social_pass_gb, entertainment_gb, bonus_data_gb, voice_minutes,
unlimited_calls, flexi_minutes, intl_minutes, sms_count, unlimited_sms,
roaming_data_gb, roaming_included, is_promo, extra_benefits, source_url,
operator, type, product_type
```

Example from the BTL file, `prepaid master_plans[1]`:

```json
{
  "plan_name": "5GB", "category": "prepaid", "market_segment": "CONSUMER",
  "price_omr": 6, "validity_days": null, "data_gb": 5, "voice_minutes": null,
  "is_promo": false, "extra_benefits": null, "source_url": null,
  "operator": "vodafone", "type": "Master", "product_type": "DATA"
}
```

The **Vodafone ATL files have a `plan_id` on every plan**. Examples:

| ATL file | plan_name | plan_id |
|---|---|---|
| prepaid_vodafone_v1.txt | `RED 5` | `182aa46ca622` |
| postpaid_vodafone_v1.txt | `BLACK 12 Contract` | `2ab257e4285e` |

### Why it matters

Market Pulse tracks a plan through all six stages by its `plan_id`. When the ID is missing, it falls back to looking the plan up by `plan_name`. Four names appear in **both** the prepaid and postpaid files:

| plan_name | Prepaid location | Postpaid location |
|---|---|---|
| `5GB` | `master_plans[1]` (price 6, data 5) | `addon_plans[6]` (price 6, data 5) |
| `1GB Weekly` | `addon_plans[3]` (price 1, data 1, validity 7) | `addon_plans[4]` (price 1, data 1, validity 7) |
| `3GB` | `addon_plans[5]` (price 4, data 3) | `addon_plans[5]` (price 4, data 3) |
| `10GB` | `addon_plans[6]` (price 9, data 10) | `addon_plans[7]` (price 9, data 10) |

The gap-analysis step (Step 4) searches prepaid and postpaid plans together. For these 8 plans the name lookup finds two candidates and cannot pick one, so the plan ends up as `REVIEW_REQUIRED` and gets no gap score or risk score.

### Required fix

Add a `plan_id` to every plan, in the same format as the ATL files (a 12-character hex string). The ID must be **unique across both files** and **stay the same between extractions** for the same offer.

---

## Issue 2 – The same offer is repeated under different names 🔴

### What is there right now

In each group below, every row has identical **category, price, data_gb, social_pass_gb, voice_minutes and unlimited_calls**. Only the name and sometimes a bonus differ. In total there are 19 groups covering 50 plans.

#### Prepaid – master plans (8 groups)

The 31 prepaid master plans contain only about 13 distinct offers.

**Group P-1: 4 OMR, 5 GB, 150 minutes**

| Location | plan_name | validity_days | bonus_data_gb | is_promo | extra_benefits |
|---|---|---|---|---|---|
| `master_plans[2]` | `5GB + 150 minutes - 4 OMR` | null | null | false | null |
| `master_plans[7]` | `RED 5GB + 150Min` | null | null | false | null |
| `master_plans[21]` | `RO 4 - 5GB + 150 mins + 5GB FREE` | null | **5** | false | null |

**Group P-2: 8 OMR, 15 GB, 300 minutes**

| Location | plan_name | validity_days | bonus_data_gb | is_promo |
|---|---|---|---|---|
| `master_plans[3]` | `15GB + 300 minutes - 8 OMR` | null | null | false |
| `master_plans[8]` | `RED 15GB + 300Min` | null | null | false |
| `master_plans[15]` | `15GB + 300 Minutes - OMR 8` | null | null | false |

**Group P-3: 12 OMR, 25 GB, 500 minutes**

| Location | plan_name | validity_days | bonus_data_gb | is_promo |
|---|---|---|---|---|
| `master_plans[4]` | `25GB + 500 minutes - 12 OMR` | null | null | false |
| `master_plans[9]` | `RED 25GB + 500Min` | null | null | false |
| `master_plans[16]` | `25GB + 500 Minutes - OMR 12` | null | null | false |

**Group P-4: 16 OMR, 35 GB, unlimited minutes**

| Location | plan_name | validity_days | unlimited_calls | is_promo |
|---|---|---|---|---|
| `master_plans[5]` | `35GB + Unlimited minutes - 16 OMR` | null | true | false |
| `master_plans[10]` | `RED 35GB + Unlimited Min` | null | true | false |
| `master_plans[17]` | `35GB + Unlimited Minutes - OMR 16` | null | true | false |

**Group P-5: 24 OMR, 80 GB, unlimited minutes**

| Location | plan_name | validity_days | unlimited_calls | is_promo |
|---|---|---|---|---|
| `master_plans[6]` | `80GB + Unlimited minutes - 24 OMR` | null | true | false |
| `master_plans[11]` | `RED 80GB + Unlimited Min` | null | true | false |
| `master_plans[18]` | `80GB + Unlimited Minutes - OMR 24` | null | true | false |

**Group P-6: 5 OMR, 6 GB, 150 minutes (6 rows)**

| Location | plan_name | validity_days | bonus_data_gb | is_promo |
|---|---|---|---|---|
| `master_plans[14]` | `6GB + 150 Minutes - OMR 5` | null | null | false |
| `master_plans[20]` | `Advance New 25` | **28** | null | false |
| `master_plans[22]` | `RO 5 - 6GB + 150 mins + 10GB FREE` | null | **10** | false |
| `master_plans[23]` | `RED Advance 5` | null | null | false |
| `master_plans[26]` | `6GB + 150 mins - RO5` | null | null | false |
| `master_plans[28]` | `Marhaba RO5` | null | null | false |

`master_plans[13]` `Red Advance` (5 OMR, 150 minutes, 28 days) is probably the same offer too, but its data values contradict each other. See Issue 7.

**Group P-7: 7 OMR, 10 GB, 200 minutes**

| Location | plan_name | validity_days | is_promo |
|---|---|---|---|
| `master_plans[24]` | `RED Premier 7` | null | false |
| `master_plans[27]` | `10GB + 200 mins - RO7` | null | false |
| `master_plans[29]` | `Marhaba RO7` | null | false |

**Group P-8: 3 OMR, 60 minutes to Egypt**

| Location | plan_name | product_type | validity_days | extra_benefits |
|---|---|---|---|---|
| `master_plans[12]` | `60 minutes - OMR 3` | NV | 30 | `Egypt connectivity` |
| `master_plans[30]` | `60 Minutes - RO3` | NV | 30 | `Connect with Egypt` |

#### Prepaid – add-ons (6 groups)

**Group P-9: 1 OMR, 1 GB, 7 days**

| Location | plan_name | validity_days | is_promo |
|---|---|---|---|
| `addon_plans[3]` | `1GB Weekly` | 7 | false |
| `addon_plans[11]` | `1 GB Weekly` | 7 | false |

**Group P-10: 1 OMR, 20 minutes to Egypt**

| Location | plan_name | product_type | validity_days | extra_benefits |
|---|---|---|---|---|
| `addon_plans[9]` | `20 minutes - OMR 1` | IDD | 30 | `Egypt connectivity` |
| `addon_plans[33]` | `20 Minutes - RO1` | IDD | 30 | `Connect with Egypt` |

**Group P-11: 10 OMR, 10 GB, 7 days**

| Location | plan_name | validity_days | is_promo | extra_benefits |
|---|---|---|---|---|
| `addon_plans[24]` | `10 GB Top-up` | 7 | true | null |
| `addon_plans[27]` | `10 GB - OMR 10` | 7 | true | `Free data top-up` |

**Group P-12: 20 OMR, 30 GB social pass, 7 days**

| Location | plan_name | validity_days | is_promo | extra_benefits |
|---|---|---|---|---|
| `addon_plans[25]` | `30 GB Social Pass Top-up` | 7 | true | null |
| `addon_plans[28]` | `30 GB Social Pass - OMR 20` | 7 | true | `Free data top-up` |

**Group P-13: 30 OMR, 77 GB social pass, 7 days**

| Location | plan_name | validity_days | is_promo | extra_benefits |
|---|---|---|---|---|
| `addon_plans[26]` | `77 GB Social Pass Top-up` | 7 | true | null |
| `addon_plans[29]` | `77 GB Social Pass - OMR 30` | 7 | true | `Free data top-up` |

**Group P-14: 2 OMR, 77 GB (possible duplicate, with conflicting fields)**

| Location | plan_name | data_gb | social_pass_gb | validity_days | is_promo | extra_benefits |
|---|---|---|---|---|---|---|
| `addon_plans[22]` | `77GB Social Apps Bundle - 2 OMR` | null | 77 | 7 | false | `Social Pass for WhatsApp, Instagram, Snapchat, Facebook, Twitter (X)` |
| `addon_plans[23]` | `77GB for 2 OMR` | **77** | null | null | true | `Eid offer - Upgrade to RED Advance to activate` |

These two may be one offer captured twice with different fields. If they are, `addon_plans[23]` wrongly records the 77 GB as **general** data instead of social data.

#### Postpaid – add-ons (5 groups)

**Group Q-1: 4 OMR, 3 GB**

| Location | plan_name | validity_days | is_promo |
|---|---|---|---|
| `addon_plans[5]` | `3GB` | null | false |
| `addon_plans[13]` | `3GB for 4 OMR` | null | false |

**Group Q-2: 6 OMR, 5 GB**

| Location | plan_name | validity_days | is_promo |
|---|---|---|---|
| `addon_plans[6]` | `5GB` | null | false |
| `addon_plans[14]` | `5GB for 6 OMR` | null | false |

**Group Q-3: 9 OMR, 10 GB**

| Location | plan_name | validity_days | is_promo |
|---|---|---|---|
| `addon_plans[7]` | `10GB` | null | false |
| `addon_plans[15]` | `10GB for 9 OMR` | null | false |

**Group Q-4: 2 OMR, 5 GB**

| Location | plan_name | validity_days | is_promo | extra_benefits |
|---|---|---|---|---|
| `addon_plans[10]` | `5GB Extra - OMR 2` | 7 | true | null |
| `addon_plans[19]` | `5GB for OMR 2` | 7 | true | null |
| `addon_plans[22]` | `5GB for 2 OMR - Summer Special` | null | true | `Summer special add-on for RED Advance plan renewal` |

**Group Q-5: 5 OMR, 30 GB**

| Location | plan_name | validity_days | is_promo | extra_benefits |
|---|---|---|---|---|
| `addon_plans[18]` | `Winter Offer 30GB` | null | true | `DENZA B5 car prize draw` |
| `addon_plans[20]` | `30GB for 5 OMR` | 30 | true | `1GB daily for 30 Days` |
| `addon_plans[21]` | `Black - 30GB` | 30 | true | `Winter offer, Draw to win car and valuable prizes` |

### Why it matters

- One real offer is counted 2–6 times in plan counts, risk tables and the business report. That makes the competitor look like it has a much bigger BTL portfolio than it really does.
- Every duplicate is classified, matched and narrated separately, which means 2–6 times the LLM cost for the same answer.
- When duplicates disagree (validity, bonus, promo flag), they can get **different** results, and the report then contains contradictions.

### Required fix

- Keep **one record per real offer**.
- If two records are truly different campaigns of the same bundle (for example, different eligibility or dates), keep both. They must then have different `plan_id`s, and the field that differs (eligibility, campaign, bonus, validity) must be filled in.

---

## Issue 3 – Recharge bonuses are listed as products 🔴

### What is there right now

These 10 prepaid add-ons contain **no** data, minutes, SMS or validity. They describe credit you receive for recharging, not a bundle you buy:

| Location | plan_name | price_omr | product_type | extra_benefits |
|---|---|---|---|---|
| `addon_plans[12]` | `2 OMR Bonus` | 2 | UNKNOWN/OTHER | `Top up 2-4 OMR` |
| `addon_plans[13]` | `3 OMR Bonus` | 3 | UNKNOWN/OTHER | `Top up 5-7 OMR` |
| `addon_plans[14]` | `4 OMR Bonus` | 4 | UNKNOWN/OTHER | `Top up 8-9 OMR` |
| `addon_plans[15]` | `6 OMR Bonus` | 6 | UNKNOWN/OTHER | `Top up 10-14 OMR` |
| `addon_plans[16]` | `9 OMR Bonus` | 9 | UNKNOWN/OTHER | `Top up 15 OMR or more` |
| `addon_plans[17]` | `4 OMR Top Up + 1 OMR Bonus` | 4 | UNKNOWN/OTHER | `1 OMR bonus credit` |
| `addon_plans[18]` | `8 OMR Top Up + 2 OMR Bonus` | 8 | UNKNOWN/OTHER | `2 OMR bonus credit` |
| `addon_plans[19]` | `12 OMR Top Up + 3 OMR Bonus` | 12 | UNKNOWN/OTHER | `3 OMR bonus credit` |
| `addon_plans[20]` | `16 OMR Top Up + 4 OMR Bonus` | 16 | UNKNOWN/OTHER | `4 OMR bonus credit` |
| `addon_plans[21]` | `24 OMR Top Up + 5 OMR Bonus` | 24 | UNKNOWN/OTHER | `5 OMR bonus credit` |

`price_omr` means different things here:

- In `addon_plans[12]`–`[16]` it is the **bonus amount**. For example, `4 OMR Bonus` is what you get for topping up 8–9 OMR.
- In `addon_plans[17]`–`[21]` it is the **top-up amount**.

So the same field holds two different meanings within this one list.

### Why it matters

Market Pulse compares bundles: price against data, minutes and validity. A recharge bonus has nothing to compare, so it would be matched against an unrelated Omantel `OTHER` product or marked `NO_GOOD_MATCH`, which adds noise to the report.

### Required fix

Move recharge and top-up bonuses out of `addon_plans`, into a separate list such as `recharge_offers`, with explicit fields:

```json
{ "topup_min_omr": 8, "topup_max_omr": 9, "bonus_credit_omr": 4 }
```

---

## Issue 4 – International minutes stored as national voice 🔴

### What is there right now

`intl_minutes` is **null on all 90 plans**. International minutes are stored in `voice_minutes`, and `product_type` is set inconsistently:

| Location | plan_name | product_type | voice_minutes | intl_minutes | extra_benefits |
|---|---|---|---|---|---|
| prepaid `master_plans[12]` | `60 minutes - OMR 3` | **NV** | 60 | null | `Egypt connectivity` |
| prepaid `master_plans[30]` | `60 Minutes - RO3` | **NV** | 60 | null | `Connect with Egypt` |
| prepaid `addon_plans[0]` | `Call Bangladesh 100 Minutes` | IDD | 100 | null | null |
| prepaid `addon_plans[1]` | `Call Bangladesh 400 Minutes` | IDD | 400 | null | null |
| prepaid `addon_plans[7]` | `Call Pakistan 50 Minutes` | IDD | 50 | null | null |
| prepaid `addon_plans[8]` | `Call Pakistan 200 Minutes` | IDD | 200 | null | null |
| prepaid `addon_plans[9]` | `20 minutes - OMR 1` | IDD | 20 | null | `Egypt connectivity` |
| prepaid `addon_plans[33]` | `20 Minutes - RO1` | IDD | 20 | null | `Connect with Egypt` |
| postpaid `addon_plans[8]` | `Call India! 100 minutes` | **VOICE** | 100 | null | null |
| postpaid `addon_plans[9]` | `Call India! 400 minutes` | **VOICE** | 400 | null | null |

For comparison, these postpaid plans really are national voice:

| Location | plan_name | product_type | voice_minutes |
|---|---|---|---|
| postpaid `addon_plans[0]` | `77 National Minutes` | VOICE | 77 |
| postpaid `addon_plans[1]` | `300 National Minutes` | VOICE | 300 |
| postpaid `addon_plans[2]` | `500 National Minutes` | VOICE | 500 |

`Call India!` uses exactly the same fields and `product_type` as `National Minutes`, so nothing in the data separates them.

### Why it matters

- The `Call India!` packs would be compared with Omantel's **national** voice add-ons, which is the wrong comparison.
- The Egypt master plans (`NV`) would not be compared with Omantel IDD packs at all.
- Market Pulse sets `has_idd` from `intl_minutes > 0`, so it is false for every plan.
- The destination country (Egypt, Bangladesh, Pakistan, India) exists only in the plan name, but IDD plans are only comparable when the destination is the same.

### Required fix

For every international plan:

- put the minutes in `intl_minutes`, not `voice_minutes`
- set `product_type` to `"IDD"`
- add a destination field, for example `"intl_destinations": ["EG"]`

---

## Issue 5 – Social-pass data labelled `DATA`, with no `data_gb` 🔴

### What is there right now

| Location | plan_name | product_type | price_omr | data_gb | social_pass_gb | validity_days |
|---|---|---|---|---|---|---|
| prepaid `addon_plans[22]` | `77GB Social Apps Bundle - 2 OMR` | DATA | 2 | null | 77 | 7 |
| prepaid `addon_plans[25]` | `30 GB Social Pass Top-up` | DATA | 20 | null | 30 | 7 |
| prepaid `addon_plans[26]` | `77 GB Social Pass Top-up` | DATA | 30 | null | 77 | 7 |
| prepaid `addon_plans[28]` | `30 GB Social Pass - OMR 20` | DATA | 20 | null | 30 | 7 |
| prepaid `addon_plans[29]` | `77 GB Social Pass - OMR 30` | DATA | 30 | null | 77 | 7 |
| prepaid `addon_plans[31]` | `30 GB Social Pass for OMR 10` | DATA | 10 | null | 30 | null |
| prepaid `addon_plans[32]` | `77 GB Social Pass for OMR 15` | DATA | 15 | null | 77 | null |
| postpaid `addon_plans[17]` | `77GB Social Pass - 2 OMR` | DATA | 2 | null | 77 | 7 |
| prepaid `addon_plans[23]` | `77GB for 2 OMR` | DATA | 2 | **77** | null | null |

The first eight are recorded consistently: `data_gb` is null and `social_pass_gb` holds the value. `addon_plans[23]` does the opposite (see Issue 2, group P-14).

### Why it matters

- Market Pulse compares `DATA` plans on **price + data + validity**. With `data_gb = null`, the data comparison drops out completely, so these plans are scored almost entirely on price.
- If `addon_plans[23]` is really social data, recording it as 77 GB of general data for 2 OMR makes Vodafone look far stronger than it is. That would create a false high-risk result.

### Required fix

- Confirm whether `addon_plans[23]` is social data or general data, and record it in the correct field.
- Social-only bundles should be clearly identifiable. Either give them a distinct marker in the record, or agree with us how they should be classified.

---

## Issue 6 – Same benefit at very different prices 🟠

### What is there right now

| Benefit | Location | plan_name | price_omr | validity_days |
|---|---|---|---|---|
| **77 GB social pass** | prepaid `addon_plans[22]` | `77GB Social Apps Bundle - 2 OMR` | **2** | 7 |
| | postpaid `addon_plans[17]` | `77GB Social Pass - 2 OMR` | **2** | 7 |
| | prepaid `addon_plans[32]` | `77 GB Social Pass for OMR 15` | **15** | null |
| | prepaid `addon_plans[26]` / `[29]` | `77 GB Social Pass Top-up` / `- OMR 30` | **30** | 7 |
| **30 GB social pass** | prepaid `addon_plans[31]` | `30 GB Social Pass for OMR 10` | **10** | null |
| | prepaid `addon_plans[25]` / `[28]` | `30 GB Social Pass Top-up` / `- OMR 20` | **20** | 7 |
| **10 GB data (prepaid)** | prepaid `addon_plans[30]` | `10 GB for OMR 5` | **5** | null |
| | prepaid `addon_plans[6]` | `10GB` | **9** | null |
| | prepaid `addon_plans[24]` / `[27]` | `10 GB Top-up` / `10 GB - OMR 10` | **10** | 7 |

The same 77 GB social pass costs 2, 15 or 30 OMR, which is a 15× difference. A 7-day 10 GB top-up at 10 OMR costs more than the 9 OMR `10GB` add-on.

Rows `[24]`–`[29]` also say `"Free data top-up"` but have a price of 10–30 OMR.

### Why it matters

Either these are genuinely different campaigns (then eligibility or campaign details are missing, see Issue 11), or the price was extracted wrongly. A wrong price directly changes the Step 4 gap score and the Step 5 risk score.

### Required fix

Check each price against the source. Where the prices are correct, add the campaign or eligibility detail that explains the difference.

---

## Issue 7 – Contradictory or suspicious values in individual plans 🟠

**7a. `prepaid master_plans[13]` `Red Advance`: bonus data appears to be double-counted**

```json
{ "plan_name": "Red Advance", "price_omr": 5, "validity_days": 28,
  "data_gb": 16, "bonus_data_gb": 10, "voice_minutes": 150,
  "extra_benefits": "6GB local data included", "product_type": "COMBO" }
```

The text says 6 GB is included, but `data_gb` is 16, which is 6 + 10. The 10 GB bonus is already in `bonus_data_gb`. Expected: `data_gb: 6`, `bonus_data_gb: 10`.

**7b. `prepaid master_plans[19]` `RED Explore`: likely the same problem**

```json
{ "plan_name": "RED Explore", "price_omr": 13, "validity_days": null,
  "data_gb": 59, "bonus_data_gb": 30, "voice_minutes": 500, "is_promo": true,
  "extra_benefits": "Winter Prom Upgrade offer, 1GB daily for 30 Days bonus" }
```

The text describes a 30 GB bonus ("1GB daily for 30 Days"). `data_gb: 59` probably includes that bonus, which would make the base 29 GB. This needs checking against the source. Validity is also missing, even though the bonus says 30 days.

**7c. `prepaid master_plans[20]` `Advance New 25`: the name doesn't match the values**

```json
{ "plan_name": "Advance New 25", "price_omr": 5, "validity_days": 28,
  "data_gb": 6, "voice_minutes": 150 }
```

The name says "25", but the plan is 5 OMR, 6 GB and 150 minutes, the same as group P-6.

**7d. `prepaid master_plans[1]` `5GB`: an add-on labelled as a master plan**

```json
{ "plan_name": "5GB", "price_omr": 6, "data_gb": 5, "voice_minutes": null,
  "type": "Master", "product_type": "DATA" }
```

This is identical to postpaid `addon_plans[6]` `5GB` (6 OMR, 5 GB, `type: Addon`) and matches the prepaid add-on price ladder (`3GB` 4 OMR, `10GB` 9 OMR). It is most likely an add-on. Labelled as `Master`, it is compared with Omantel's main plans instead of its add-ons.

**7e. `Red Advance` vs `RED Advance`: two different products with nearly the same name**

| Location | plan_name | type | price_omr | data_gb | voice_minutes |
|---|---|---|---|---|---|
| prepaid `master_plans[13]` | `Red Advance` | Master | 5 | 16 | 150 |
| prepaid `addon_plans[2]` | `RED Advance` | Addon | 2 | 5 | null |

The add-on's real name looks like it was replaced by the name of the plan it attaches to. Its `extra_benefits` is `"Summer special"`, and it matches postpaid `addon_plans[22]` `5GB for 2 OMR - Summer Special`.

**7f. `postpaid basic_plans[1]` `Unlimited data for OMR 34`: no data value and an odd validity**

```json
{ "plan_name": "Unlimited data for OMR 34", "price_omr": 34,
  "validity_days": 360, "contract_months": 12, "data_gb": null,
  "product_type": "DATA" }
```

- There is no field that marks the data as unlimited; only the name says so.
- `validity_days: 360` on a postpaid monthly contract plan (`contract_months: 12`) looks like the contract length was put into validity. The other BTL base plan, `NextStep package`, has `validity_days: 30` and `contract_months: 12`.

**7g. `postpaid addon_plans[16]` `Summer Offer - Extra Data`: no data amount**

```json
{ "plan_name": "Summer Offer - Extra Data", "price_omr": 2, "validity_days": 7,
  "data_gb": null, "is_promo": true, "extra_benefits": "Extra data for 7 days",
  "product_type": "UNKNOWN/OTHER" }
```

A data offer with no data amount cannot be compared with anything.

**7h. Validity filled for one duplicate but not the others**

In group P-6, only `Advance New 25` has `validity_days: 28`. The other five identical offers have `null`. In group Q-5, `Winter Offer 30GB` has `null` while its two duplicates have `30`.

### Required fix

Correct each row against the source. As a rule:

- `data_gb` holds base data only, and the bonus goes in `bonus_data_gb`.
- Unlimited data is recorded explicitly.
- `validity_days` holds the bundle validity, never the contract length.

---

## Issue 8 – `validity_days` missing on 58 of 90 plans 🟡

### What is there right now

**Prepaid: 45 of 65 plans have `validity_days: null`**

| Location | plan_name | product_type | price_omr |
|---|---|---|---|
| `master_plans[1]` | `5GB` | DATA | 6 |
| `master_plans[2]` | `5GB + 150 minutes - 4 OMR` | COMBO | 4 |
| `master_plans[3]` | `15GB + 300 minutes - 8 OMR` | COMBO | 8 |
| `master_plans[4]` | `25GB + 500 minutes - 12 OMR` | COMBO | 12 |
| `master_plans[5]` | `35GB + Unlimited minutes - 16 OMR` | COMBO | 16 |
| `master_plans[6]` | `80GB + Unlimited minutes - 24 OMR` | COMBO | 24 |
| `master_plans[7]` | `RED 5GB + 150Min` | COMBO | 4 |
| `master_plans[8]` | `RED 15GB + 300Min` | COMBO | 8 |
| `master_plans[9]` | `RED 25GB + 500Min` | COMBO | 12 |
| `master_plans[10]` | `RED 35GB + Unlimited Min` | COMBO | 16 |
| `master_plans[11]` | `RED 80GB + Unlimited Min` | COMBO | 24 |
| `master_plans[14]` | `6GB + 150 Minutes - OMR 5` | COMBO | 5 |
| `master_plans[15]` | `15GB + 300 Minutes - OMR 8` | COMBO | 8 |
| `master_plans[16]` | `25GB + 500 Minutes - OMR 12` | COMBO | 12 |
| `master_plans[17]` | `35GB + Unlimited Minutes - OMR 16` | COMBO | 16 |
| `master_plans[18]` | `80GB + Unlimited Minutes - OMR 24` | COMBO | 24 |
| `master_plans[19]` | `RED Explore` | COMBO | 13 |
| `master_plans[21]` | `RO 4 - 5GB + 150 mins + 5GB FREE` | COMBO | 4 |
| `master_plans[22]` | `RO 5 - 6GB + 150 mins + 10GB FREE` | COMBO | 5 |
| `master_plans[23]` | `RED Advance 5` | COMBO | 5 |
| `master_plans[24]` | `RED Premier 7` | COMBO | 7 |
| `master_plans[25]` | `Vodafone Student Prepaid` | COMBO | 12 |
| `master_plans[26]` | `6GB + 150 mins - RO5` | COMBO | 5 |
| `master_plans[27]` | `10GB + 200 mins - RO7` | COMBO | 7 |
| `master_plans[28]` | `Marhaba RO5` | COMBO | 5 |
| `master_plans[29]` | `Marhaba RO7` | COMBO | 7 |
| `addon_plans[2]` | `RED Advance` | DATA | 2 |
| `addon_plans[5]` | `3GB` | DATA | 4 |
| `addon_plans[6]` | `10GB` | DATA | 9 |
| `addon_plans[7]` | `Call Pakistan 50 Minutes` | IDD | 1 |
| `addon_plans[8]` | `Call Pakistan 200 Minutes` | IDD | 3 |
| `addon_plans[12]` | `2 OMR Bonus` | UNKNOWN/OTHER | 2 |
| `addon_plans[13]` | `3 OMR Bonus` | UNKNOWN/OTHER | 3 |
| `addon_plans[14]` | `4 OMR Bonus` | UNKNOWN/OTHER | 4 |
| `addon_plans[15]` | `6 OMR Bonus` | UNKNOWN/OTHER | 6 |
| `addon_plans[16]` | `9 OMR Bonus` | UNKNOWN/OTHER | 9 |
| `addon_plans[17]` | `4 OMR Top Up + 1 OMR Bonus` | UNKNOWN/OTHER | 4 |
| `addon_plans[18]` | `8 OMR Top Up + 2 OMR Bonus` | UNKNOWN/OTHER | 8 |
| `addon_plans[19]` | `12 OMR Top Up + 3 OMR Bonus` | UNKNOWN/OTHER | 12 |
| `addon_plans[20]` | `16 OMR Top Up + 4 OMR Bonus` | UNKNOWN/OTHER | 16 |
| `addon_plans[21]` | `24 OMR Top Up + 5 OMR Bonus` | UNKNOWN/OTHER | 24 |
| `addon_plans[23]` | `77GB for 2 OMR` | DATA | 2 |
| `addon_plans[30]` | `10 GB for OMR 5` | DATA | 5 |
| `addon_plans[31]` | `30 GB Social Pass for OMR 10` | DATA | 10 |
| `addon_plans[32]` | `77 GB Social Pass for OMR 15` | DATA | 15 |

**Postpaid: 13 of 25 plans have `validity_days: null`**

| Location | plan_name | product_type | price_omr |
|---|---|---|---|
| `addon_plans[0]` | `77 National Minutes` | VOICE | 1 |
| `addon_plans[1]` | `300 National Minutes` | VOICE | 3 |
| `addon_plans[2]` | `500 National Minutes` | VOICE | 5 |
| `addon_plans[5]` | `3GB` | DATA | 4 |
| `addon_plans[6]` | `5GB` | DATA | 6 |
| `addon_plans[7]` | `10GB` | DATA | 9 |
| `addon_plans[11]` | `600GB for OMR 24` | DATA | 24 |
| `addon_plans[12]` | `1TB for OMR 29` | DATA | 29 |
| `addon_plans[13]` | `3GB for 4 OMR` | DATA | 4 |
| `addon_plans[14]` | `5GB for 6 OMR` | DATA | 6 |
| `addon_plans[15]` | `10GB for 9 OMR` | DATA | 9 |
| `addon_plans[18]` | `Winter Offer 30GB` | DATA | 5 |
| `addon_plans[22]` | `5GB for 2 OMR - Summer Special` | DATA | 2 |

### Why it matters

Validity is one of the metrics used for matching (weight 0.10 in Step 3 similarity) and for gap analysis (weight 0.10–0.15 for most product types in Step 4). When it is missing:

- a 7-day pack and a 30-day pack look equally similar
- the gap score is calculated without validity
- the plan is put in the `UNKNOWN` validity bucket

### Required fix

Fill in `validity_days` for every bundle. If the source really does not state a validity, mark it explicitly (for example `"validity_source": "NOT_STATED"`) so we can tell "unknown" apart from "forgot to extract".

---

## Issue 9 – `product_type` values outside the allowed list 🟡

### What is there right now

The allowed values are `COMBO`, `DATA`, `VOICE`, `IDD`, `ROAMING`, `SMS`, `OTHER`.

| Value in file | Count | Allowed? |
|---|---|---|
| `COMBO` | 29 | ✅ |
| `DATA` | 37 | ✅ (but see Issue 5) |
| `IDD` | 6 | ✅ |
| `VOICE` | 5 | ✅ (but see Issue 4) |
| `UNKNOWN/OTHER` | 11 | ❌ |
| `NV` | 2 | ❌ |

`UNKNOWN/OTHER` rows:

- prepaid `addon_plans[12]`–`[21]`, the 10 recharge bonuses from Issue 3
- postpaid `addon_plans[16]` `Summer Offer - Extra Data`

`NV` rows:

- prepaid `master_plans[12]` `60 minutes - OMR 3`
- prepaid `master_plans[30]` `60 Minutes - RO3`

Both are Egypt international minutes and should be `IDD`.

### Why it matters

Market Pulse does not recognise these values. It falls back to the LLM's guess at the product type, which means less predictable matching.

### Required fix

Use only the allowed values.

---

## Issue 10 – `is_promo` inconsistent for similar offers 🟡

### What is there right now

| Location | plan_name | price_omr | data_gb | validity_days | is_promo | extra_benefits |
|---|---|---|---|---|---|---|
| prepaid `addon_plans[30]` | `10 GB for OMR 5` | 5 | 10 | null | **false** | null |
| prepaid `addon_plans[6]` | `10GB` | 9 | 10 | null | **false** | null |
| prepaid `addon_plans[24]` | `10 GB Top-up` | 10 | 10 | 7 | **true** | null |
| prepaid `addon_plans[27]` | `10 GB - OMR 10` | 10 | 10 | 7 | **true** | `Free data top-up` |
| prepaid `addon_plans[22]` | `77GB Social Apps Bundle - 2 OMR` | 2 | null | 7 | **false** | Social Pass … |
| prepaid `addon_plans[23]` | `77GB for 2 OMR` | 2 | 77 | null | **true** | `Eid offer - …` |

In this table, the 10 OMR packs are flagged as promotions while the cheaper 5 OMR pack is not, and the two 77 GB / 2 OMR packs are flagged differently. Overall, 29 of 90 BTL plans are `is_promo: true`, against 0 in the Vodafone ATL files.

### Required fix

Define what counts as a promotion (time-limited campaign? discounted price? prize draw?) and apply that definition consistently.

---

## Issue 11 – No BTL attributes; eligibility only in free text 🔵

### What is there right now

Nothing in the plan records says the offer is BTL. The only clue is the filename. Every plan has `market_segment: "CONSUMER"`. Who can get the offer appears only in `plan_name` or `extra_benefits`:

| Location | plan_name | Eligibility hidden in text |
|---|---|---|
| prepaid `master_plans[0]` | `NextStep graduates package` | Graduates only (name). `extra_benefits: "Free vanity number"` |
| postpaid `basic_plans[0]` | `NextStep package` | Same programme (graduates?). Not stated |
| prepaid `master_plans[25]` | `Vodafone Student Prepaid` | Students only: `"International Student ISIC card with discounts and exclusive offers"` |
| prepaid `master_plans[19]` | `RED Explore` | `"Winter Prom Upgrade offer, 1GB daily for 30 Days bonus"` |
| prepaid `addon_plans[23]` | `77GB for 2 OMR` | `"Eid offer - Upgrade to RED Advance to activate"`, requires a specific plan |
| postpaid `addon_plans[22]` | `5GB for 2 OMR - Summer Special` | `"Summer special add-on for RED Advance plan renewal"`, requires a specific plan |
| prepaid `addon_plans[10]` | `30 GB for OMR 5` | `"Enter draw for car and other valuable prizes"` |
| postpaid `addon_plans[18]` | `Winter Offer 30GB` | `"DENZA B5 car prize draw"` |
| postpaid `addon_plans[21]` | `Black - 30GB` | `"Winter offer, Draw to win car and valuable prizes"` |
| prepaid `addon_plans[12]`–`[21]` | Recharge bonuses | Top-up ranges, e.g. `"Top up 8-9 OMR"` |

### Why it matters

A BTL offer only means something together with **who receives it**. Without that, Market Pulse cannot:

- separate BTL results from ATL results for the same competitor
- tell that a 2 OMR add-on only works on the `RED Advance` plan
- recognise student or graduate offers as targeted segments rather than offers to all consumers

### Required fix

Add structured fields to every BTL plan. For example:

```json
{
  "offer_scope": "BTL",
  "target_segment": "STUDENT",
  "eligibility": "Valid ISIC student card",
  "requires_plan": null,
  "channel": "SMS",
  "campaign": "Winter 2026",
  "valid_from": "2026-09-01",
  "valid_to": "2026-12-31"
}
```

---

## Issue 12 – `source_url` is null on every plan ⚪

### What is there right now

`source_url: null` on all 90 BTL plans. The Vodafone ATL files are also null, so this problem is not specific to BTL.

### Why it matters

No plan can be traced back to where it was captured, which makes it impossible to check the questions raised in Issues 6 and 7. Market Pulse also uses `source_url` (a `/b2b` path) to detect business plans.

### Required fix

Record where each offer was found. For BTL this may be a source reference rather than a URL, for example an SMS ID, a capture date or a screenshot ID.

---

## Issue 13 – Root key `ooredoo_plans` inside Vodafone files ⚪

### What is there right now

Both root objects contain:

```json
"key": "Normalized_Plans:vodafone:mp_2026-09-28T0707",
"operator": "vodafone",
"ooredoo_plans": [ ... ]
```

| File | `ooredoo_plans` length | Contents |
|---|---|---|
| prepaid | 65 | The same 65 plans as `master_plans` + `addon_plans`, identical fields, different order |
| postpaid | 25 | The same 25 plans as `basic_plans` + `addon_plans`, identical fields, different order |

### Why it matters

Market Pulse ignores this key, so it has no effect on results. But the key is named after the wrong operator, it duplicates the whole payload, and it suggests the extraction template was copied from the Ooredoo job.

### Required fix

Remove the key, or rename it to something like `all_plans`.

---

## Appendix – Example of a corrected BTL plan record

```json
{
  "plan_id": "a1b2c3d4e5f6",
  "plan_name": "RED Advance 5",
  "operator": "vodafone",
  "category": "prepaid",
  "type": "Master",
  "product_type": "COMBO",
  "market_segment": "CONSUMER",
  "price_omr": 5,
  "validity_days": 28,
  "contract_months": null,
  "data_gb": 6,
  "bonus_data_gb": null,
  "social_pass_gb": null,
  "entertainment_gb": null,
  "voice_minutes": 150,
  "unlimited_calls": false,
  "flexi_minutes": null,
  "intl_minutes": null,
  "sms_count": null,
  "unlimited_sms": false,
  "roaming_data_gb": null,
  "roaming_included": false,
  "is_promo": false,
  "extra_benefits": null,
  "source_url": "<where the offer was captured>",
  "offer_scope": "BTL",
  "target_segment": "<who receives it>",
  "eligibility": "<conditions>",
  "requires_plan": null,
  "channel": "SMS",
  "campaign": null,
  "valid_from": null,
  "valid_to": null
}
```

## Fix priority for the extraction team

1. Add a stable, unique `plan_id` (Issue 1).
2. Remove duplicate offers (Issue 2).
3. Move recharge bonuses out of `addon_plans` (Issue 3).
4. Put international minutes in `intl_minutes`, with `product_type: IDD` and a destination (Issue 4).
5. Correct the social-pass and double-counted data values (Issues 5, 7).
6. Check the prices that vary widely for the same benefit (Issue 6).
7. Fill in `validity_days` (Issue 8).
8. Use only the allowed `product_type` values (Issue 9).
9. Define `is_promo` consistently (Issue 10).
10. Add the BTL targeting fields (Issue 11).
11. Fill in `source_url` (Issue 12) and remove `ooredoo_plans` (Issue 13).
