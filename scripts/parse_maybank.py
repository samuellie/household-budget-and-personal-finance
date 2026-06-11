"""
Maybank PDF Statement Parser
==============================
Extracts transactions from Maybank Islamic savings account PDFs and writes CSV.

Maybank statement format:
  - Columns: ENTRY DATE (DD/MM/YY), TRANSACTION DESCRIPTION (multi-line),
             TRANSACTION AMOUNT (number with +/- suffix), STATEMENT BALANCE
  - Header: BEGINNING BALANCE
  - Footer: ENDING BALANCE, TOTAL CREDIT, TOTAL DEBIT

Usage:
    python3 parse_maybank.py "Bank Statements/Samuel/Maybank/2025/158088-403774_20250430.pdf"
    python3 parse_maybank.py --all
    python3 parse_maybank.py --year 2025
"""

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
import config

# Date pattern: DD/MM/YY at start of a line
DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{2})\s")
# Amount with sign suffix: e.g. "1,234.56-" or "5,000.00+"
SIGNED_AMOUNT_RE = re.compile(r"([\d,]+\.\d{2})([+-])")
# Plain amount (for balances)
AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")

SKIP_PATTERNS = [
    "MAYBANK ISLAMIC", "15TH FLOOR", "TOWER A", "DATARAN MAYBANK",
    "MUKA/", "TARIKH PENYATA", "STATEMENT DATE", "NOMBOR AKAUN",
    "ACCOUNT NUMBER", "PROTECTED BY PIDM", "PERSONAL SAVER",
    "URUSNIAGA AKAUN", "TARIKH MASUK", "ENTRY DATE", "BUTIR URUSNIAGA",
    "TRANSACTION DESCRIPTION", "JUMLAH URUSNIAGA", "TRANSACTION AMOUNT",
    "BAKI PENYATA", "STATEMENT BALANCE", "PERHATION", "NOTE",
    "SEMUA MAKLUMAT", "ALL ITEMS AND BALANCES", "SILA BERITAHU",
    "PLEASE NOTIFY", "TANJUNG MALIM", "PERAK", "JLN CHANGKAT",
    "LIE ZHI HOU", "G 52",
]


def parse_statement(pdf_path: Path) -> dict:
    """
    Parse a single Maybank PDF.
    """
    # Infer year/month from filename: 158088-403774_YYYYMMDD.pdf
    stem = pdf_path.stem  # e.g. 158088-403774_20250430
    date_part = stem.split("_")[1]  # 20250430
    stmt_year  = int(date_part[:4])
    stmt_month = int(date_part[4:6])

    all_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_text.append(text)

    full_text = "\n".join(all_text)
    lines = full_text.splitlines()

    # ── Parse summary figures ─────────────────────────────────────────────────
    beginning = ending = total_credit = total_debit = 0.0

    for line in lines:
        lu = line.upper()
        if "BEGINNING BALANCE" in lu:
            m = AMOUNT_RE.search(line)
            if m:
                beginning = float(m.group().replace(",", ""))
        if "ENDING BALANCE" in lu:
            m = AMOUNT_RE.search(line)
            if m:
                ending = float(m.group().replace(",", ""))
        if "TOTAL CREDIT" in lu:
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                total_credit = float(amounts[-1].replace(",", ""))
        if "TOTAL DEBIT" in lu:
            amounts = AMOUNT_RE.findall(line)
            if amounts:
                total_debit = float(amounts[-1].replace(",", ""))

    # ── Parse transactions ────────────────────────────────────────────────────
    # Each transaction spans multiple lines:
    #   DD/MM/YY  <first description line>  amount+/-  balance
    #   <continuation line 1>
    #   <continuation line 2 — transaction type label, e.g. "SALE DEBIT">
    #
    # Strategy: collect lines between date-starting lines; extract amount from
    # the line that contains a signed amount token.

    transactions = []
    current_date_str = None
    current_lines = []

    def flush(date_str, raw_lines):
        if not date_str or not raw_lines:
            return
        raw = " | ".join(raw_lines)

        # Find signed amount (e.g. "1,234.56-")
        m = SIGNED_AMOUNT_RE.search(raw)
        if not m:
            return

        txn_amount = float(m.group(1).replace(",", ""))
        sign = m.group(2)
        signed_amount = txn_amount if sign == "+" else -txn_amount
        txn_type = "credit" if sign == "+" else "debit"

        # Extract balance — the last plain amount on the same segment after the signed amount
        after = raw[m.end():]
        balance_m = AMOUNT_RE.search(after)
        balance = float(balance_m.group().replace(",", "")) if balance_m else 0.0

        # Build description: first line text, strip the date prefix
        first_line = raw_lines[0]
        desc_parts = first_line.split()
        # Remove the date token (first word)
        if desc_parts and re.match(r"\d{2}/\d{2}/\d{2}", desc_parts[0]):
            desc_parts = desc_parts[1:]
        # Strip signed amount and balance from description
        desc_raw = " ".join(desc_parts)
        desc_raw = SIGNED_AMOUNT_RE.sub("", desc_raw)
        desc_raw = re.sub(r"[\d,]+\.\d{2}", "", desc_raw)
        # Add continuation lines (skip pure type labels and reference numbers)
        for continuation in raw_lines[1:]:
            c = continuation.strip()
            # Skip lines that are just transaction type labels or reference numbers
            if re.match(r"^(SALE DEBIT|PRE-AUTH DEBIT|PRE-AUTH REFUND|FUND TRANSFER|IBK FUND|FPX PAYMENT|DUITNOW QR|MAE QR|QR PAY|MBB CT|INTERBANK GIRO|SVG GIRO)$", c, re.IGNORECASE):
                continue
            if re.match(r"^\d{10,}$", c):  # pure reference number
                continue
            desc_raw += " " + c
        desc = re.sub(r"\s+", " ", desc_raw).strip(" |*")

        # Parse date
        d = datetime.strptime(date_str, "%d/%m/%y").date()

        transactions.append({
            "date": d.isoformat(),
            "description": desc,
            "amount": signed_amount,
            "balance": balance,
            "type": txn_type,
            "raw": raw[:200],
        })

    in_transactions = False
    for line in lines:
        stripped = line.strip()

        # Skip boilerplate
        if any(pat in stripped.upper() for pat in SKIP_PATTERNS):
            continue
        if not stripped:
            continue

        # Detect transaction section
        if "BEGINNING BALANCE" in stripped.upper():
            in_transactions = True
            continue
        if "ENDING BALANCE" in stripped.upper():
            in_transactions = False
            flush(current_date_str, current_lines)
            current_date_str, current_lines = None, []
            continue

        if not in_transactions:
            continue

        m = DATE_RE.match(stripped)
        if m:
            flush(current_date_str, current_lines)
            current_date_str = m.group(1)
            current_lines = [stripped]
        else:
            if current_date_str is not None:
                current_lines.append(stripped)

    flush(current_date_str, current_lines)

    return {
        "account": config.MAYBANK_ACCOUNT,
        "stmt_year": stmt_year,
        "stmt_month": stmt_month,
        "beginning_balance": beginning,
        "ending_balance": ending,
        "total_credit": total_credit,
        "total_debit": total_debit,
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
        csv_path = config.maybank_csv_path(result["stmt_year"], result["stmt_month"])

    write_csv(result["transactions"], csv_path)

    print(f"  Beginning: MYR {result['beginning_balance']:,.2f}  Ending: MYR {result['ending_balance']:,.2f}")
    if result["total_credit"] or result["total_debit"]:
        print(f"  Credits: MYR {result['total_credit']:,.2f}  Debits: MYR {result['total_debit']:,.2f}")
    return result


def process_all(year: int | None = None):
    months = [
        (2025, 3), (2025, 4), (2025, 5), (2025, 6), (2025, 7),
        (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2), (2026, 3),
    ]
    for y, m in months:
        if year and y != year:
            continue
        pdf = config.maybank_pdf_path(y, m)
        if pdf.exists():
            process_pdf(pdf)
        else:
            print(f"  MISSING: {pdf}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse Maybank PDF statements to CSV")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all Maybank PDFs on file")
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
