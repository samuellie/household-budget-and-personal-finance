"""
UOB Credit Card PDF Statement Parser (Fui Yee)
================================================
Extracts transactions from Fui Yee's UOB Credit Card statement PDFs and writes CSV.

Statement format (UOB CARD CENTRE / STATEMENT OF ACCOUNT):
  - Header:  "Statement Date  DD MMM YY"  (e.g. "16 OCT 25")  → statement month/year
  - Summary row (final page), header then values:
        Previous Balance | Credit / Payment | Debit / Fees | Retail Purchase |
        Cash Advance | Total Balance Due
    e.g.  11,243.54  11,554.09  .00  544.70  .00  234.15
    Values may carry a trailing "CR" for a credit balance (e.g. "0.01CR").
  - Transaction detail lines (final page):
        "DD MMM  DESCRIPTION  AMOUNT[ CR]"     e.g. "18 SEP GRAB RIDES-EC ... MY 3.00"
    Purchases/fees are plain amounts (debits); payments/refunds/rebates carry "CR".
  - Non-transaction lines inside the section (skipped):
        card header ("ONE PLATINUM VISA ..."), "CARD", "CREDIT LIMIT RM ...",
        "PREVIOUS BAL ...", "SUB-TOTAL ...", "MINIMUM PAYMENT DUE ...",
        "** END OF STATEMENT**".

Year inference for DD MMM dates:
  Statements are dated the 16th and cover ~one month back. If a transaction's
  month is after the statement month, it belongs to the previous calendar year
  (year-boundary handling, e.g. a "28 DEC" line on a "16 JAN 26" statement).

Amount convention in output CSV:
  - Negative  = purchase / fee / service tax (debit — you owe more)
  - Positive  = payment / refund / rebate / credit (CR — reduces what you owe)

CSV schema:
    date,description,amount,type,balance,raw

Usage:
    python3 parse_fy_uob.py "Bank Statements/Fui Yee/UOB/2025/Oct 25.pdf"
    python3 parse_fy_uob.py --all
    python3 parse_fy_uob.py --year 2025
"""

import argparse
import calendar
import csv
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
import config

FY_UOB_DIR = config.BANK_STATEMENTS_DIR / "Fui Yee" / "UOB"
FY_UOB_CSV_DIR = config.CSV_DIR / "fuiyee" / "uob"

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # long forms used in filenames
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"

# Statement date in the header: "Statement Date  16 OCT 25"
STMT_DATE_RE = re.compile(
    rf"Statement Date\s+(\d{{2}})\s+({MON})\s+(\d{{2}})", re.IGNORECASE
)

# Transaction line: "DD MMM  <desc>  <amount>[ CR]"
TXN_LINE_RE = re.compile(
    rf"^(\d{{2}})\s+({MON})\s+(.+?)\s+([\d,]+\.\d{{2}})\s*(CR)?\s*$",
    re.IGNORECASE,
)

# A single monetary value, optional trailing CR (no space), used in the summary row
VAL_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?:CR)?|\.00(?:CR)?")

# Lines inside the transaction section that are NOT transactions
_SKIP_SUBSTRINGS = [
    "transaction date", "tarikh transaksi",
    "transaction description", "huraian transaksi",
    "transaction amount", "amaun transaksi",
    "credit limit", "previous bal", "sub-total", "sub total",
    "minimum payment due", "end of statement",
    "note :", "credit balance do not", "one platinum visa",
]


def _parse_summary_value(token: str) -> float:
    """Parse a summary token like '11,554.09', '.00', or '0.01CR' → signed float
    where CR is a credit balance (returned negative)."""
    token = token.strip()
    is_cr = token.upper().endswith("CR")
    if is_cr:
        token = token[:-2].strip()
    val = float(token.replace(",", ""))
    return -val if is_cr else val


def statement_month_from_pdf(pdf_path: Path) -> tuple[int, int]:
    """Return (year, month) of the statement, read from the 'Statement Date' header."""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            m = STMT_DATE_RE.search(text)
            if m:
                month = MONTH_MAP[m.group(2).lower()]
                year = 2000 + int(m.group(3))
                return year, month
    raise ValueError(f"Could not find Statement Date in {pdf_path}")


def month_from_filename(pdf_path: Path) -> tuple[int, int]:
    """Return (year, month) inferred from a filename like 'Oct 25.pdf' / 'June 26.pdf'."""
    stem = pdf_path.stem  # e.g. "Oct 25" or "June 26"
    m = re.match(r"^([A-Za-z]+)\s+(\d{2})$", stem.strip())
    if not m:
        raise ValueError(f"Unrecognised filename format: {pdf_path.name}")
    month = MONTH_MAP[m.group(1).lower()]
    year = 2000 + int(m.group(2))
    return year, month


def _infer_txn_year(txn_month: int, stmt_year: int, stmt_month: int) -> int:
    """A transaction whose month is later than the statement month belongs to the
    previous calendar year (statement covers ~one month leading up to it)."""
    if txn_month > stmt_month:
        return stmt_year - 1
    return stmt_year


def parse_statement(pdf_path: Path) -> tuple[list[dict], dict]:
    """
    Parse a single UOB CC PDF.
    Returns (transactions, summary).

    transactions: list of {date, description, amount, type, balance, raw}
    summary: {prev_balance, credit, debit_fees, retail, cash_advance, new_balance}
             (values signed; CR balances negative)
    """
    stmt_year, stmt_month = statement_month_from_pdf(pdf_path)

    all_lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_lines.extend(text.splitlines())

    # ── Summary row ───────────────────────────────────────────────────────────
    summary: dict = {}
    for i, raw in enumerate(all_lines):
        if "Baki Perlu Dibayar" in raw or "Total Balance Due" in raw:
            # The numeric values line follows the (possibly multi-line) header.
            for j in range(i + 1, min(i + 6, len(all_lines))):
                cand = all_lines[j].strip()
                vals = VAL_RE.findall(cand)
                if len(vals) >= 6:
                    nums = [_parse_summary_value(v) for v in vals[:6]]
                    summary = {
                        "prev_balance": nums[0],
                        "credit":       nums[1],
                        "debit_fees":   nums[2],
                        "retail":       nums[3],
                        "cash_advance": nums[4],
                        "new_balance":  nums[5],
                    }
                    break
            if summary:
                break

    # ── Transaction detail lines ──────────────────────────────────────────────
    transactions: list[dict] = []
    in_section = False
    for raw in all_lines:
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()

        if "transaction amount" in lower or "amaun transaksi" in lower:
            in_section = True
            continue
        if "end of statement" in lower:
            in_section = False
            continue
        if not in_section:
            continue

        if any(s in lower for s in _SKIP_SUBSTRINGS):
            continue

        m = TXN_LINE_RE.match(line)
        if not m:
            continue

        day = int(m.group(1))
        txn_month = MONTH_MAP[m.group(2).lower()]
        desc = re.sub(r"\s+", " ", m.group(3)).strip()
        raw_amount = float(m.group(4).replace(",", ""))
        is_cr = bool(m.group(5))

        txn_year = _infer_txn_year(txn_month, stmt_year, stmt_month)
        try:
            txn_date = date(txn_year, txn_month, day)
        except ValueError:
            continue

        amount = raw_amount if is_cr else -raw_amount
        txn_type = "credit" if is_cr else "debit"

        transactions.append({
            "date":        txn_date.isoformat(),
            "description": desc,
            "amount":      amount,
            "type":        txn_type,
            "balance":     "",
            "raw":         line[:200],
        })

    return transactions, summary


def write_csv(transactions: list[dict], csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "description", "amount", "type", "balance", "raw"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions → {csv_path}")


def validate(transactions: list[dict], summary: dict) -> tuple[bool, float]:
    """Reconcile prev_balance + total_debits - total_credits == new_balance.

    Debits are stored negative, credits positive, so:
        expected_new = prev - sum(credits) + sum(abs(debits))
                     = prev - sum(positive amounts) - sum(negative amounts)  ... no
    Compute explicitly from signed amounts.
    """
    if not summary:
        return False, float("nan")
    total_debits = sum(-t["amount"] for t in transactions if t["type"] == "debit")
    total_credits = sum(t["amount"] for t in transactions if t["type"] == "credit")
    expected_new = summary["prev_balance"] + total_debits - total_credits
    diff = round(expected_new - summary["new_balance"], 2)
    return abs(diff) < 0.01, diff


def process_pdf(pdf_path: Path, csv_path: Path | None = None) -> list[dict]:
    print(f"Parsing {pdf_path.name} ...")
    transactions, summary = parse_statement(pdf_path)

    stmt_year, stmt_month = statement_month_from_pdf(pdf_path)
    file_year, file_month = month_from_filename(pdf_path)
    if (stmt_year, stmt_month) != (file_year, file_month):
        print(f"  WARNING: filename says {file_year}-{file_month:02d} but statement "
              f"date says {stmt_year}-{stmt_month:02d}. Keying by statement date.")

    if csv_path is None:
        csv_path = FY_UOB_CSV_DIR / f"{stmt_year}-{stmt_month:02d}.csv"

    write_csv(transactions, csv_path)

    debits = sum(1 for t in transactions if t["type"] == "debit")
    credits = sum(1 for t in transactions if t["type"] == "credit")
    ok, diff = validate(transactions, summary)
    total_debits = sum(-t["amount"] for t in transactions if t["type"] == "debit")
    total_credits = sum(t["amount"] for t in transactions if t["type"] == "credit")
    status = "PASS" if ok else "FAIL"
    if summary:
        print(f"  Charges: {debits}  Payments/credits: {credits}  "
              f"| debits={total_debits:.2f} credits={total_credits:.2f}")
        print(f"  Reconcile [{status}]: prev {summary['prev_balance']:.2f} "
              f"+ debits {total_debits:.2f} - credits {total_credits:.2f} "
              f"=> {summary['prev_balance'] + total_debits - total_credits:.2f} "
              f"vs new {summary['new_balance']:.2f} (diff {diff:+.2f})")
    else:
        print(f"  Charges: {debits}  Payments/credits: {credits}  | NO SUMMARY FOUND [FAIL]")
    return transactions


def _discover_pdfs(year: int | None = None) -> list[Path]:
    pdfs: list[Path] = []
    for sub in sorted(FY_UOB_DIR.glob("*")):
        if not sub.is_dir():
            continue
        for pdf in sorted(sub.glob("*.pdf")):
            pdfs.append(pdf)
    if year is not None:
        filtered = []
        for pdf in pdfs:
            try:
                y, _ = statement_month_from_pdf(pdf)
            except ValueError:
                continue
            if y == year:
                filtered.append(pdf)
        return filtered
    return pdfs


def process_all(year: int | None = None):
    pdfs = _discover_pdfs(year)
    if not pdfs:
        print("No UOB PDFs found.")
        return
    for pdf in pdfs:
        process_pdf(pdf)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse Fui Yee's UOB Credit Card PDF statements to CSV"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all UOB PDFs on file")
    group.add_argument("--year", type=int, help="Process all PDFs for a given statement year")
    parser.add_argument("--output", help="Output CSV path (single-file mode only)")
    args = parser.parse_args()

    if args.all or args.year:
        process_all(year=args.year)
    elif args.pdf:
        out = Path(args.output) if args.output else None
        process_pdf(Path(args.pdf), out)
    else:
        parser.print_help()
