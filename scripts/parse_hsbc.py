"""
HSBC Credit Card PDF Statement Parser (OCR)
=============================================
HSBC statements are image-only PDFs (no extractable text).
This script converts each page to an image then runs OCR via tesseract.

Prerequisites:
    brew install tesseract poppler
    pip3 install --break-system-packages pytesseract pdf2image Pillow

Usage:
    python3 parse_hsbc.py "Bank Statements/Samuel/HSBC Ccard/HSBC-PDF-2025-09.pdf"
    python3 parse_hsbc.py --all
    python3 parse_hsbc.py --year 2026
"""

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

# Try importing OCR libraries — helpful error if not installed
try:
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError as e:
    OCR_AVAILABLE = False
    _OCR_IMPORT_ERROR = str(e)

# HSBC formats:
#   "03 SEP 03 SEP DESCRIPTION 25.00"  — two DD MMM dates (posting + transaction)
#   "01 Sep 25 DESCRIPTION 25.00"      — single date with 2-digit year
#   "01/09/25 DESCRIPTION"             — slash format
MON = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
# Two-date format (no year — infer from statement): "03 SEP 03 SEP"
DATE_RE_TWO  = re.compile(rf"^(\d{{1,2}})\s+{MON}\s+\d{{1,2}}\s+{MON}\b", re.IGNORECASE)
# Single date with 2-digit year: "01 Sep 25"
DATE_RE1     = re.compile(rf"^(\d{{1,2}})\s+{MON}\s+(\d{{2}})\b", re.IGNORECASE)
# Slash format: "01/09/25"
DATE_RE2     = re.compile(r"^(\d{2})/(\d{2})/(\d{2})\b")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}

# Amount pattern: optional minus, digits, commas, dot, 2 decimal places, optional CR suffix
AMOUNT_RE = re.compile(r"-?[\d,]+\.\d{2}(?:CR)?", re.IGNORECASE)

SKIP_KEYWORDS = [
    "HSBC", "STATEMENT", "CREDIT CARD", "PAGE", "ACCOUNT NUMBER",
    "CREDIT LIMIT", "AVAILABLE CREDIT", "MINIMUM PAYMENT", "PAYMENT DUE",
    "OPENING BALANCE", "CLOSING BALANCE", "TOTAL", "BROUGHT FORWARD",
    "CARRIED FORWARD", "BALANCE B/F", "BALANCE C/F",
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
    """Apply common OCR correction heuristics."""
    text = re.sub(r"\bO(\d)", r"0\1", text)    # O -> 0 before digit
    text = re.sub(r"(\d)O\b", r"\g<1>0", text) # digit + O -> digit + 0
    text = re.sub(r"\bl(\d)", r"1\1", text)    # lowercase l -> 1 before digit
    # Fix amounts where OCR uses comma as decimal: "45,60" -> "45.60"
    # Also handles CR suffix: "1,415,00CR" -> "1,415.00CR"
    text = re.sub(r"(\d),(\d{2})(?=[^,\d]|$)", r"\1.\2", text)
    return text


def is_boilerplate_heavy(page_text: str) -> bool:
    """Return True if a page looks like T&C / legal boilerplate rather than transactions."""
    boilerplate_markers = [
        "minimum monthly payment", "finance charge", "cardholder agreement",
        "terms and conditions", "terma & syarat", "caj kewangan",
        "pembayaran bulanan minimum", "agensi kaunseling", "bank negara malaysia",
        "interest-free period", "tempoh ihsan", "kadar faedah tahunan",
    ]
    lower = page_text.lower()
    hits = sum(1 for marker in boilerplate_markers if marker in lower)
    return hits >= 3  # 3+ boilerplate phrases = T&C page


def parse_ocr_text(text: str, stmt_year: int, stmt_month: int) -> list:
    """
    Parse OCR text into transactions.
    Returns list of dicts: {date, description, amount, type, confidence, raw}
    """
    lines = text.splitlines()
    transactions = []
    current_date = None
    current_lines = []
    low_confidence_lines = []

    def try_parse_date(line: str):
        stripped = line.strip()

        # Two-date format: "03 SEP 03 SEP ..." — infer year from statement
        m = DATE_RE_TWO.match(stripped)
        if m:
            day = int(m.group(1))
            month_num = MONTH_MAP[m.group(2).lower()]
            # Infer year: if month > stmt_month it belongs to previous year
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

        # Strip HSBC statement boilerplate that bleeds into transaction lines via OCR:
        #   "Your charge(s) for this month RM..." — monthly statement summary
        #   "***Please forward your payment..."  — payment reminder
        #   "Summary of Instalment Plan ..."     — instalment table (unbilled balances)
        raw_parsed = re.sub(r'\s*Your charge\(s\).*', '', raw, flags=re.IGNORECASE | re.DOTALL)
        raw_parsed = re.sub(r'\s*\*{3}Please.*', '', raw_parsed, flags=re.IGNORECASE | re.DOTALL)
        raw_parsed = re.sub(r'\s*Summary of Instalment.*', '', raw_parsed, flags=re.IGNORECASE | re.DOTALL)

        amounts = AMOUNT_RE.findall(raw_parsed)
        if not amounts:
            low_confidence_lines.append(f"[NO AMOUNT] {raw[:100]}")
            return

        # Last amount is the transaction amount (credit card: positive = purchase, negative = payment)
        amt_raw = amounts[-1]
        is_cr = amt_raw.upper().endswith("CR")
        amt_str = amt_raw.rstrip("CcRr").replace(",", "")
        try:
            amount = float(amt_str)
            if is_cr:
                amount = -amount  # CR = credit/payment, reduces card balance
        except ValueError:
            low_confidence_lines.append(f"[BAD AMOUNT] {raw[:100]}")
            return

        # Description: first line minus date prefix(es), minus amount, minus statement noise
        desc = raw_lines[0]
        desc = re.sub(r'\s*Your charge\(s\).*', '', desc, flags=re.IGNORECASE | re.DOTALL)
        desc = re.sub(r'\s*\*{3}Please.*', '', desc, flags=re.IGNORECASE | re.DOTALL)
        desc = re.sub(r'\s*Summary of Instalment.*', '', desc, flags=re.IGNORECASE | re.DOTALL)
        # Remove two-date prefix: "03 SEP 03 SEP"
        desc = re.sub(r"^\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*", "", desc, flags=re.IGNORECASE)
        # Remove single date with year: "01 Sep 25"
        desc = re.sub(r"^\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{2}\s*", "", desc, flags=re.IGNORECASE)
        # Remove slash date: "01/09/25"
        desc = re.sub(r"^\d{2}/\d{2}/\d{2}\s*", "", desc)
        # Remove amounts
        desc = AMOUNT_RE.sub("", desc)
        # Append continuation lines (skip pure noise)
        for cont in raw_lines[1:]:
            c = cont.strip()
            if c and not re.match(r"^[\d\s,.-]+$", c):
                desc += " " + c
        desc = re.sub(r"\s+", " ", desc).strip()

        # Credit card: positive = purchase (debit to expenses), negative = payment/credit
        if amount < 0:
            txn_type = "credit"
        else:
            txn_type = "debit"

        # Flag for review if description is very short or amount looks suspicious
        confidence = "ok"
        if len(desc) < 3 or re.search(r"[|]{2,}", desc):
            confidence = "review"
            low_confidence_lines.append(f"[LOW CONF] {d} | {desc} | {amount}")

        transactions.append({
            "date": d.isoformat(),
            "description": desc,
            "amount": -amount if txn_type == "debit" else amount,  # normalise: negative = expense
            "type": txn_type,
            "confidence": confidence,
            "raw": raw[:200],
        })

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(kw in stripped.upper() for kw in SKIP_KEYWORDS):
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


def write_csv(transactions: list, csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "description", "amount", "type", "confidence", "raw"])
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions → {csv_path}")


def write_review_file(low_confidence: list, review_path: Path):
    if not low_confidence:
        return
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with open(review_path, "w", encoding="utf-8") as f:
        f.write(f"HSBC OCR Review File — {len(low_confidence)} items need manual check\n")
        f.write("=" * 60 + "\n\n")
        for line in low_confidence:
            f.write(line + "\n")
    print(f"  ⚠ {len(low_confidence)} low-confidence items → {review_path}")


def process_pdf(pdf_path: Path, csv_path: Path | None = None):
    if not OCR_AVAILABLE:
        print(f"ERROR: OCR libraries not installed. Run:")
        print(f"  brew install tesseract poppler")
        print(f"  pip3 install --break-system-packages pytesseract pdf2image Pillow")
        print(f"  Original error: {_OCR_IMPORT_ERROR}")
        sys.exit(1)

    print(f"Parsing {pdf_path.name} (OCR) ...")
    stem = pdf_path.stem  # HSBC-PDF-YYYY-MM
    parts = stem.split("-")
    stmt_year  = int(parts[2])
    stmt_month = int(parts[3])

    raw_text = ocr_pdf(pdf_path)
    clean_text = clean_ocr_text(raw_text)
    transactions, low_conf = parse_ocr_text(clean_text, stmt_year, stmt_month)

    if csv_path is None:
        csv_path = config.hsbc_csv_path(stmt_year, stmt_month)

    write_csv(transactions, csv_path)

    review_path = csv_path.with_name(csv_path.stem + "_review.txt")
    write_review_file(low_conf, review_path)

    print(f"  Parsed {len(transactions)} transactions  ({len(low_conf)} need review)")
    return transactions


def process_all(year: int | None = None):
    months = [
        (2025, 9), (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2), (2026, 3), (2026, 4),
    ]
    for y, m in months:
        if year and y != year:
            continue
        pdf = config.hsbc_pdf_path(y, m)
        if pdf.exists():
            process_pdf(pdf)
        else:
            print(f"  MISSING: {pdf}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse HSBC credit card PDFs to CSV via OCR")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all HSBC PDFs on file")
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
