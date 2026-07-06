"""
CSV → Ledger Journal Converter
================================
Reads a parsed transaction CSV and produces a double-entry ledger journal file.

Usage:
    python3 csv_to_ledger.py --bank ambank --input csv/ambank/2025-04.csv
    python3 csv_to_ledger.py --bank maybank --all
    python3 csv_to_ledger.py --bank hsbc --all
    python3 csv_to_ledger.py --bank ambank --all   # writes to ledger/ambank/YYYY-MM.journal

After generating journals, manually uncomment the corresponding include lines
in ledger/main.journal (or run: python3 csv_to_ledger.py --update-main).
"""

import argparse
import csv
import re
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
from categorize import categorize_with_confidence

CURRENCY = config.CURRENCY

# Known own-account patterns — treat as Equity:Transfer regardless of category rule
OWN_ACCOUNT_PATTERNS = re.compile(
    r"DEP TO CARD|LIE ZHI HOU.*TRFR|TRFR.*LIE ZHI HOU|"
    r"TRANSFER.*OWN|OWN.*TRANSFER|IBG.*OWN",
    re.IGNORECASE
)

# Mortgage detection: payment to Wong Fui Yee CIMB account, MYR 3,551/mth
_MORTGAGE_PAYEE = re.compile(r"WONG\s+FUI\s+YEE", re.IGNORECASE)
_MORTGAGE_AMOUNT = 3551.00

# Inter-spouse transfers: money moving between Samuel (LIE ZHI HOU) and
# Fui Yee (WONG FUI YEE) is internal to the household → Equity:Transfer,
# so the combined P&L is not inflated. Applied on the *receiving* person's
# accounts (e.g. Samuel's funding landing in Fui Yee's CIMB).
_SPOUSE_OF = {
    "Samuel": re.compile(r"WONG\s*FUI\s*YEE", re.IGNORECASE),
    "FuiYee": re.compile(r"LIE\s*ZHI\s*HOU|LIE\s*ZHI\s*HO\b", re.IGNORECASE),
}


def inject_person(account: str, owner: str) -> str:
    """Insert the owner segment after the root: Expenses:Groceries → Expenses:FuiYee:Groceries.
    Equity accounts are shared (person-neutral) and left unchanged."""
    parts = account.split(":")
    root = parts[0]
    if root not in ("Assets", "Liabilities", "Expenses", "Income"):
        return account  # Equity:* stays shared
    if len(parts) >= 2 and parts[1] in ("Samuel", "FuiYee"):
        return account  # already namespaced
    tail = ":".join(parts[1:])
    return f"{root}:{owner}:{tail}" if tail else f"{root}:{owner}"


def format_amount(amount: float) -> str:
    """Format a float as 'MYR 1,234.56' or 'MYR -1,234.56'."""
    if amount < 0:
        return f"{CURRENCY} -{abs(amount):,.2f}"
    return f"{CURRENCY} {amount:,.2f}"


def build_entry(txn: dict, bank: str) -> str:
    """
    Build a ledger journal entry string for one transaction.

    Double-entry logic:
    - Bank account (AmBank/Maybank debit = money out):
        Expenses:Category    MYR amount
        Assets:Bank:X       MYR -amount

    - Bank account credit (money in):
        Assets:Bank:X       MYR amount
        Income:Category    MYR -amount

    - Credit card (HSBC debit = purchase, increases liability):
        Expenses:Category          MYR amount
        Liabilities:CreditCard:HSBC  MYR -amount

    - Credit card credit (payment reduces liability):
        Liabilities:CreditCard:HSBC  MYR amount
        Equity:Transfer              MYR -amount
    """
    txn_date = txn["date"]
    desc = txn["description"].strip()
    try:
        amount = float(txn["amount"])
    except (ValueError, KeyError):
        return f"; SKIPPED (bad amount): {txn}\n\n"

    txn_type = txn.get("type", "debit" if amount < 0 else "credit")
    is_debit = (txn_type == "debit")

    own_account_account = config.ACCOUNTS[bank]
    abs_amount = abs(amount)
    owner = config.OWNER[bank]
    kind = config.BANK_KIND[bank]

    # Categorise the counter-account (person-neutral category)
    category, matched = categorize_with_confidence(desc, is_debit=is_debit)
    todo_flag = "" if matched else "  ; TODO: categorise"

    # Override for obvious own-account transfers
    if OWN_ACCOUNT_PATTERNS.search(desc):
        category = "Equity:Transfer"
        todo_flag = ""

    # Inter-spouse transfer: counterparty is the other partner → internal
    # household transfer (Equity:Transfer), so combined P&L isn't inflated.
    spouse_re = _SPOUSE_OF.get(owner)
    if spouse_re and spouse_re.search(desc):
        category = "Equity:Transfer"
        todo_flag = ""

    # Override: mortgage payment to Wong Fui Yee CIMB (exact amount match) — Samuel's savings only
    if (kind == "bank" and owner == "Samuel" and is_debit
            and abs_amount == _MORTGAGE_AMOUNT
            and _MORTGAGE_PAYEE.search(desc)):
        category = "Expenses:Housing:Mortgage"
        todo_flag = ""

    # Inject the owner segment (Equity stays shared)
    category = inject_person(category, owner)

    lines = []
    # Marker: * = cleared
    payee = desc[:70]  # truncate for readability
    lines.append(f"{txn_date} * {payee}")

    if kind == "bank":
        if is_debit:
            # Split mortgage+personal: when a Wong Fui Yee transfer exceeds the mortgage amount,
            # carve out the fixed 3,551 portion and treat the remainder as personal spending.
            if category == "Expenses:Housing:Mortgage" and abs_amount > _MORTGAGE_AMOUNT:
                remainder = round(abs_amount - _MORTGAGE_AMOUNT, 2)
                lines.append(f"    {'Expenses:Housing:Mortgage':<45}  {format_amount(_MORTGAGE_AMOUNT)}")
                lines.append(f"    {'Expenses:Personal':<45}  {format_amount(remainder)}")
                lines.append(f"    {own_account_account:<45}  {format_amount(-abs_amount)}")
            else:
                # Money leaving bank: debit Expense, credit Asset
                lines.append(f"    {category:<45}  {format_amount(abs_amount)}{todo_flag}")
                lines.append(f"    {own_account_account:<45}  {format_amount(-abs_amount)}")
        else:
            # Money entering bank: debit Asset, credit Income
            lines.append(f"    {own_account_account:<45}  {format_amount(abs_amount)}")
            lines.append(f"    {category:<45}  {format_amount(-abs_amount)}{todo_flag}")

    elif kind == "card":
        if is_debit:
            # Purchase on credit card: debit Expense, credit Liability
            lines.append(f"    {category:<45}  {format_amount(abs_amount)}{todo_flag}")
            lines.append(f"    {own_account_account:<45}  {format_amount(-abs_amount)}")
        else:
            # Credit / refund / payment on card: debit Liability, credit Transfer
            lines.append(f"    {own_account_account:<45}  {format_amount(abs_amount)}")
            lines.append(f"    {category:<45}  {format_amount(-abs_amount)}{todo_flag}")

    # Add raw description as comment if description was truncated or cleaned
    if len(desc) > 70 or txn.get("raw"):
        raw = txn.get("raw", "")[:120]
        lines.append(f"    ; raw: {raw}")

    return "\n".join(lines) + "\n\n"


def process_csv(csv_path: Path, bank: str, journal_path: Path):
    """Read CSV, generate ledger entries, write journal file."""
    if not csv_path.exists():
        print(f"  MISSING CSV: {csv_path}")
        return 0

    transactions = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            transactions.append(row)

    journal_path.parent.mkdir(parents=True, exist_ok=True)

    header = (
        f"; Generated by csv_to_ledger.py on {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"; Source: {csv_path.name}\n"
        f"; Bank:   {bank}  |  Account: {config.ACCOUNTS[bank]}\n"
        f"; Transactions: {len(transactions)}\n\n"
    )

    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(header)
        for txn in transactions:
            f.write(build_entry(txn, bank))

    # Count uncategorised
    uncategorized = sum(
        1 for t in transactions
        if categorize_with_confidence(t["description"], float(t.get("amount", 0)) < 0)[0] == "Expenses:Uncategorized"
    )
    print(f"  Wrote {len(transactions)} entries → {journal_path}  ({uncategorized} uncategorised)")
    return len(transactions)


def process_all(bank: str):
    """Process all CSVs for a bank."""
    if bank == "ambank":
        months = [(2025, m) for m in range(4, 13)] + [(2026, m) for m in range(1, 6)]
        csv_fn = config.ambank_csv_path
        jrn_fn = config.ambank_journal_path
    elif bank == "maybank":
        months = [(2025, m) for m in range(3, 13)] + [(2026, m) for m in range(1, 6)]
        csv_fn = config.maybank_csv_path
        jrn_fn = config.maybank_journal_path
    elif bank == "hsbc":
        months = [(2025, m) for m in range(9, 13)] + [(2026, m) for m in range(1, 7)]
        csv_fn = config.hsbc_csv_path
        jrn_fn = config.hsbc_journal_path
    elif bank == "ambcc":
        months = [(2025, m) for m in range(10, 13)] + [(2026, m) for m in range(1, 7)]
        csv_fn = config.ambcc_csv_path
        jrn_fn = config.ambcc_journal_path
    elif bank.startswith("fy_"):
        sub = bank[3:]  # cimb, cimbcc, hsbc, rhb, uob
        csv_dir = config.CSV_DIR / "fuiyee" / sub
        months = []
        for p in sorted(csv_dir.glob("*.csv")):
            ym = p.stem  # YYYY-MM
            months.append((int(ym[:4]), int(ym[5:7])))
        csv_fn = lambda y, m, _s=sub: config.fy_csv_path(_s, y, m)
        jrn_fn = lambda y, m, _s=sub: config.fy_journal_path(_s, y, m)
    else:
        print(f"Unknown bank: {bank}")
        sys.exit(1)

    total = 0
    for y, m in months:
        csv_path = csv_fn(y, m)
        jrn_path = jrn_fn(y, m)
        if csv_path.exists():
            print(f"Converting {csv_path.name} ...")
            total += process_csv(csv_path, bank, jrn_path)
        else:
            print(f"  SKIP (no CSV): {csv_path.name}")
    print(f"Total: {total} journal entries written for {bank}.")


def update_main_journal():
    """Uncomment include lines in main.journal for journals that now exist."""
    main = config.MAIN_JOURNAL
    text = main.read_text(encoding="utf-8")
    updated = 0
    new_lines = []
    for line in text.splitlines():
        # Look for commented-out includes: "; include ambank/2025-04.journal"
        m = re.match(r"^;\s*(include\s+(\S+\.journal))", line)
        if m:
            include_line = m.group(1)
            rel_path = m.group(2)
            full_path = config.LEDGER_DIR / rel_path
            if full_path.exists():
                new_lines.append(include_line)  # uncomment it
                updated += 1
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    main.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"Updated main.journal: {updated} includes activated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert parsed CSVs to ledger journal files")
    parser.add_argument("--bank", choices=[
        "ambank", "maybank", "hsbc", "ambcc",
        "fy_cimb", "fy_cimbcc", "fy_hsbc", "fy_rhb", "fy_uob",
    ], required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--input", help="Path to a single CSV file")
    group.add_argument("--all", action="store_true", help="Process all CSVs for this bank")
    parser.add_argument("--output", help="Output journal path (single-file mode only)")
    parser.add_argument("--update-main", action="store_true", help="Uncomment includes in main.journal for existing journals")
    args = parser.parse_args()

    if args.update_main:
        update_main_journal()
    elif args.all:
        process_all(args.bank)
    elif args.input:
        out = Path(args.output) if args.output else None
        if out is None:
            # Derive from input filename
            p = Path(args.input)
            ym = p.stem  # e.g. 2025-04
            y, m = int(ym[:4]), int(ym[5:7])
            if args.bank == "ambank":   out = config.ambank_journal_path(y, m)
            elif args.bank == "maybank": out = config.maybank_journal_path(y, m)
            elif args.bank == "hsbc":   out = config.hsbc_journal_path(y, m)
            elif args.bank == "ambcc":  out = config.ambcc_journal_path(y, m)
            elif args.bank.startswith("fy_"): out = config.fy_journal_path(args.bank[3:], y, m)
        process_csv(Path(args.input), args.bank, out)
    else:
        parser.print_help()
