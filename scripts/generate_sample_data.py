"""Synthetic sample data generator for Data Refinery DWH pipeline.

Generates realistic master files and transaction data across P&L, inventory, and market pricing
with 100+ cost accounts, multi-entity records, and test flags.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path


def generate_sample_data(base_dir: Path | str = ".", inject_unmapped: bool = False) -> None:
    base = Path(base_dir).resolve()
    masters_dir = base / "data" / "01_raw" / "masters"
    pnl_dir = base / "data" / "01_raw" / "pnl"
    inv_dir = base / "data" / "01_raw" / "inventory"
    price_dir = base / "data" / "01_raw" / "price"

    for d in [masters_dir, pnl_dir, inv_dir, price_dir]:
        d.mkdir(parents=True, exist_ok=True)

    random.seed(42)

    # 1. Official Master Customers (Leading zeros preserved)
    customers = [
        ("000100200301", "MediaExpert Polska", "Retail", "PL", "Warszawa"),
        ("000100200302", "RTV Euro AGD", "Retail", "PL", "Gdansk"),
        ("000100200303", "Allegro Direct PL", "Online", "PL", "Poznan"),
        ("000100200304", "Komputronik", "Online", "PL", "Wroclaw"),
        ("000100200305", "Neonet PL", "Retail", "PL", "Wroclaw"),
        ("000100200306", "Carrefour Polska", "Hypermarket", "PL", "Krakow"),
    ]
    with open(masters_dir / "master_customers.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bill_to_code", "customer_name", "channel_group", "country_code", "city"])
        w.writerows(customers)

    # 2. Official Master Products
    products = [
        ("OLED65C6P", "OLED TV 65 C6", "HE", "OLED TV"),
        ("QNED55B6P", "QNED TV 55 B6", "HE", "QNED TV"),
        ("WM9000PL", "Eco Washer 9kg", "HA", "Washing Machine"),
        ("REF8000PL", "InstaView Refrigerator", "HA", "Refrigerator"),
        ("AUD300PL", "Soundbar 3.1ch", "HE", "Audio"),
        ("AC5000PL", "Dual Inverter AC", "RAC", "Air Conditioner"),
    ]
    with open(masters_dir / "master_products.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model_code", "product_name", "division", "category"])
        w.writerows(products)

    # 3. 100+ Cost/Revenue Account Codes
    rev_accounts = [f"REV_{i:03d}" for i in range(1, 11)]
    ded_accounts = [f"DED_{i:03d}" for i in range(1, 16)]
    cog_accounts = [f"COG_{i:03d}" for i in range(1, 21)]
    log_accounts = [f"LOG_{i:03d}" for i in range(1, 16)]
    prm_accounts = [f"PRM_{i:03d}" for i in range(1, 21)]
    war_accounts = [f"WAR_{i:03d}" for i in range(1, 11)]
    fix_accounts = [f"FIX_{i:03d}" for i in range(1, 21)]
    subtotal_cols = ["CALC_GROSS_PROFIT", "CALC_NET_SALES", "CALC_CONTRIBUTION_MARGIN", "CALC_OPERATING_PROFIT"]

    all_acc_cols = rev_accounts + ded_accounts + cog_accounts + log_accounts + prm_accounts + war_accounts + fix_accounts + subtotal_cols

    # 4. Raw ERP P&L (Global Data: PL, DE, CZ)
    pnl_headers = [
        "period", "entity_code", "bill_to_code", "model_code", "currency_code", "sales_qty"
    ] + all_acc_cols

    periods = ["2026-01", "2026-02", "2026-03"]
    entities = ["PL", "PL", "PL", "PL", "DE", "CZ"]  # Weighted towards PL
    currencies = ["PLN", "PLN", "EUR"]

    rows = []
    for period in periods:
        for cust in customers:
            for prod in products:
                entity = random.choice(entities)
                curr = "EUR" if entity == "DE" else ("CZK" if entity == "CZ" else random.choice(currencies))
                qty = random.randint(10, 500)

                # Generate amounts
                rev_sum = 0.0
                acc_vals = {}
                for acc in rev_accounts:
                    v = round(random.uniform(5000, 80000), 2)
                    acc_vals[acc] = v
                    rev_sum += v

                ded_sum = 0.0
                for acc in ded_accounts:
                    v = round(random.uniform(100, 3000), 2)
                    acc_vals[acc] = v
                    ded_sum += v

                cog_sum = 0.0
                for acc in cog_accounts:
                    v = round(random.uniform(1000, 15000), 2)
                    acc_vals[acc] = v
                    cog_sum += v

                log_sum = 0.0
                for acc in log_accounts:
                    v = round(random.uniform(50, 2000), 2)
                    acc_vals[acc] = v
                    log_sum += v

                prm_sum = 0.0
                for acc in prm_accounts:
                    v = round(random.uniform(100, 2500), 2)
                    acc_vals[acc] = v
                    prm_sum += v

                war_sum = 0.0
                for acc in war_accounts:
                    v = round(random.uniform(50, 1000), 2)
                    acc_vals[acc] = v
                    war_sum += v

                fix_sum = 0.0
                for acc in fix_accounts:
                    v = round(random.uniform(100, 3000), 2)
                    acc_vals[acc] = v
                    fix_sum += v

                # Subtotals
                acc_vals["CALC_NET_SALES"] = round(rev_sum - ded_sum, 2)
                acc_vals["CALC_GROSS_PROFIT"] = round(acc_vals["CALC_NET_SALES"] - cog_sum, 2)
                acc_vals["CALC_CONTRIBUTION_MARGIN"] = round(acc_vals["CALC_GROSS_PROFIT"] - log_sum - prm_sum - war_sum, 2)
                acc_vals["CALC_OPERATING_PROFIT"] = round(acc_vals["CALC_CONTRIBUTION_MARGIN"] - fix_sum, 2)

                row = [
                    period, entity, cust[0], prod[0], curr, qty
                ] + [acc_vals[c] for c in all_acc_cols]
                rows.append(row)

    if inject_unmapped:
        # Inject deliberate unmapped codes for strict Fail-Fast verification
        rows.append([
            "2026-03", "PL", "999999999999", "UNKNOWN_MODEL_X", "PLN", 100
        ] + [1000.0] * len(all_acc_cols))

    with open(pnl_dir / "raw_pnl_2026_q1.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(pnl_headers)
        w.writerows(rows)

    # 5. Raw Inventory Data
    inv_headers = [
        "period", "entity_code", "warehouse_code", "warehouse_name", "model_code",
        "currency_code", "ending_qty", "ending_inventory_val", "dio_days", "warehouse_logistics_cost"
    ]
    warehouses = [("WAW_DC1", "Warsaw Central DC"), ("WRO_DC2", "Wroclaw Regional DC")]
    inv_rows = []
    for period in periods:
        for wh in warehouses:
            for prod in products:
                entity = "PL"
                curr = "PLN"
                end_qty = random.randint(50, 1200)
                end_val = round(end_qty * random.uniform(800, 3500), 2)
                dio = random.randint(15, 65)
                wh_cost = round(end_qty * random.uniform(10, 45), 2)
                inv_rows.append([
                    period, entity, wh[0], wh[1], prod[0], curr, end_qty, end_val, dio, wh_cost
                ])

    with open(inv_dir / "raw_inventory.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(inv_headers)
        w.writerows(inv_rows)

    # 6. Raw Market Price Data
    price_headers = [
        "period", "entity_code", "channel_code", "channel_name", "model_code",
        "currency_code", "our_price", "competitor_price", "market_benchmark_price"
    ]
    channels = [("CH_RETAIL", "Offline Retail Market"), ("CH_ONLINE", "E-Commerce Market")]
    price_rows = []
    for period in periods:
        for ch in channels:
            for prod in products:
                entity = "PL"
                curr = "PLN"
                base_p = random.uniform(1800, 7500)
                our_p = round(base_p, 2)
                comp_p = round(base_p * random.uniform(0.92, 1.08), 2)
                bm_p = round((our_p + comp_p) / 2.0, 2)
                price_rows.append([
                    period, entity, ch[0], ch[1], prod[0], curr, our_p, comp_p, bm_p
                ])

    with open(price_dir / "raw_market_price.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(price_headers)
        w.writerows(price_rows)

    print(f"Sample data generated successfully at {base / 'data' / '01_raw'}")
    print(f" - Master customers: {len(customers)}")
    print(f" - Master products: {len(products)}")
    print(f" - P&L records: {len(rows)} (100+ account columns)")
    print(f" - Inventory records: {len(inv_rows)}")
    print(f" - Price records: {len(price_rows)}")
    if inject_unmapped:
        print(" [!] Injected 1 unmapped code record for Fail-Fast verification.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic sample data for DWH pipeline.")
    parser.add_argument("--base-dir", default=".", help="Base directory of the repository")
    parser.add_argument("--inject-unmapped", action="store_true", help="Inject unmapped customer/product codes to test fail-fast")
    args = parser.parse_args()

    generate_sample_data(base_dir=args.base_dir, inject_unmapped=args.inject_unmapped)
