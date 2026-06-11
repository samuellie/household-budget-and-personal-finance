"""
AmBank PDF Statement Parser
============================
Extracts transactions from AmBank account statement PDFs and writes CSV.

AmBank statement format:
  - Columns: DATE (DDMon), TRANSACTION (multi-line), CHEQUE NO, DEBIT, CREDIT, BALANCE
  - Account summary header: OPENING BALANCE, TOTAL DEBITS, TOTAL CREDITS, CLOSING BALANCE
  - Date format: DDMon (e.g. "01Apr", "26Apr")

Usage:
    python3 parse_ambank.py Bank\ Statements/Samuel/Ambank/2025/AMB-PDF-2025-04.pdf
    python3 parse_ambank.py --all        # process every AmBank PDF on file
    python3 parse_ambank.py --year 2025  # process one year
"""

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber

# Add scripts dir to path so config is importable from anywhere
sys.path.insert(0, str(Path(__file__).parent))
import config

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}

# Date pattern: DDMon at start of line (e.g. "01Apr", "26Apr")
DATE_RE = re.compile(r"^(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", re.IGNORECASE)
# Amount: digits with optional commas, 2 decimal places
AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")


def parse_statement(pdf_path: Path) -> dict:
    """
    Parse a single AmBank PDF. Returns:
      {
        "account": str,
        "period_start": date,
        "period_end": date,
        "opening_balance": float,
        "closing_balance": float,
        "total_debits": float,
        "total_credits": float,
        "transactions": [{"date", "description", "amount", "balance", "type"}]
      }
    """
    # Infer year and month from filename: AMB-PDF-YYYY-MM.pdf
    stem = pdf_path.stem  # e.g. AMB-PDF-2025-04
    parts = stem.split("-")
    stmt_year = int(parts[2])
    stmt_month = int(parts[3])

    all_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_text.append(text)

    full_text = "\n".join(all_text)
    lines = full_text.splitlines()

    # ── Parse header summary ──────────────────────────────────────────────────
    opening = closing = total_debits = total_credits = 0.0
    account_no = ""
    period_start = period_end = None

    for line in lines:
        if "ACCOUNT NO" in line.upper() or "NO. AKAUN" in line.upper():
            m = re.search(r":\s*([\d]+)", line)
            if m:
                account_no = m.group(1)
        if "STATEMENT DATE" in line.upper() or "TARIKH PENYATA" in line.upper():
            m = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", line)
            if m:
                from datetime import datetime
                period_start = datetime.strptime(m.group(1), "%d/%m/%Y").date()
                period_end   = datetime.strptime(m.group(2), "%d/%m/%Y").date()
        if "OPENING BALANCE" in line.upper() or "BAKI PEMBUKAAN" in line.upper():
            m = AMOUNT_RE.search(line)
            if m:
                opening = float(m.group().replace(",", ""))
        if "CLOSING BALANCE" in line.upper() or "BAKI PENUTUPAN" in line.upper():
            m = AMOUNT_RE.search(line)
            if m:
                closing = float(m.group().replace(",", ""))
        if "TOTAL DEBITS" in line.upper() or "JUMLAH DEBIT" in line.upper():
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                total_debits = float(amounts[-1].replace(",", ""))
        if "TOTAL CREDITS" in line.upper() or "JUMLAH KREDIT" in line.upper():
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                total_credits = float(amounts[-1].replace(",", ""))

    # ── Parse transactions ────────────────────────────────────────────────────
    # Strategy: accumulate lines; flush a transaction when a new date line appears.
    # Transaction line layout (space-separated, variable widths):
    #   DDMon  <description...>  [cheque]  [debit]  [credit]  balance
    # Multi-line descriptions: continuation lines have no leading date.

    transactions = []
    current_date = None
    current_lines = []

    def flush(d, raw_lines):
        if not d or not raw_lines:
            return
        raw = " ".join(raw_lines)
        # Extract all amounts from the raw line
        amounts = AMOUNT_RE.findall(raw)
        if len(amounts) < 2:
            return  # Need at least amount + balance

        balance = float(amounts[-1].replace(",", ""))
        # The second-to-last amount might be debit or credit
        txn_amount_str = amounts[-2].replace(",", "")
        txn_amount = float(txn_amount_str)

        # Determine type by checking keywords or by balance movement
        raw_upper = raw.upper()
        # Remove date prefix and trailing amounts to get description
        desc = re.sub(r"^\d{1,2}[A-Za-z]{3}\s*", "", raw)
        # Strip trailing numeric fields (balance and transaction amount)
        for amt in reversed(amounts[-2:]):
            desc = desc.rsplit(amt, 1)[0].strip(" ,")
        # Clean up extra whitespace
        desc = re.sub(r"\s+", " ", desc).strip()

        # Determine debit/credit
        if "DEBIT" in raw_upper or "/MISC DEBIT" in raw_upper:
            txn_type = "debit"
            signed_amount = -txn_amount
        elif "CREDIT" in raw_upper or "/MISC CREDIT" in raw_upper or "CR TRF" in raw_upper:
            txn_type = "credit"
            signed_amount = txn_amount
        elif "INT/HB/PFT" in raw_upper:
            txn_type = "credit"
            signed_amount = txn_amount
        else:
            # Infer from balance direction — need previous balance
            txn_type = "debit"
            signed_amount = -txn_amount

        transactions.append({
            "date": d.isoformat(),
            "description": desc,
            "amount": signed_amount,
            "balance": balance,
            "type": txn_type,
            "raw": raw.strip(),
        })

    in_transactions = False
    for line in lines:
        # Skip header/footer boilerplate
        if any(x in line.upper() for x in [
            "ACCOUNT STATEMENT", "PENYATA AKAUN", "PRIVACY NOTICE",
            "COMPLAINTS MANAGEMENT", "CHEQUE CREDITING", "FIXED DEPOSIT",
            "PROTECTED BY PIDM", "PAGE /", "MUKA SURAT",
        ]):
            continue

        # Detect start of transaction section
        if "DATE" in line.upper() and "TRANSACTION" in line.upper() and "BALANCE" in line.upper():
            in_transactions = True
            continue
        if "BAKI BAWA KE HADAPAN" in line.upper() or "BALANCE B/F" in line.upper():
            in_transactions = True
            continue

        if not in_transactions:
            continue

        m = DATE_RE.match(line.strip())
        if m:
            flush(current_date, current_lines)
            day = int(m.group(1))
            month_num = MONTH_MAP[m.group(2).lower()]
            # Handle year boundary (Dec statement may have Jan transactions)
            year = stmt_year
            if month_num < stmt_month - 1:
                year = stmt_year + 1
            current_date = date(year, month_num, day)
            current_lines = [line.strip()]
        elif current_date is not None:
            stripped = line.strip()
            if stripped:
                current_lines.append(stripped)

    flush(current_date, current_lines)

    return {
        "account": account_no,
        "period_start": period_start,
        "period_end": period_end,
        "opening_balance": opening,
        "closing_balance": closing,
        "total_debits": total_debits,
        "total_credits": total_credits,
        "transactions": transactions,
    }


def write_csv(transactions: list, csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "description", "amount", "balance", "type", "raw"])
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions → {csv_path}")


def process_pdf(pdf_path: Path, csv_path: Path | None = None):
    print(f"Parsing {pdf_path.name} ...")
    result = parse_statement(pdf_path)

    if csv_path is None:
        stem = pdf_path.stem  # AMB-PDF-YYYY-MM
        parts = stem.split("-")
        csv_path = config.ambank_csv_path(int(parts[2]), int(parts[3]))

    write_csv(result["transactions"], csv_path)

    # Validation
    txn_count = len(result["transactions"])
    print(f"  Period : {result['period_start']} – {result['period_end']}")
    print(f"  Opening: MYR {result['opening_balance']:,.2f}  Closing: MYR {result['closing_balance']:,.2f}")
    if result["total_debits"] and result["total_credits"]:
        print(f"  Debits : MYR {result['total_debits']:,.2f}  Credits: MYR {result['total_credits']:,.2f}")
    return result


def process_all(year: int | None = None):
    """Process all AmBank PDFs on file."""
    months = [
        (2025, 4), (2025, 5), (2025, 6), (2025, 7), (2025, 8),
        (2025, 9), (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2), (2026, 3),
    ]
    for y, m in months:
        if year and y != year:
            continue
        pdf = config.ambank_pdf_path(y, m)
        if pdf.exists():
            process_pdf(pdf)
        else:
            print(f"  MISSING: {pdf}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse AmBank PDF statements to CSV")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all AmBank PDFs on file")
    group.add_argument("--year", type=int, help="Process all PDFs for a given year")
    parser.add_argument("--output", help="Output CSV path (single-file mode only)")
    args = parser.parse_args()

    if args.all or args.year:
        process_all(year=args.year)
    elif args.pdf:
        out = Path(args.output) if args.output else None
        process_pdf(Path(args.pdf), out)
    else:
        parser.print_help()
