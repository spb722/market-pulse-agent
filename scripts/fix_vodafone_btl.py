"""Produce cleaned Vodafone BTL payloads from the 2026-09-28 extraction.

Reads (never modifies):
    data/competitors/vodafone/prepaid_vodafone_derived_features_updated_btl.json
    data/competitors/vodafone/postpaid_vodafone_derived_features_updated_btl.json

Writes:
    data/competitors/vodafone/prepaid_vodafone_btl_v2.json
    data/competitors/vodafone/postpaid_vodafone_btl_v2.json
    docs/vodafone_btl_fix_log.md

Every change is keyed by the plan's original location (list name + 0-based
index) and asserts the original value before changing it, so the script fails
loudly if run against a different extraction. Issue numbers refer to
docs/vodafone_btl_data_issues.md.

Usage:
    python scripts/fix_vodafone_btl.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "competitors" / "vodafone"
SOURCES = {
    "prepaid": DATA / "prepaid_vodafone_derived_features_updated_btl.json",
    "postpaid": DATA / "postpaid_vodafone_derived_features_updated_btl.json",
}
OUTPUTS = {
    "prepaid": DATA / "prepaid_vodafone_btl_v2.json",
    "postpaid": DATA / "postpaid_vodafone_btl_v2.json",
}
LOG_PATH = ROOT / "docs" / "vodafone_btl_fix_log.md"

MAIN_LIST = {"prepaid": "master_plans", "postpaid": "basic_plans"}
ALLOWED_PRODUCT_TYPES = {"COMBO", "DATA", "VOICE", "IDD", "ROAMING", "SMS", "OTHER"}

# ---------------------------------------------------------------------------
# Issue 2 - duplicate groups: (category, list, [indices]); first index is kept.
# Only rows with identical commercial fields AND only cosmetic text
# differences are merged. Rows whose text signals a different campaign or
# eligibility are kept and flagged instead (see POSSIBLE_DUPLICATES).
# ---------------------------------------------------------------------------
MERGES = [
    ("prepaid", "master_plans", [2, 7]),            # P-1 (bonus variant [21] kept)
    ("prepaid", "master_plans", [3, 8, 15]),        # P-2
    ("prepaid", "master_plans", [4, 9, 16]),        # P-3
    ("prepaid", "master_plans", [5, 10, 17]),       # P-4
    ("prepaid", "master_plans", [6, 11, 18]),       # P-5
    ("prepaid", "master_plans", [14, 20, 23, 26, 28]),  # P-6 (bonus variant [22] kept)
    ("prepaid", "master_plans", [24, 27, 29]),      # P-7
    ("prepaid", "master_plans", [12, 30]),          # P-8
    ("prepaid", "addon_plans", [3, 11]),            # P-9
    ("prepaid", "addon_plans", [9, 33]),            # P-10
    ("prepaid", "addon_plans", [27, 24]),           # P-11 (keep the one with benefits text)
    ("prepaid", "addon_plans", [28, 25]),           # P-12
    ("prepaid", "addon_plans", [29, 26]),           # P-13
    ("postpaid", "addon_plans", [13, 5]),           # Q-1
    ("postpaid", "addon_plans", [14, 6]),           # Q-2
    ("postpaid", "addon_plans", [15, 7]),           # Q-3
    ("postpaid", "addon_plans", [19, 10]),          # Q-4 (Summer Special [22] kept)
]

POSSIBLE_DUPLICATES = {
    ("prepaid", "addon_plans", 22): "Same price and 77GB as addon_plans[23] '77GB for 2 OMR' but recorded as social data; different campaign text.",
    ("prepaid", "addon_plans", 23): "Same price and 77GB as addon_plans[22] '77GB Social Apps Bundle - 2 OMR'; recorded as general data_gb. Confirm social vs general data.",
    ("postpaid", "addon_plans", 18): "Same price/data as addon_plans[20] and [21] (30GB / 5 OMR); different campaign text.",
    ("postpaid", "addon_plans", 20): "Same price/data as addon_plans[18] and [21] (30GB / 5 OMR); different campaign text.",
    ("postpaid", "addon_plans", 21): "Same price/data as addon_plans[18] and [20] (30GB / 5 OMR); 'Black' may indicate a plan-tier restriction.",
    ("prepaid", "master_plans", 13): "Probably the same offer as the 5 OMR / 6GB / 150 min group, once the data value is corrected.",
}

# ---------------------------------------------------------------------------
# Issue 3 - recharge bonuses moved to root.recharge_offers
# ---------------------------------------------------------------------------
RECHARGE_ADDON_INDICES = list(range(12, 22))  # prepaid addon_plans[12]..[21]

# ---------------------------------------------------------------------------
# Issue 4 - international minutes: (category, list, index) -> destinations
# ---------------------------------------------------------------------------
INTERNATIONAL = {
    ("prepaid", "master_plans", 12): ["EG"],
    ("prepaid", "master_plans", 30): ["EG"],
    ("prepaid", "addon_plans", 0): ["BD"],
    ("prepaid", "addon_plans", 1): ["BD"],
    ("prepaid", "addon_plans", 7): ["PK"],
    ("prepaid", "addon_plans", 8): ["PK"],
    ("prepaid", "addon_plans", 9): ["EG"],
    ("prepaid", "addon_plans", 33): ["EG"],
    ("postpaid", "addon_plans", 8): ["IN"],
    ("postpaid", "addon_plans", 9): ["IN"],
}

# ---------------------------------------------------------------------------
# Issues 7 / 9 - individual value corrections:
# (category, list, index, field, expected_original, new_value, reason)
# ---------------------------------------------------------------------------
CORRECTIONS = [
    ("prepaid", "master_plans", 13, "data_gb", 16, 6,
     "extra_benefits states '6GB local data included'; the 10GB bonus is already in bonus_data_gb (16 = 6 + 10 double count)."),
    ("prepaid", "master_plans", 1, "type", "Master", "Addon",
     "Identical to postpaid addon '5GB' (6 OMR, 5GB) and fits the prepaid data add-on ladder (3GB 4 OMR / 10GB 9 OMR)."),
    ("postpaid", "basic_plans", 1, "validity_days", 360, 30,
     "Monthly postpaid contract plan (contract_months 12); 360 looks like the contract length. Matches 'NextStep package' (validity 30, contract 12)."),
    ("postpaid", "addon_plans", 16, "product_type", "UNKNOWN/OTHER", "DATA",
     "extra_benefits 'Extra data for 7 days' - a data add-on; 'UNKNOWN/OTHER' is not an allowed value."),
]

# Field additions that do not overwrite an existing value.
ADDITIONS = [
    ("postpaid", "basic_plans", 1, "unlimited_data", True,
     "Name 'Unlimited data for OMR 34'; makes unlimited data explicit (pipeline reads the 'unlimited_data' flag)."),
]

# ---------------------------------------------------------------------------
# Issue 11 - BTL targeting derived from the plan's own name/benefits text
# ---------------------------------------------------------------------------
TARGETING = {
    ("prepaid", "master_plans", 0): {"target_segment": "GRADUATE", "eligibility": "NextStep graduates package"},
    ("postpaid", "basic_plans", 0): {"target_segment": "GRADUATE", "eligibility": "NextStep package (graduate programme - confirm)"},
    ("prepaid", "master_plans", 25): {"target_segment": "STUDENT", "eligibility": "International Student ISIC card"},
    ("prepaid", "master_plans", 19): {"campaign": "Winter Prom Upgrade offer"},
    ("prepaid", "addon_plans", 23): {"requires_plan": "RED Advance", "campaign": "Eid offer", "eligibility": "Upgrade to RED Advance to activate"},
    ("postpaid", "addon_plans", 22): {"requires_plan": "RED Advance", "campaign": "Summer special", "eligibility": "Add-on for RED Advance plan renewal"},
    ("prepaid", "addon_plans", 2): {"campaign": "Summer special"},
    ("prepaid", "addon_plans", 10): {"campaign": "Prize draw (car)"},
    ("postpaid", "addon_plans", 18): {"campaign": "Winter offer - DENZA B5 car prize draw"},
    ("postpaid", "addon_plans", 21): {"campaign": "Winter offer - car prize draw"},
}

# ---------------------------------------------------------------------------
# Values left unchanged because they need confirmation from the source.
# ---------------------------------------------------------------------------
NEEDS_VERIFICATION = {
    ("prepaid", "master_plans", 19): "data_gb 59 with bonus_data_gb 30 ('1GB daily for 30 Days bonus') - base data may be 29 (double count). validity_days missing.",
    ("prepaid", "addon_plans", 2): "Named 'RED Advance' (same as the master plan) - real add-on name unknown; looks like the 'Summer Special' 5GB / 2 OMR add-on.",
    ("prepaid", "addon_plans", 23): "Confirm whether the 77GB is general data or social data.",
    ("postpaid", "addon_plans", 16): "No data amount stated - cannot be compared on data.",
    ("prepaid", "addon_plans", 22): "77GB social pass at 2 OMR vs 15 OMR (addon_plans[32]) vs 30 OMR (addon_plans[29]) - confirm prices.",
    ("prepaid", "addon_plans", 29): "77GB social pass at 30 OMR while 'Free data top-up' - confirm price.",
    ("prepaid", "addon_plans", 32): "77GB social pass at 15 OMR - confirm price.",
    ("prepaid", "addon_plans", 28): "30GB social pass at 20 OMR while 'Free data top-up' vs 10 OMR (addon_plans[31]) - confirm price.",
    ("prepaid", "addon_plans", 31): "30GB social pass at 10 OMR vs 20 OMR (addon_plans[28]) - confirm price.",
    ("prepaid", "addon_plans", 27): "10GB / 7 days at 10 OMR while 'Free data top-up' vs 5 OMR (addon_plans[30]) - confirm price.",
}


def make_plan_id(category: str, plan_type: str, plan_name: str) -> str:
    """Deterministic 12-hex id, same format as the ATL files."""

    key = f"vodafone|btl|{category}|{plan_type}|{plan_name.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def parse_recharge(plan: dict) -> dict:
    """Turn a recharge-bonus pseudo-plan into a structured recharge offer."""

    name = plan["plan_name"]
    text = plan.get("extra_benefits") or ""
    combo = re.fullmatch(r"(\d+) OMR Top Up \+ (\d+) OMR Bonus", name)
    if combo:
        topup = float(combo.group(1))
        return {"topup_min_omr": topup, "topup_max_omr": topup, "bonus_credit_omr": float(combo.group(2))}

    bonus = re.fullmatch(r"(\d+) OMR Bonus", name)
    assert bonus, f"Unrecognised recharge offer name: {name!r}"
    rng = re.fullmatch(r"Top up (\d+)-(\d+) OMR", text)
    more = re.fullmatch(r"Top up (\d+) OMR or more", text)
    assert rng or more, f"Unrecognised recharge text: {text!r}"
    return {
        "topup_min_omr": float((rng or more).group(1)),
        "topup_max_omr": float(rng.group(2)) if rng else None,
        "bonus_credit_omr": float(bonus.group(1)),
    }


def main() -> None:
    roots = {cat: json.loads(path.read_text(encoding="utf-8"))[0] for cat, path in SOURCES.items()}
    log: dict[str, list[str]] = {k: [] for k in (
        "plan_id", "merge", "recharge", "international", "correction", "addition",
        "targeting", "possible_duplicate", "needs_verification", "root",
    )}

    # Work on annotated copies so original indices stay addressable.
    work: dict[tuple[str, str], list[dict | None]] = {}
    for cat, root in roots.items():
        for list_name in (MAIN_LIST[cat], "addon_plans"):
            work[(cat, list_name)] = [copy.deepcopy(p) for p in root.get(list_name, [])]

    def plan_at(cat: str, list_name: str, idx: int) -> dict:
        plan = work[(cat, list_name)][idx]
        assert plan is not None, f"{cat} {list_name}[{idx}] already removed"
        return plan

    def loc(cat: str, list_name: str, idx: int) -> str:
        return f"`{cat}` `{list_name}[{idx}]`"

    # Issue 7/9 - corrections (before merges so merged records carry them)
    for cat, list_name, idx, field, expected, new, reason in CORRECTIONS:
        plan = plan_at(cat, list_name, idx)
        assert plan.get(field) == expected, (cat, list_name, idx, field, plan.get(field))
        plan[field] = new
        log["correction"].append(
            f"| {loc(cat, list_name, idx)} | `{plan['plan_name']}` | `{field}` | `{json.dumps(expected)}` | `{json.dumps(new)}` | {reason} |"
        )

    for cat, list_name, idx, field, value, reason in ADDITIONS:
        plan = plan_at(cat, list_name, idx)
        assert field not in plan, (cat, list_name, idx, field)
        plan[field] = value
        log["addition"].append(
            f"| {loc(cat, list_name, idx)} | `{plan['plan_name']}` | `{field}` | `{json.dumps(value)}` | {reason} |"
        )

    # Issue 4 - international minutes
    for (cat, list_name, idx), destinations in INTERNATIONAL.items():
        plan = plan_at(cat, list_name, idx)
        assert plan["voice_minutes"] and plan.get("intl_minutes") is None, (cat, list_name, idx)
        before = (plan["product_type"], plan["voice_minutes"])
        plan["intl_minutes"] = plan["voice_minutes"]
        plan["voice_minutes"] = None
        plan["product_type"] = "IDD"
        plan["intl_destinations"] = destinations
        log["international"].append(
            f"| {loc(cat, list_name, idx)} | `{plan['plan_name']}` | `{before[0]}` → `IDD` | "
            f"voice_minutes `{before[1]}` → `null`; intl_minutes `null` → `{plan['intl_minutes']}` | `{destinations}` |"
        )

    # Issue 11 - targeting fields (before merges)
    for (cat, list_name, idx), fields in TARGETING.items():
        plan = plan_at(cat, list_name, idx)
        plan.update(fields)
        log["targeting"].append(
            f"| {loc(cat, list_name, idx)} | `{plan['plan_name']}` | "
            + ", ".join(f"`{k}`: `{v}`" for k, v in fields.items()) + " |"
        )

    for (cat, list_name, idx), note in POSSIBLE_DUPLICATES.items():
        plan_at(cat, list_name, idx)["data_quality_flags"] = ["POSSIBLE_DUPLICATE"]
        plan_at(cat, list_name, idx)["data_quality_notes"] = [note]
        log["possible_duplicate"].append(f"| {loc(cat, list_name, idx)} | `{plan_at(cat, list_name, idx)['plan_name']}` | {note} |")

    for (cat, list_name, idx), note in NEEDS_VERIFICATION.items():
        plan = plan_at(cat, list_name, idx)
        plan.setdefault("data_quality_flags", []).append("NEEDS_SOURCE_VERIFICATION")
        plan.setdefault("data_quality_notes", []).append(note)
        log["needs_verification"].append(f"| {loc(cat, list_name, idx)} | `{plan['plan_name']}` | {note} |")

    # Issue 2 - merges
    commercial = ("price_omr", "data_gb", "social_pass_gb", "voice_minutes", "intl_minutes",
                  "unlimited_calls", "bonus_data_gb", "type", "product_type")
    for cat, list_name, indices in MERGES:
        keep_idx, *drop_idxs = indices
        keeper = plan_at(cat, list_name, keep_idx)
        aliases = []
        for d in drop_idxs:
            dup = plan_at(cat, list_name, d)
            for field in commercial:
                assert dup.get(field) == keeper.get(field), (cat, list_name, keep_idx, d, field)
            if keeper.get("validity_days") is None and dup.get("validity_days") is not None:
                log["merge"].append(
                    f"| {loc(cat, list_name, keep_idx)} | `{keeper['plan_name']}` | validity_days `null` → "
                    f"`{dup['validity_days']}` taken from duplicate `{dup['plan_name']}` |"
                )
                keeper["validity_days"] = dup["validity_days"]
            if not keeper.get("extra_benefits") and dup.get("extra_benefits"):
                keeper["extra_benefits"] = dup["extra_benefits"]
            aliases.append(dup["plan_name"])
            work[(cat, list_name)][d] = None
        keeper["alias_names"] = aliases
        log["merge"].append(
            f"| {loc(cat, list_name, keep_idx)} | `{keeper['plan_name']}` (kept) | removed: "
            + ", ".join(f"`{list_name}[{d}]` `{a}`" for d, a in zip(drop_idxs, aliases)) + " |"
        )

    # Issue 3 - recharge bonuses
    recharge_offers = []
    for idx in RECHARGE_ADDON_INDICES:
        plan = plan_at("prepaid", "addon_plans", idx)
        assert plan["product_type"] == "UNKNOWN/OTHER" and "Bonus" in plan["plan_name"], plan["plan_name"]
        offer = {
            "offer_id": make_plan_id("prepaid", "Recharge", plan["plan_name"]),
            "offer_name": plan["plan_name"],
            "operator": "vodafone",
            "category": "prepaid",
            "offer_scope": "BTL",
            **parse_recharge(plan),
            "is_promo": plan["is_promo"],
            "extra_benefits": plan["extra_benefits"],
        }
        recharge_offers.append(offer)
        work[("prepaid", "addon_plans")][idx] = None
        log["recharge"].append(
            f"| `prepaid` `addon_plans[{idx}]` | `{plan['plan_name']}` | topup {offer['topup_min_omr']}–"
            f"{offer['topup_max_omr'] if offer['topup_max_omr'] is not None else 'or more'} OMR, bonus {offer['bonus_credit_omr']} OMR |"
        )

    # Build outputs: offer_scope, plan_id, root cleanup
    seen_ids: dict[str, str] = {}
    for cat, root in roots.items():
        out_root = {
            "key": root["key"],
            "operator": root["operator"],
            "category": root["category"],
            "offer_scope": "BTL",
            "source_files": [SOURCES[cat].name],
        }
        lists = {}
        for list_name in (MAIN_LIST[cat], "addon_plans"):
            cleaned = []
            for idx, plan in enumerate(work[(cat, list_name)]):
                if plan is None:
                    continue
                plan["offer_scope"] = "BTL"
                plan["plan_id"] = make_plan_id(cat, plan["type"], plan["plan_name"])
                assert plan["plan_id"] not in seen_ids, (plan["plan_id"], plan["plan_name"], seen_ids.get(plan["plan_id"]))
                seen_ids[plan["plan_id"]] = plan["plan_name"]
                assert plan["product_type"] in ALLOWED_PRODUCT_TYPES, (cat, list_name, idx, plan["product_type"])
                plan["source_location"] = f"{list_name}[{idx}]"
                log["plan_id"].append(f"| {loc(cat, list_name, idx)} | `{plan['plan_name']}` | `{plan['plan_id']}` |")
                cleaned.append(plan)
            lists[list_name] = cleaned

        main_name = MAIN_LIST[cat]
        count_key = "master_plan_count" if cat == "prepaid" else "basic_plan_count"
        out_root["total_plan_count"] = len(lists[main_name]) + len(lists["addon_plans"])
        out_root[count_key] = len(lists[main_name])
        out_root["addon_plan_count"] = len(lists["addon_plans"])
        out_root[main_name] = lists[main_name]
        out_root["addon_plans"] = lists["addon_plans"]
        if cat == "prepaid":
            out_root["recharge_offers"] = recharge_offers
            out_root["recharge_offer_count"] = len(recharge_offers)

        log["root"].append(
            f"| `{cat}` | {root['total_plan_count']} ({len(root.get(main_name, []))} + {len(root['addon_plans'])}) | "
            f"{out_root['total_plan_count']} ({out_root[count_key]} + {out_root['addon_plan_count']})"
            + (f" + {len(recharge_offers)} recharge_offers" if cat == "prepaid" else "")
            + " | `ooredoo_plans` removed |"
        )

        OUTPUTS[cat].write_text(json.dumps([out_root], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    write_log(log)
    print("Wrote", *(str(p.relative_to(ROOT)) for p in OUTPUTS.values()), str(LOG_PATH.relative_to(ROOT)))


def write_log(log: dict[str, list[str]]) -> None:
    sections = [
        "# Vodafone BTL – Fix Log\n",
        "Generated by `scripts/fix_vodafone_btl.py`. Locations refer to the **original** files; "
        "issue numbers refer to `docs/vodafone_btl_data_issues.md`.\n",
        "## Plan counts\n",
        "| Category | Before | After | Root |", "|---|---|---|---|", *log["root"], "",
        "## Issue 2 – Duplicates merged\n",
        "The kept record gets `alias_names` listing the removed names.\n",
        "| Kept | Plan | Change |", "|---|---|---|", *log["merge"], "",
        "## Issue 2 – Possible duplicates kept and flagged (`POSSIBLE_DUPLICATE`)\n",
        "| Location | Plan | Note |", "|---|---|---|", *log["possible_duplicate"], "",
        "## Issue 3 – Recharge bonuses moved to `recharge_offers`\n",
        "| Original location | Name | Structured as |", "|---|---|---|", *log["recharge"], "",
        "## Issue 4 – International minutes\n",
        "| Location | Plan | product_type | Minutes | intl_destinations |", "|---|---|---|---|---|", *log["international"], "",
        "## Issues 7 / 9 – Value corrections\n",
        "| Location | Plan | Field | Before | After | Reason |", "|---|---|---|---|---|---|", *log["correction"], "",
        "## Fields added\n",
        "| Location | Plan | Field | Value | Reason |", "|---|---|---|---|---|", *log["addition"], "",
        "## Issue 11 – BTL targeting fields (derived from the plan's own text)\n",
        "Every plan and both roots also get `\"offer_scope\": \"BTL\"`.\n",
        "| Location | Plan | Fields |", "|---|---|---|", *log["targeting"], "",
        "## Not changed – flagged `NEEDS_SOURCE_VERIFICATION`\n",
        "| Location | Plan | What to confirm |", "|---|---|---|", *log["needs_verification"], "",
        "## Issue 1 – plan_id assigned\n",
        "`plan_id` = first 12 hex chars of SHA-1 of `vodafone|btl|<category>|<type>|<lower-cased plan_name>`. "
        "Each plan also gets `source_location` (its original list and index).\n",
        "| Original location | Plan | plan_id |", "|---|---|---|", *log["plan_id"], "",
    ]
    LOG_PATH.write_text("\n".join(sections) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
