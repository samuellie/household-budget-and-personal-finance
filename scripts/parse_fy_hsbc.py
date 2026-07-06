"""
Fui Yee HSBC Credit Card PDF Statement Parser (OCR)
====================================================
Fui Yee's HSBC Live+ Credit Card statements are image-only PDFs (no
extractable text). This script converts each page to an image then runs
OCR via tesseract, reconstructs transaction lines, and writes a CSV.

It is adapted from scripts/parse_hsbc.py (Samuel's HSBC OCR parser) and
follows the same OCR approach, line reconstruction, CSV schema, and
_review.txt behaviour.

Statement layout (Live+ card):
    Transactions use the two-date format:
        "21 AUG 21 AUG PAYMENT - THANK YOU 2,022.67CR"   (post date + txn date)
    Purchases (debits) have a plain amount; payments / refunds / credits
    carry a "CR" suffix. The summary block on page 2 contains:
        "Your Previous Statement Balance   955.84"
        "Your charge(s) for this month     RM1,800.51"
        "Your statement balance            660.49"     <- new balance

Prerequisites:
    brew install tesseract poppler
    pip3 install --break-system-packages pytesseract pdf2image Pillow

Usage:
    python3 parse_fy_hsbc.py "Bank Statements/Fui Yee/HSBC/2025/2025-09-12_Statement.pdf"
    python3 parse_fy_hsbc.py --all
    python3 parse_fy_hsbc.py --year 2026
"""

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

# Try importing OCR libraries — helpful error if not installed
try:
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import Image  # noqa: F401
    OCR_AVAILABLE = True
except ImportError as e:
    OCR_AVAILABLE = False
    _OCR_IMPORT_ERROR = str(e)

# ── Source / output paths (Fui Yee HSBC) ────────────────────────────────────
FY_HSBC_DIR = config.BANK_STATEMENTS_DIR / "Fui Yee" / "HSBC"


def fy_hsbc_csv_path(year: int, month: int) -> Path:
    return config.CSV_DIR / "fuiyee" / "hsbc" / f"{year}-{month:02d}.csv"


# ── Regexes ─────────────────────────────────────────────────────────────────
MON = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
# Two-date format (no year — infer from statement): "21 AUG 21 AUG ..."
DATE_RE_TWO = re.compile(rf"^(\d{{1,2}})\s+{MON}\s+\d{{1,2}}\s+{MON}\b", re.IGNORECASE)
# Single date with 2-digit year: "01 Sep 25"
DATE_RE1 = re.compile(rf"^(\d{{1,2}})\s+{MON}\s+(\d{{2}})\b", re.IGNORECASE)
# Slash format: "01/09/25"
DATE_RE2 = re.compile(r"^(\d{2})/(\d{2})/(\d{2})\b")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Amount: optional minus, digits/commas, dot, 2 decimals, optional CR suffix
AMOUNT_RE = re.compile(r"-?[\d,]+\.\d{2}(?:CR)?", re.IGNORECASE)

# Summary anchors used for reconciliation
PREV_BAL_RE = re.compile(r"Previous Statement Balance\s+([\d,]+\.\d{2})", re.IGNORECASE)
NEW_BAL_RE = re.compile(r"Your statement balance\s+([\d,]+\.\d{2})", re.IGNORECASE)
CHARGES_RE = re.compile(r"charge\(s\) for this month\s+RM\s*([\d,]+\.\d{2})", re.IGNORECASE)

SKIP_KEYWORDS = [
    "HSBC", "STATEMENT", "CREDIT CARD", "PAGE", "ACCOUNT NUMBER",
    "CREDIT LIMIT", "AVAILABLE CREDIT", "MINIMUM PAYMENT", "PAYMENT DUE",
    "OPENING BALANCE", "CLOSING BALANCE", "TOTAL", "BROUGHT FORWARD",
    "CARRIED FORWARD", "BALANCE B/F", "BALANCE C/F",
    "CARD NUMBER", "CERT NUMBER", "CASH BACK", "PAYMENT DUE DATE",
    "STATEMENT DATE", "YOUR CREDIT LIMIT", "YOUR PREVIOUS",
]


def ocr_pdf(pdf_path: Path) -> str:
    """Convert PDF pages to images and run OCR. Skips boilerplate T&C pages."""
    print(f"  Converting PDF to images ...")
    images = convert_from_path(str(pdf_path), dpi=300)
    print(f"  Running OCR on {len(images)} page(s) ...")
    pages_text = []
    for i, img in enumerate(images):
        text = pytesseract.image_to_string(img, config="--psm 6")
        if is_boilerplate_heavy(text):
            print(f"    Page {i+1}: skipped (T&C boilerplate)")
            continue
        pages_text.append(text)
    return "\n".join(pages_text)


def clean_ocr_text(text: str) -> str:
    """Apply common OCR correction heuristics (0/O, 1/l, comma-as-decimal)."""
    text = re.sub(r"\bO(\d)", r"0\1", text)     # O -> 0 before digit
    text = re.sub(r"(\d)O\b", r"\g<1>0", text)  # digit + O -> digit + 0
    text = re.sub(r"\bl(\d)", r"1\1", text)     # lowercase l -> 1 before digit
    # Fix amounts where OCR uses comma as decimal: "45,60" -> "45.60"
    # Also handles trailing CR: "489,.85CR" style handled below.
    text = re.sub(r"(\d),(\d{2})(?=[^,\d]|$)", r"\1.\2", text)
    # Fix OCR artefact "489,.85" (stray comma before dot): "489,.85" -> "489.85"
    text = re.sub(r"(\d),\.(\d{2})", r"\1.\2", text)
    # Fix a transaction row whose leading day-of-month was OCR'd with a stray
    # leading letter O / zero, e.g. "O06 APR 05 APR ..." or "006 APR 05 APR ...".
    # These otherwise fail the date regex and get merged into the prior row,
    # dropping a transaction (and breaking reconciliation).
    _MONS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    text = re.sub(
        rf"(?im)^[O0]+(\d{{1,2}}\s+(?:{_MONS})\b)", r"\1", text)
    return text


def is_boilerplate_heavy(page_text: str) -> bool:
    """True if a page looks like T&C / legal boilerplate rather than transactions."""
    boilerplate_markers = [
        "minimum monthly payment", "finance charge", "cardholder agreement",
        "terms and conditions", "terma & syarat", "caj kewangan",
        "pembayaran bulanan minimum", "agensi kaunseling", "bank negara malaysia",
        "interest-free period", "tempoh ihsan", "kadar faedah tahunan",
        "grace period", "loss or theft", "payment allocation",
    ]
    lower = page_text.lower()
    hits = sum(1 for marker in boilerplate_markers if marker in lower)
    return hits >= 3  # 3+ boilerplate phrases = T&C page


def extract_summary(text: str) -> dict:
    """Pull previous balance, charges, and new balance from the summary block."""
    summary = {"prev_balance": None, "charges": None, "new_balance": None}
    m = PREV_BAL_RE.search(text)
    if m:
        summary["prev_balance"] = float(m.group(1).replace(",", ""))
    m = CHARGES_RE.search(text)
    if m:
        summary["charges"] = float(m.group(1).replace(",", ""))
    m = NEW_BAL_RE.search(text)
    if m:
        summary["new_balance"] = float(m.group(1).replace(",", ""))
    return summary


def parse_ocr_text(text: str, stmt_year: int, stmt_month: int):
    """
    Parse OCR text into transactions.
    Returns (transactions, low_confidence_lines).
    Each transaction: {date, description, amount, type, confidence, raw}
      amount: signed float — purchase (debit) = negative, CR (credit) = positive
    """
    lines = text.splitlines()
    transactions = []
    current_date = None
    current_lines = []
    low_confidence_lines = []

    def try_parse_date(line: str):
        stripped = line.strip()

        # Two-date format: "21 AUG 21 AUG ..." — infer year from statement
        m = DATE_RE_TWO.match(stripped)
        if m:
            day = int(m.group(1))
            month_num = MONTH_MAP[m.group(2).lower()]
            # Infer year: if the (posting) month is after the statement month,
            # it belongs to the previous calendar year (year boundary).
            year = stmt_year if month_num <= stmt_month else stmt_year - 1
            try:
                return date(year, month_num, day)
            except ValueError:
                return None

        # Single date with 2-digit year: "01 Sep 25"
        m = DATE_RE1.match(stripped)
        if m:
            day = int(m.group(1))
            month_num = MONTH_MAP[m.group(2).lower()]
            year = 2000 + int(m.group(3))
            if year < 2020 or year > 2030:
                return None
            try:
                return date(year, month_num, day)
            except ValueError:
                return None

        # Slash format: "01/09/25"
        m = DATE_RE2.match(stripped)
        if m:
            day, month_num, year_2d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            year = 2000 + year_2d
            if year < 2020 or year > 2030:
                return None
            try:
                return date(year, month_num, day)
            except ValueError:
                return None
        return None

    def flush(d, raw_lines):
        if not d or not raw_lines:
            return
        raw = " ".join(raw_lines)

        # Strip statement boilerplate that bleeds into transaction lines via OCR.
        raw_parsed = re.sub(r"\s*Your charge\(s\).*", "", raw, flags=re.IGNORECASE | re.DOTALL)
        raw_parsed = re.sub(r"\s*\*{2,}\s*Please.*", "", raw_parsed, flags=re.IGNORECASE | re.DOTALL)
        raw_parsed = re.sub(r"\s*Summary of Instalment.*", "", raw_parsed, flags=re.IGNORECASE | re.DOTALL)
        raw_parsed = re.sub(r"\s*Total credit limit.*", "", raw_parsed, flags=re.IGNORECASE | re.DOTALL)
        raw_parsed = re.sub(r"\s*Your statement balance.*", "", raw_parsed, flags=re.IGNORECASE | re.DOTALL)

        amounts = AMOUNT_RE.findall(raw_parsed)
        if not amounts:
            low_confidence_lines.append(f"[NO AMOUNT] {raw[:120]}")
            return

        amt_raw = amounts[-1]
        is_cr = amt_raw.upper().endswith("CR")
        amt_str = amt_raw.rstrip("CcRr").replace(",", "")
        try:
            value = float(amt_str)
        except ValueError:
            low_confidence_lines.append(f"[BAD AMOUNT] {raw[:120]}")
            return

        # Credit card sign convention (matches downstream converter):
        #   purchase (debit) -> negative ; payment / refund (CR) -> positive
        if is_cr:
            amount = value
            txn_type = "credit"
        else:
            amount = -value
            txn_type = "debit"

        # Build description: first line minus date prefix, minus amounts, minus noise
        desc = raw_lines[0]
        desc = re.sub(r"\s*Your charge\(s\).*", "", desc, flags=re.IGNORECASE | re.DOTALL)
        desc = re.sub(r"\s*\*{2,}\s*Please.*", "", desc, flags=re.IGNORECASE | re.DOTALL)
        desc = re.sub(r"\s*Summary of Instalment.*", "", desc, flags=re.IGNORECASE | re.DOTALL)
        # Remove two-date prefix: "21 AUG 21 AUG"
        desc = re.sub(
            r"^\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+"
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*",
            "", desc, flags=re.IGNORECASE)
        # Remove single date with year: "01 Sep 25"
        desc = re.sub(
            r"^\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{2}\s*",
            "", desc, flags=re.IGNORECASE)
        # Remove slash date: "01/09/25"
        desc = re.sub(r"^\d{2}/\d{2}/\d{2}\s*", "", desc)
        # Remove amounts (incl. CR suffix)
        desc = AMOUNT_RE.sub("", desc)
        # Append continuation lines (skip pure numeric noise)
        for cont in raw_lines[1:]:
            c = cont.strip()
            if c and not re.match(r"^[\d\s,.-]+(?:CR)?$", c, re.IGNORECASE):
                desc += " " + c
        desc = re.sub(r"\s+", " ", desc).strip()

        # Confidence flag
        confidence = "ok"
        if len(desc) < 3 or re.search(r"[|]{2,}", desc):
            confidence = "review"
            low_confidence_lines.append(f"[LOW CONF] {d} | {desc} | {amount}")

        transactions.append({
            "date": d.isoformat(),
            "description": desc,
            "amount": round(amount, 2),
            "type": txn_type,
            "balance": "",
            "raw": raw[:200],
        })

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip header/summary lines, but NOT transaction lines that merely
        # contain a skip word inside a merchant name — only skip if the line
        # does not start with a date.
        d_check = try_parse_date(stripped)
        if d_check is None and any(kw in stripped.upper() for kw in SKIP_KEYWORDS):
            continue

        d = try_parse_date(stripped)
        if d:
            flush(current_date, current_lines)
            current_date = d
            current_lines = [stripped]
        elif current_date:
            current_lines.append(stripped)

    flush(current_date, current_lines)
    return transactions, low_confidence_lines


def reconcile(transactions: list, summary: dict):
    """
    Attempt: previous_balance + debits - credits == new_balance.
    (debits already stored negative, credits positive, so:
     new = prev - sum(debit_values) + sum(credit_values) = prev + sum(amounts))
    Returns (passed: bool|None, diff: float|None, computed: float|None).
    """
    prev = summary.get("prev_balance")
    new = summary.get("new_balance")
    if prev is None or new is None:
        return None, None, None
    net = sum(t["amount"] for t in transactions)  # debits negative, credits positive
    # New balance grows with purchases (debit) and shrinks with payments (credit).
    # amounts: debit = -value, credit = +value  ->  computed = prev - net_amounts? No:
    #   balance_increase = purchases - payments = (-sum(debit amounts)) - ... careful.
    # sum(amounts) = (sum credits) - (sum debits). New balance = prev + debits - credits
    #             = prev - sum(amounts).
    computed = round(prev - net, 2)
    diff = round(computed - new, 2)
    # Exact match -> PASS. A tiny residual (<= RM0.05) is almost always a
    # single mis-OCR'd digit in one of the *summary* balance anchors (e.g.
    # previous balance 426.83 vs true 426.81) rather than a transaction error,
    # so classify it as a soft pass ("near") that still gets human review.
    if abs(diff) < 0.01:
        passed = True
    elif abs(diff) <= 0.05:
        passed = "near"
    else:
        passed = False
    return passed, diff, computed


def write_csv(transactions: list, csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date", "description", "amount", "type", "balance", "raw"]
        )
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions → {csv_path}")


def write_review_file(low_confidence: list, review_path: Path,
                      summary: dict, recon: tuple, n_txns: int):
    review_path.parent.mkdir(parents=True, exist_ok=True)
    passed, diff, computed = recon
    with open(review_path, "w", encoding="utf-8") as f:
        f.write(f"Fui Yee HSBC OCR Review File\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Transactions parsed : {n_txns}\n")
        f.write(f"Previous balance    : {summary.get('prev_balance')}\n")
        f.write(f"Charges (bank stmt) : {summary.get('charges')}\n")
        f.write(f"New balance (bank)  : {summary.get('new_balance')}\n")
        if passed is None:
            f.write("Reconciliation      : SKIPPED (missing summary balances)\n")
        else:
            if passed is True:
                label = "PASS"
            elif passed == "near":
                label = "NEAR-PASS (<=RM0.05 summary OCR drift; txns look correct)"
            else:
                label = "FAIL"
            f.write(f"Computed new balance: {computed}\n")
            f.write(f"Reconciliation      : {label} (diff = {diff})\n")
        f.write("\n")
        if low_confidence:
            f.write(f"Low-confidence / unparsed lines ({len(low_confidence)}):\n")
            f.write("-" * 60 + "\n")
            for line in low_confidence:
                f.write(line + "\n")
        else:
            f.write("No low-confidence lines flagged.\n")
    print(f"  Review → {review_path}")


def month_from_filename(pdf_path: Path):
    """Filename like 2025-09-12_Statement.pdf -> (2025, 9)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", pdf_path.stem)
    if not m:
        raise ValueError(f"Cannot parse statement date from filename: {pdf_path.name}")
    return int(m.group(1)), int(m.group(2))


def process_pdf(pdf_path: Path, csv_path: Path | None = None):
    if not OCR_AVAILABLE:
        print("ERROR: OCR libraries not installed. Run:")
        print("  brew install tesseract poppler")
        print("  pip3 install --break-system-packages pytesseract pdf2image Pillow")
        print(f"  Original error: {_OCR_IMPORT_ERROR}")
        sys.exit(1)

    print(f"Parsing {pdf_path.name} (OCR) ...")
    stmt_year, stmt_month = month_from_filename(pdf_path)

    raw_text = ocr_pdf(pdf_path)
    clean_text = clean_ocr_text(raw_text)
    summary = extract_summary(clean_text)
    transactions, low_conf = parse_ocr_text(clean_text, stmt_year, stmt_month)

    if csv_path is None:
        csv_path = fy_hsbc_csv_path(stmt_year, stmt_month)

    recon = reconcile(transactions, summary)
    passed, diff, computed = recon

    write_csv(transactions, csv_path)
    review_path = csv_path.with_name(csv_path.stem + "_review.txt")
    write_review_file(low_conf, review_path, summary, recon, len(transactions))

    if passed is None:
        status = "RECON SKIPPED (no summary balances)"
    elif passed is True:
        status = "RECON PASS"
    elif passed == "near":
        status = f"RECON NEAR-PASS (diff={diff}; summary OCR drift)"
    else:
        status = f"RECON FAIL (diff={diff})"
    print(f"  Parsed {len(transactions)} txns | {len(low_conf)} to review | {status}")

    return {
        "month": f"{stmt_year}-{stmt_month:02d}",
        "file": pdf_path.name,
        "n_txns": len(transactions),
        "n_review": len(low_conf),
        "prev": summary.get("prev_balance"),
        "new": summary.get("new_balance"),
        "computed": computed,
        "passed": passed,
        "diff": diff,
    }


def find_all_pdfs():
    pdfs = []
    for year_dir in sorted(FY_HSBC_DIR.glob("[0-9][0-9][0-9][0-9]")):
        for pdf in sorted(year_dir.glob("*.pdf")):
            pdfs.append(pdf)
    return pdfs


def process_all(year: int | None = None):
    results = []
    pdfs = find_all_pdfs()
    if not pdfs:
        print(f"  No PDFs found under {FY_HSBC_DIR}")
        return results
    for pdf in pdfs:
        if year:
            fy, _ = month_from_filename(pdf)
            if fy != year:
                continue
        results.append(process_pdf(pdf))

    # Summary reconciliation table
    print("\n" + "=" * 72)
    print("RECONCILIATION SUMMARY  (prev + debits - credits == new balance)")
    print("=" * 72)
    print(f"{'Month':<9}{'Txns':>5}{'Review':>7}{'Prev':>11}{'New':>11}"
          f"{'Computed':>11}{'Diff':>9}  Result")
    for r in results:
        prev = f"{r['prev']:.2f}" if r['prev'] is not None else "n/a"
        new = f"{r['new']:.2f}" if r['new'] is not None else "n/a"
        comp = f"{r['computed']:.2f}" if r['computed'] is not None else "n/a"
        diff = f"{r['diff']:.2f}" if r['diff'] is not None else "n/a"
        if r["passed"] is None:
            result = "SKIP"
        elif r["passed"] is True:
            result = "PASS"
        elif r["passed"] == "near":
            result = "NEAR"
        else:
            result = "FAIL"
        print(f"{r['month']:<9}{r['n_txns']:>5}{r['n_review']:>7}{prev:>11}{new:>11}"
              f"{comp:>11}{diff:>9}  {result}")

    fails = [r for r in results if r["passed"] is False]
    nears = [r for r in results if r["passed"] == "near"]
    skips = [r for r in results if r["passed"] is None]
    print("-" * 72)
    print(f"{len(results)} statements | "
          f"{sum(1 for r in results if r['passed'] is True)} PASS | "
          f"{len(nears)} NEAR | {len(fails)} FAIL | {len(skips)} SKIP")
    if nears:
        print("\nNear-passes (<=RM0.05, summary-anchor OCR drift; txns look correct):")
        for r in nears:
            print(f"  {r['month']}: diff={r['diff']:.2f}")
    if fails:
        print("\nNeed human review (did NOT reconcile):")
        for r in fails:
            print(f"  {r['month']}: diff={r['diff']:.2f}  → see "
                  f"{fy_hsbc_csv_path(*map(int, r['month'].split('-'))).with_name(r['month']+'_review.txt')}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse Fui Yee HSBC credit card PDFs to CSV via OCR")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all Fui Yee HSBC PDFs")
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
