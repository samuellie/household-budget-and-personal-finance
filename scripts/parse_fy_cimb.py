"""
CIMB Savings Account PDF Statement Parser (Fui Yee)
====================================================
Extracts transactions from Fui Yee's CIMB savings account PDFs and writes CSV.

CIMB statement format:
  - Columns: Date (DD/MM/YYYY) | Description | Ref No | Withdrawal | Deposits | Tax | Balance
  - Header row: OPENING BALANCE <balance>
  - Footer row: CLOSING BALANCE / BAKI PENUTUP <balance>
  - Each transaction starts on a date line ending in "<amount> <running balance>".
    Description continues on following lines; a second DD/MM/YYYY date + ref may
    also appear as a continuation line (no decimal amount) — it is NOT a new txn.

Withdrawal vs deposit is disambiguated by the running Balance column:
  balance decreased vs previous row  -> withdrawal (debit,  negative amount)
  balance increased vs previous row  -> deposit    (credit,  positive amount)

Input  PDFs: Bank Statements/Fui Yee/CIMB/{YYYY}/<Mon YY>.pdf  (e.g. "July 25.pdf")
Output CSVs: csv/fuiyee/cimb/YYYY-MM.csv

Usage:
    python3 parse_fy_cimb.py "Bank Statements/Fui Yee/CIMB/2025/July 25.pdf"
    python3 parse_fy_cimb.py --all
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
import config

# CIMB source directory + output CSV location (this account is not in config.py)
FY_CIMB_DIR = config.BANK_STATEMENTS_DIR / "Fui Yee" / "CIMB"
FY_CIMB_CSV_DIR = config.CSV_DIR / "fuiyee" / "cimb"

# Date at start of a line: DD/MM/YYYY
DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\b")
# Plain amount with 2 decimals: e.g. "1,234.56"
AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")
# A leading second date + optional ref on a continuation line (e.g. "16/06/2025 5947")
CONT_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}\s*")
# Ref-code token like "T33049" often trailing the description on the main line
REF_TOKEN_RE = re.compile(r"\bT\d{4,}\b")

# Month name / abbreviation -> month number
MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Boilerplate substrings to skip (headers, footers, addresses, notices)
SKIP_PATTERNS = [
    "STATEMENT OF ACCOUNT", "CIMB BANK BERHAD", "PAGE / HALAMAN",
    "STATEMENT DATE", "TARIKH PENYATA", "WONG FUI YEE", "NO 17",
    "JALAN CEMPERAI", "TAMAN RASA", "SELANGOR",
    "SUMMARY OF YOUR TOTAL RELATIONSHIP", "RINGKASAN MATA",
    "POINTS EARNED", "MATA DIPEROLEHI", "POINTS EXPIRING", "MATA YANG AKAN",
    "SAVINGS ACCOUNT TRANSACTION DETAILS", "BUTIR-BUTIR TRANSAKSI",
    "ACCOUNT NO", "NO AKAUN", "PROTECTED BY PIDM",
    "DATE DESCRIPTION REF NO", "TARIKH DISKRIPSI",
    "(RM) (RM) (RM) (RM)", "PENGELUARAN DEPOSIT",
    "IMPORTANT NOTICE", "NOTIS PENTING", "EFFECTIVE 8 NOVEMBER",
    "THE BANK MUST", "YOU CAN TRANSFER FUNDS", "FOR MORE INFORMATION",
    "MM/S BBB", "END OF STATEMENT", "AKHIR PENYATA",
]

# Additional footer/notice fragments that wrap onto their own lines and would
# otherwise be swallowed into the last transaction on a page. Matched anywhere.
NOISE_PATTERNS = [
    "OF TOTAL AVAILABLE BONUS POINTS", "INFO: CIMB.COM.MY", "CIMB.COM.MY/BC",
    "WHICH THE INFORMATION REFLECTED", "DEEMED TO BE CORRECT",
    "EXPLANATORY NOTES", "PHONE BANKING SERVICE", "CALL CENTRE",
    "CALLCENTRE@CIMB", "WWW.CIMB", "WWW.CIMBCLICKS",
    "PAY YOUR CREDIT CARD AND MUCH MORE", "CONSOLIDATED TO GIVE YOU",
    "14DAYS", "14 DAYS", "FAILING",
]


def infer_year_month(pdf_path: Path) -> tuple[int, int]:
    """Determine (year, month) from a filename like 'July 25.pdf' or 'Jan 26.pdf'."""
    stem = pdf_path.stem.strip()
    m = re.match(r"([A-Za-z]+)\s*'?(\d{2})", stem)
    if not m:
        raise ValueError(f"Cannot parse month/year from filename: {pdf_path.name}")
    mon_word = m.group(1).lower()
    yy = int(m.group(2))
    if mon_word not in MONTHS:
        raise ValueError(f"Unknown month name '{mon_word}' in {pdf_path.name}")
    return 2000 + yy, MONTHS[mon_word]


def clean_description(raw_lines: list[str]) -> str:
    """Join a transaction's lines into a clean description string."""
    # First line: drop leading date and trailing "amount balance"
    first = raw_lines[0]
    first = DATE_RE.sub("", first).strip()
    # Remove the trailing two amounts (txn amount + running balance)
    amts = list(AMOUNT_RE.finditer(first))
    if amts:
        first = first[: amts[0].start()].strip()
    # Drop a trailing ref token like "T33049"
    first = REF_TOKEN_RE.sub("", first).strip()

    parts = [first]
    for cont in raw_lines[1:]:
        c = cont.strip()
        if not c:
            continue
        # Strip a leading continuation date (e.g. "16/06/2025 5947" -> "5947")
        c = CONT_DATE_RE.sub("", c).strip()
        # Drop standalone numeric-only ref lines
        if re.fullmatch(r"[\d,\.]+", c):
            continue
        if c:
            parts.append(c)

    desc = " ".join(parts)
    desc = re.sub(r"\s+", " ", desc).strip(" |*")
    return desc


def parse_statement(pdf_path: Path) -> dict:
    """Parse a single CIMB PDF into a dict of results."""
    stmt_year, stmt_month = infer_year_month(pdf_path)

    all_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_text.append(text)
    lines = "\n".join(all_text).splitlines()

    opening = closing = None
    for line in lines:
        lu = line.upper()
        if opening is None and "OPENING BALANCE" in lu:
            m = AMOUNT_RE.search(line)
            if m:
                opening = float(m.group().replace(",", ""))
        if "CLOSING BALANCE" in lu or "BAKI PENUTUP" in lu:
            amts = AMOUNT_RE.findall(line)
            if amts:
                closing = float(amts[-1].replace(",", ""))

    # ── Parse transactions ────────────────────────────────────────────────────
    # A "real" transaction line starts with a date AND ends with two amounts
    # (txn amount + running balance). A date line with no decimal amount is a
    # continuation (second date + ref) and belongs to the current transaction.
    transactions = []
    prev_balance = opening if opening is not None else 0.0
    current_lines: list[str] = []
    current_date: str | None = None

    def is_new_txn(line: str) -> bool:
        if not DATE_RE.match(line):
            return False
        return len(AMOUNT_RE.findall(line)) >= 2

    def flush():
        nonlocal prev_balance
        if not current_date or not current_lines:
            return
        raw = " | ".join(current_lines)
        amts = AMOUNT_RE.findall(current_lines[0])
        if len(amts) < 2:
            return
        txn_amount = float(amts[-2].replace(",", ""))
        balance = float(amts[-1].replace(",", ""))

        # Disambiguate debit/credit via running balance movement
        if balance < prev_balance - 1e-6:
            signed = -txn_amount
            txn_type = "debit"
        elif balance > prev_balance + 1e-6:
            signed = txn_amount
            txn_type = "credit"
        else:
            # No net change (rare). Fall back to sign by amount ~ 0 -> treat as credit.
            signed = txn_amount
            txn_type = "credit"

        transactions.append({
            "date": datetime.strptime(current_date, "%d/%m/%Y").date().isoformat(),
            "description": clean_description(current_lines),
            "amount": round(signed, 2),
            "type": txn_type,
            "balance": balance,
            "raw": raw[:150],
        })
        prev_balance = balance

    in_txns = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        if "OPENING BALANCE" in stripped.upper():
            in_txns = True
            continue
        if "CLOSING BALANCE" in stripped.upper() or "BAKI PENUTUP" in stripped.upper():
            flush()
            current_date, current_lines = None, []
            in_txns = False
            continue
        if not in_txns:
            continue
        su = stripped.upper()
        if any(pat in su for pat in SKIP_PATTERNS):
            continue
        if any(pat in su for pat in NOISE_PATTERNS):
            continue

        if is_new_txn(stripped):
            flush()
            current_date = DATE_RE.match(stripped).group(1)
            current_lines = [stripped]
        else:
            if current_date is not None:
                current_lines.append(stripped)

    flush()

    return {
        "stmt_year": stmt_year,
        "stmt_month": stmt_month,
        "opening_balance": opening,
        "closing_balance": closing,
        "transactions": transactions,
    }


def write_csv(transactions: list, csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date", "description", "amount", "type", "balance", "raw"]
        )
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions -> {csv_path}")


def validate(result: dict) -> tuple[bool, float]:
    """Reconcile opening + deposits - withdrawals == final balance / closing."""
    txns = result["transactions"]
    opening = result["opening_balance"] or 0.0
    deposits = sum(t["amount"] for t in txns if t["amount"] > 0)
    withdrawals = sum(-t["amount"] for t in txns if t["amount"] < 0)
    computed = opening + deposits - withdrawals

    final_balance = txns[-1]["balance"] if txns else opening
    target = result["closing_balance"] if result["closing_balance"] is not None else final_balance
    diff = round(computed - target, 2)
    # Also check the last running balance matches closing
    tail_diff = round(final_balance - target, 2)
    ok = abs(diff) < 0.01 and abs(tail_diff) < 0.01
    return ok, diff


def process_pdf(pdf_path: Path, csv_path: Path | None = None) -> dict:
    print(f"Parsing {pdf_path.name} ...")
    result = parse_statement(pdf_path)
    if csv_path is None:
        csv_path = FY_CIMB_CSV_DIR / f"{result['stmt_year']}-{result['stmt_month']:02d}.csv"
    write_csv(result["transactions"], csv_path)

    op = result["opening_balance"]
    cl = result["closing_balance"]
    print(f"  Opening: MYR {op:,.2f}  Closing: MYR {cl:,.2f}"
          if op is not None and cl is not None else "  (balances not found)")

    ok, diff = validate(result)
    status = "PASS" if ok else "FAIL"
    print(f"  Reconcile: {status}  (diff = MYR {diff:,.2f})")
    result["_status"] = status
    result["_diff"] = diff
    result["_csv"] = csv_path
    return result


def process_all() -> list[dict]:
    pdfs = sorted(FY_CIMB_DIR.glob("*/*.pdf"))
    if not pdfs:
        print(f"No PDFs found under {FY_CIMB_DIR}")
        return []
    results = []
    for pdf in pdfs:
        try:
            results.append(process_pdf(pdf))
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR parsing {pdf.name}: {e}")

    # Summary table
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"{'Statement':<12}{'Txns':>6}{'Status':>8}{'Diff (MYR)':>14}")
    print("-" * 60)
    for r in sorted(results, key=lambda x: (x["stmt_year"], x["stmt_month"])):
        key = f"{r['stmt_year']}-{r['stmt_month']:02d}"
        print(f"{key:<12}{len(r['transactions']):>6}{r['_status']:>8}{r['_diff']:>14,.2f}")
    total_txns = sum(len(r["transactions"]) for r in results)
    passed = sum(1 for r in results if r["_status"] == "PASS")
    print("-" * 60)
    print(f"{'TOTAL':<12}{total_txns:>6}{f'{passed}/{len(results)}':>8}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse Fui Yee's CIMB PDF statements to CSV")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all CIMB PDFs on file")
    parser.add_argument("--output", help="Output CSV path (single-file mode only)")
    args = parser.parse_args()

    if args.all:
        process_all()
    elif args.pdf:
        out = Path(args.output) if args.output else None
        process_pdf(Path(args.pdf), out)
    else:
        parser.print_help()
