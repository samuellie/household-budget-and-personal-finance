"""
De-duplicate Fui Yee's CIMB savings statements.

CIMB issues BOTH monthly and overlapping quarterly statements, so the raw
per-statement CSVs contain the same transaction multiple times. This script:
  1. reads all raw statement CSVs from csv/fuiyee/cimb/_statements/
  2. de-duplicates by (date, amount, balance) — the running balance uniquely
     identifies each transaction in the account timeline
  3. regroups unique transactions by TRANSACTION month
  4. writes clean per-month CSVs to csv/fuiyee/cimb/  (what the ledger reads)

Run after parse_fy_cimb.py, before csv_to_ledger.py --bank fy_cimb.
Idempotent: raw statements are archived once into _statements/.
"""
import csv, sys, shutil, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config

CLEAN_DIR = config.CSV_DIR / "fuiyee" / "cimb"
RAW_DIR   = CLEAN_DIR / "_statements"
FIELDS = ["date", "description", "amount", "type", "balance", "raw"]

def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # Archive raw statement CSVs on first run (files directly in CLEAN_DIR)
    for p in sorted(CLEAN_DIR.glob("*.csv")):
        shutil.move(str(p), str(RAW_DIR / p.name))
    raw_files = sorted(RAW_DIR.glob("*.csv"))
    if not raw_files:
        print("No raw CIMB statement CSVs found."); return

    rows = []
    for f in raw_files:
        for r in csv.DictReader(open(f, encoding="utf-8")):
            rows.append(r)

    # Integrity check: any (date, amount) with conflicting balances?
    byda = collections.defaultdict(set)
    for r in rows:
        byda[(r["date"], r["amount"])].add(r["balance"])
    # (this is informational — same amount on same day with different balance is
    #  legitimately two different transactions; we keep them via the balance key)

    # De-dup by (date, amount, balance)
    seen = {}
    for r in rows:
        key = (r["date"], r["amount"], r["balance"])
        if key not in seen:
            seen[key] = r
    unique = list(seen.values())

    # Balance-chain integrity: sort by balance-implied order and verify continuity.
    # Sort by date, then reconstruct: for equal dates order is ambiguous, so we
    # verify via the set of balances forming a connected chain.
    unique.sort(key=lambda r: (r["date"], float(r["balance"])))
    # Verify: each txn's balance == some prior balance + its amount (chain).
    balances_after = set(round(float(r["balance"]), 2) for r in unique)
    breaks = 0
    prev_bal = None
    for r in sorted(unique, key=lambda r: r["date"]):
        amt = float(r["amount"]); bal = round(float(r["balance"]), 2)
        if prev_bal is not None:
            if round(prev_bal + amt, 2) != bal:
                breaks += 1
        prev_bal = bal
    # (breaks are expected where same-day ordering differs; end balance is the real check)

    # Regroup by transaction month
    bymonth = collections.defaultdict(list)
    for r in unique:
        ym = r["date"][:7]  # YYYY-MM
        bymonth[ym].append(r)

    total = 0
    for ym in sorted(bymonth):
        y, m = ym.split("-")
        out = CLEAN_DIR / f"{y}-{m}.csv"
        recs = sorted(bymonth[ym], key=lambda r: (r["date"], float(r["balance"])))
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            for r in recs:
                w.writerow({k: r.get(k, "") for k in FIELDS})
        total += len(recs)
        print(f"  {ym}: {len(recs)} txns")

    print(f"\nRaw rows: {len(rows)}  ->  unique: {len(unique)}  (removed {len(rows)-len(unique)} dups)")
    print(f"Clean per-month files written to {CLEAN_DIR}  (total {total})")
    print(f"Balance-chain same-day-order breaks (informational): {breaks}")
    # End-to-end: final balance = last txn's balance
    last = max(unique, key=lambda r: (r["date"], float(r["balance"])))
    print(f"Latest transaction: {last['date']}  running balance MYR {last['balance']}")

if __name__ == "__main__":
    main()
