"""Build clean Omantel BTL catalogue + performance CSVs from the raw BTL exports.

Reads (never modifies):
    data/omantel/btl/_INFO - BTL CATALOGUE.csv
    data/omantel/btl/_INFO - subscription_data_new.csv
    data/omantel/PREPAID_PRODUCT_CATALOG.csv   (column layout + old-ID overlap check only)

Writes:
    data/omantel/btl/PREPAID_BTL_PRODUCT_CATALOG.csv      paid BTL products (matched + risk-scored)
    data/omantel/btl/PRODUCT_PERFORMANCE_BTL.csv          their usage, 2026-07..2026-09
    data/omantel/btl/BTL_FREE_OFFERS.csv                  free BTL products (listed, not scored)
    data/omantel/btl/BTL_FREE_OFFERS_PERFORMANCE.csv      their usage, 2026-07..2026-09
    docs/omantel_btl_build_log.md

Rules (agreed decisions, see docs/btl_design_plan.md):
- Only catalogue products that have subscription data are kept.
- Test / complaint / training / internal products are removed.
- PRODUCT_GROUP_NAME -> product_type: Data Offer -> DATA, Combo/Hybrid Offer -> COMBO,
  Voice Offer -> VOICE, IDD/ILD -> IDD, Social Media Offers/Youtube Offer -> DATA;
  every other group is excluded.
- Role: COMBO -> Master (main plan); everything else -> Addon.
- Price 0 -> free offer: written to the free-offers files, not to the scored catalogue.
- Usage window: 2026-07, 2026-08, 2026-09 (2026-10 is a partial month and is dropped).

Output catalogues use exactly the column layout of PREPAID_PRODUCT_CATALOG.csv and
follow its conventions: unit_in_mb in MB, data in GB, minutes in units_minutes and
min, IDD minutes in units_minutes and idd.

Usage:
    python scripts/build_omantel_btl.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BTL_DIR = ROOT / "data" / "omantel" / "btl"
RAW_CATALOGUE = BTL_DIR / "_INFO - BTL CATALOGUE.csv"
RAW_USAGE = BTL_DIR / "_INFO - subscription_data_new.csv"
ATL_PREPAID = ROOT / "data" / "omantel" / "PREPAID_PRODUCT_CATALOG.csv"

OUT_CATALOGUE = BTL_DIR / "PREPAID_BTL_PRODUCT_CATALOG.csv"
OUT_PERFORMANCE = BTL_DIR / "PRODUCT_PERFORMANCE_BTL.csv"
OUT_FREE_CATALOGUE = BTL_DIR / "BTL_FREE_OFFERS.csv"
OUT_FREE_PERFORMANCE = BTL_DIR / "BTL_FREE_OFFERS_PERFORMANCE.csv"
LOG_PATH = ROOT / "docs" / "omantel_btl_build_log.md"

USAGE_MONTHS = ["2026-07", "2026-08", "2026-09"]
DROPPED_MONTHS = ["2026-10"]

NON_PRODUCT_PATTERN = r"test|training|tech_|dummy|asma|complaint|pcrf"

GROUP_TO_PRODUCT_TYPE = {
    "Data Offer": "DATA",
    "Combo/Hybrid Offer": "COMBO",
    "Voice Offer": "VOICE",
    "IDD": "IDD",
    "ILD": "IDD",
    "Social Media Offers": "DATA",
    "Youtube Offer": "DATA",
}

IDD_DESTINATIONS = {"PAK": "Pakistan", "India": "India", "Bangla": "Bangladesh"}

PERFORMANCE_COLUMNS = [
    "product_id", "product_name", "price", "month",
    "number_of_purchases", "unique_customers", "total_revenue", "arpu",
]


def name_data_mb(name: str) -> float | None:
    """Data amount stated in a product name, in MB (1GB = 1024MB)."""

    match = re.search(r"(\d+(?:\.\d+|Dot\d+)?)\s*(GB|MB|G)(?![a-z])", name, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace("Dot", "."))
    return value * 1024 if match.group(2).upper() in ("GB", "G") else value


def name_price(name: str) -> float | None:
    """Price stated in a product name ('RO4', '4RO'), if any."""

    match = re.search(r"RO\s?(\d+(?:\.\d+)?)(?![\d.])|(\d+(?:\.\d+)?)RO", name)
    if not match:
        return None
    return float(match.group(1) or match.group(2))


def derived_benefits(name: str, product_type: str) -> str | None:
    """Benefit notes stated in the product name, for the LLM / audit trail."""

    notes = []
    if product_type == "IDD":
        for token, country in IDD_DESTINATIONS.items():
            if f"_{token}_" in name:
                notes.append(f"IDD destination: {country}")
    if "NIGHT" in name.upper():
        notes.append("Night-time data only")
    if "FLEXMIN" in name.upper():
        notes.append("Flexi minutes")
    if "Bengali" in name:
        notes.append("Targeted at Bengali-speaking customers")
    if re.search(r"_LOC_", name):
        notes.append("Local minutes")
    if name.upper().startswith("KHAREEF"):
        notes.append("Khareef seasonal offer")
    if name.startswith("Revival"):
        notes.append("Revival (win-back) offer")
    return "; ".join(notes) or None


def to_catalogue_row(raw: pd.Series, columns: list[str]) -> dict:
    """Map one raw BTL row onto the PREPAID_PRODUCT_CATALOG.csv column layout."""

    product_type = GROUP_TO_PRODUCT_TYPE[raw["PRODUCT_GROUP_NAME"]]
    data_mb = round(float(raw["Data_benefits"]) / 1024, 5)
    minutes = float(raw["Voice_benefits"])
    is_idd = product_type == "IDD"

    row = {col: None for col in columns}
    row.update({
        "id": int(raw["ID"]),
        "bundle_type": raw["PRODUCT_GROUP_NAME"],
        "product_id": str(int(raw["ID"])),
        "product_name": raw["PRODUCT_NAME"],
        "price": float(raw["Price"]),
        "product_type": product_type,
        "unit_in_mb": data_mb,
        "validity_in_days": float(raw["Validity"]),
        "type": "Master" if product_type == "COMBO" else "Addon",
        "units_minutes": minutes,
        "units_sms": float(raw["SMS_benefits"]),
        "data": round(data_mb / 1024, 3),
        "data_social": 0.0,
        "data_roaming": 0.0,
        "min": 0.0 if is_idd else minutes,
        "min_flex": 0.0,
        "idd": minutes if is_idd else 0.0,
        "owner": "Omantel",
        "ui_product_id": int(raw["UPC_PRODUCT"]),
        "offer_type": "BTL",
        "product_status": "active",
        "extra_benefits": derived_benefits(raw["PRODUCT_NAME"], product_type),
    })
    return row


def main() -> None:
    raw = pd.read_csv(RAW_CATALOGUE, encoding="utf-8-sig")
    usage = pd.read_csv(RAW_USAGE, encoding="utf-8-sig")
    atl = pd.read_csv(ATL_PREPAID, encoding="utf-8-sig")
    columns = list(atl.columns)
    log: dict[str, list[str]] = {k: [] for k in ("counts", "excluded", "checks", "ids", "kept", "free")}

    # Usage names must all exist in the catalogue (exact match, verified 138/138).
    unknown = set(usage["product_name"]) - set(raw["PRODUCT_NAME"])
    assert not unknown, f"Usage rows with no catalogue product: {sorted(unknown)}"
    assert not usage.duplicated(["product_name", "month"]).any(), "duplicate (product, month) usage rows"

    # 1. Keep only products that have subscription data
    with_usage = raw[raw["PRODUCT_NAME"].isin(usage["product_name"])].copy()
    no_usage = raw[~raw["PRODUCT_NAME"].isin(usage["product_name"])]

    # 2. Remove test / complaint / training / internal products
    non_product = with_usage["PRODUCT_NAME"].str.contains(NON_PRODUCT_PATTERN, case=False)
    removed_non_product = with_usage[non_product]
    with_usage = with_usage[~non_product]

    # 3. Remove product groups outside the approved mapping
    unmapped = ~with_usage["PRODUCT_GROUP_NAME"].isin(GROUP_TO_PRODUCT_TYPE)
    removed_group = with_usage[unmapped]
    with_usage = with_usage[~unmapped]

    assert not with_usage["ID"].duplicated().any(), "duplicate ID among kept products"
    assert (with_usage["validity_category"] == "Days").all(), "kept product with non-day validity"
    assert (with_usage["Price"] >= 0).all(), "kept product with negative price"

    # 4. Paid vs free
    paid = with_usage[with_usage["Price"] > 0]
    free = with_usage[with_usage["Price"] == 0]

    # 5. Consistency checks: name vs data / price (paid products only; any failure stops the build)
    for _, r in paid.iterrows():
        mb = name_data_mb(r["PRODUCT_NAME"])
        if mb is not None:
            assert abs(mb - r["Data_benefits"] / 1024) < 1, (r["PRODUCT_NAME"], mb, r["Data_benefits"])
        price = name_price(r["PRODUCT_NAME"])
        if price is not None:
            assert abs(price - r["Price"]) < 0.01, (r["PRODUCT_NAME"], price, r["Price"])
    log["checks"].append(f"- Data amount in the name matches `Data_benefits` for all {len(paid)} paid products (KB ÷ 1024 = MB).")
    log["checks"].append(f"- Price in the name matches `Price` for all {len(paid)} paid products where the name states a price.")
    log["checks"].append("- All kept products have `validity_category = Days`, a non-negative price and a unique `ID`.")
    log["checks"].append(f"- All {usage['product_name'].nunique()} products in the subscription file exist in the catalogue (exact name match); no duplicate (product, month) rows.")

    blank_line = with_usage[with_usage["LineType"].isna()]
    for _, r in blank_line.iterrows():
        log["checks"].append(f"- `{r['PRODUCT_NAME']}` (ID {r['ID']}) has no `LineType`; treated as **prepaid** (134 of 135 products with usage are prepaid).")
    assert (with_usage["LineType"].fillna("Prepaid") == "Prepaid").all(), "kept postpaid product - no postpaid output defined"

    # Old-catalogue ID overlap (IDs reused with different names)
    old_btl = atl[atl["offer_type"] == "BTL"]
    old_btl = old_btl.set_index(old_btl["product_id"].astype(str))
    for _, r in paid.iterrows():
        pid = str(int(r["ID"]))
        if pid in old_btl.index:
            old = old_btl.loc[pid]
            log["ids"].append(f"| {pid} | `{r['PRODUCT_NAME']}` | `{old['product_name']}` | {old['product_status']} |")

    # 6. Write catalogues
    cat_rows = [to_catalogue_row(r, columns) for _, r in paid.iterrows()]
    free_rows = [to_catalogue_row(r, columns) for _, r in free.iterrows()]
    pd.DataFrame(cat_rows, columns=columns).to_csv(OUT_CATALOGUE, index=False)
    pd.DataFrame(free_rows, columns=columns).to_csv(OUT_FREE_CATALOGUE, index=False)

    # 7. Write performance (Jul-Sep only; price taken from the catalogue because the source column is blank)
    def performance(products: pd.DataFrame) -> pd.DataFrame:
        u = usage[usage["product_name"].isin(products["PRODUCT_NAME"]) & usage["month"].isin(USAGE_MONTHS)]
        u = u.merge(products[["PRODUCT_NAME", "ID", "Price"]], left_on="product_name", right_on="PRODUCT_NAME")
        u["product_id"] = u["ID"].astype(int).astype(str)
        u["price"] = u["Price"]
        return u[PERFORMANCE_COLUMNS].sort_values(["product_id", "month"])

    perf = performance(paid)
    free_perf = performance(free)
    perf.to_csv(OUT_PERFORMANCE, index=False)
    free_perf.to_csv(OUT_FREE_PERFORMANCE, index=False)

    months = perf.groupby("product_id")["month"].nunique()
    full = months[months == len(USAGE_MONTHS)].index
    dropped_oct = usage["month"].isin(DROPPED_MONTHS).sum()

    # Log
    log["counts"] += [
        f"| Raw catalogue | {len(raw)} |",
        f"| Products with no subscription data (excluded) | {len(no_usage)} |",
        f"| Test / complaint / training products with usage (excluded) | {len(removed_non_product)} |",
        f"| Product group outside the mapping (excluded) | {len(removed_group)} |",
        f"| **Paid products → `PREPAID_BTL_PRODUCT_CATALOG.csv`** | **{len(paid)}** |",
        f"| **Free products → `BTL_FREE_OFFERS.csv`** | **{len(free)}** |",
        f"| Paid products with all 3 months (risk-scorable) | {len(full)} |",
        f"| Usage rows for 2026-10 dropped (partial month) | {dropped_oct} |",
        f"| Usage rows written (paid / free) | {len(perf)} / {len(free_perf)} |",
    ]
    for _, r in removed_non_product.iterrows():
        log["excluded"].append(f"| {r['ID']} | `{r['PRODUCT_NAME']}` | Test / complaint / internal |")
    for _, r in removed_group.iterrows():
        log["excluded"].append(f"| {r['ID']} | `{r['PRODUCT_NAME']}` | Group `{r['PRODUCT_GROUP_NAME']}` not in the product-type mapping |")
    for row in cat_rows:
        pid = row["product_id"]
        n = int(months.get(pid, 0))
        log["kept"].append(
            f"| {pid} | `{row['product_name']}` | {row['type']} | {row['product_type']} | {row['price']:g} | "
            f"{row['data']:g} | {row['units_minutes']:g} | {row['validity_in_days']:g} | {n}/3 | {row['extra_benefits'] or ''} |"
        )
    free_months = free_perf.groupby("product_id")["month"].nunique()
    for row in free_rows:
        log["free"].append(
            f"| {row['product_id']} | `{row['product_name']}` | {row['product_type']} | {row['data']:g} | "
            f"{row['units_minutes']:g} | {row['validity_in_days']:g} | {int(free_months.get(row['product_id'], 0))}/3 |"
        )

    write_log(log)
    print(f"paid={len(paid)} free={len(free)} scorable={len(full)} -> {OUT_CATALOGUE.name}, {OUT_PERFORMANCE.name}, "
          f"{OUT_FREE_CATALOGUE.name}, {OUT_FREE_PERFORMANCE.name}, {LOG_PATH.relative_to(ROOT)}")


def write_log(log: dict[str, list[str]]) -> None:
    lines = [
        "# Omantel BTL – Build Log\n",
        "Generated by `scripts/build_omantel_btl.py` from `data/omantel/btl/_INFO - BTL CATALOGUE.csv` and "
        "`data/omantel/btl/_INFO - subscription_data_new.csv` (both unchanged).\n",
        "## Counts\n", "| Step | Count |", "|---|---|", *log["counts"], "",
        "## Field mapping (raw → catalogue layout)\n",
        "| Output column | Source / rule |", "|---|---|",
        "| `product_id`, `id` | `ID` |", "| `ui_product_id` | `UPC_PRODUCT` |",
        "| `product_name` | `PRODUCT_NAME` |", "| `price` | `Price` |",
        "| `product_type` | `PRODUCT_GROUP_NAME` via the approved mapping |",
        "| `type` | `Master` if COMBO, else `Addon` |", "| `bundle_type` | `PRODUCT_GROUP_NAME` |",
        "| `unit_in_mb` | `Data_benefits` ÷ 1024 (source is KB) |", "| `data` | `unit_in_mb` ÷ 1024 (GB) |",
        "| `units_minutes` | `Voice_benefits` |", "| `min` | `Voice_benefits` (0 for IDD) |",
        "| `idd` | `Voice_benefits` for IDD products, else 0 |", "| `units_sms` | `SMS_benefits` |",
        "| `validity_in_days` | `Validity` |", "| `offer_type` / `product_status` | `BTL` / `active` |",
        "| `extra_benefits` | Notes taken from the product name (IDD destination, night data, flexi, segment, season) |",
        "| Performance `price` | Catalogue `Price` (the source `price` column is blank) |", "",
        "## Checks passed\n", *log["checks"], "",
        "## Excluded products that had usage\n", "| ID | Product | Reason |", "|---|---|---|", *log["excluded"], "",
        "## IDs that also exist in the old ATL catalogue file as BTL rows\n",
        "The new export is used as the source of truth. These old rows are not used for BTL.\n",
        "| ID | New name | Old name | Old status |", "|---|---|---|---|", *log["ids"], "",
        "## Paid products (scored)\n",
        "| product_id | Name | Role | Type | Price | Data GB | Minutes | Validity | Months | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|", *log["kept"], "",
        "## Free products (listed, not scored)\n",
        "| product_id | Name | Type | Data GB | Minutes | Validity | Months |", "|---|---|---|---|---|---|---|", *log["free"], "",
    ]
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
