"""
AmBank Credit Card PDF Statement Parser
=========================================
Extracts transactions from AmBank Credit Card statement PDFs and writes CSV.

The consolidated statement covers multiple cards. Only transactions belonging to
the card defined in config.AMBCC_TARGET_CARD are extracted (Samuel's CARz Card
Gold VISA (S) 4293 1307 0071 4622).

Statement format (AMBCC-PDF-YYYY-MM.pdf):
  - Table columns: Transaction Date | Posting Date | Description | Amount (RM)
  - Date format: DD MMM YY (e.g. "06 OCT 25")
  - Amounts: plain number for charges; number + "CR" for credits/payments
  - Card sections delimited by: "CardType (P/S) NNNN NNNN NNNN NNNN" headers,
    "PREVIOUS BALANCE", and "SUB TOTAL" lines
  - Foreign currency items have a continuation line: "USD 70.20", "SGD 28.50"
  - Long descriptions may wrap to the next line (no leading dates on continuation)

Amount convention in output CSV:
  - Negative  = charge/purchase (you owe more)
  - Positive  = payment/credit/cashback (reduces what you owe)

Usage:
    python3 parse_ambcc.py "Bank Statements/Samuel/Ambank Ccard/AMBCC-PDF-2025-11.pdf"
    python3 parse_ambcc.py --all
    python3 parse_ambcc.py --year 2025
"""

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
import config

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"

# Transaction line: starts with two "DD MMM YY" patterns (transaction date + posting date)
# Groups: 1=txn_day, 2=txn_mon, 3=txn_yr, 4=rest (posting date + desc + amount)
TXN_LINE_RE = re.compile(
    rf"^(\d{{2}})\s+({MON})\s+(\d{{2}})\s+(\d{{2}}\s+{MON}\s+\d{{2}}\s+.+)$",
    re.IGNORECASE,
)

# Posting date prefix — stripped from the captured remainder of a TXN_LINE_RE match
POSTING_DATE_RE = re.compile(rf"^\d{{2}}\s+{MON}\s+\d{{2}}\s+", re.IGNORECASE)

# Amount at end of a line: digits/commas, 2 decimals, optional "CR"
AMOUNT_TAIL_RE = re.compile(r"([\d,]+\.\d{2})\s*(CR)?\s*$", re.IGNORECASE)

# 16-digit card number in "NNNN NNNN NNNN NNNN" format
CARD_NUM_RE = re.compile(r"\b(\d{4}\s+\d{4}\s+\d{4}\s+\d{4})\b")

# Foreign currency continuation line: "USD 70.20" or "SGD 28.50"
FX_LINE_RE = re.compile(r"^([A-Z]{3})\s+([\d,]+\.\d{2})\s*$")

# Pages with 3+ of these phrases are T&C boilerplate — skip entirely
_BOILERPLATE_MARKERS = [
    "finance charge", "minimum monthly repayment", "cardholder agreement",
    "terms and conditions", "terma & syarat", "payment procedure",
    "interest-free period", "tempoh ihsan", "outstanding balance",
]

# Non-transaction lines that appear within the transaction section
_SKIP_SUBSTRINGS = [
    "transaction date", "tarikh transaksi",
    "posting date", "tarikh catatan",
    "transaction description", "butir-butir transaksi",
    "amaun (rm)",
    "please see overleaf",
    "previous balance", "baki sebelumnya",
    "sub total", "jumlah kecil",
    "total current balance", "jumlah baki semasa",
    "note: *subject",
    "note / nota",
    "the enrich points",
    "mata ganjaran enrich",
    "the above shaded",
    "lajur berlorek",
]


def _is_boilerplate_page(text: str) -> bool:
    lower = text.lower()
    return sum(1 for m in _BOILERPLATE_MARKERS if m in lower) >= 3


def _card_matches_target(card_label: str) -> bool:
    """Return True if card_label contains the target card number (digits only)."""
    digits = re.sub(r"\s+", "", card_label)
    return config.AMBCC_TARGET_CARD in digits


def parse_statement(pdf_path: Path) -> list[dict]:
    """
    Parse a single AmBank CC PDF.
    Returns transactions for config.AMBCC_TARGET_CARD only.
    Each dict: {date, description, amount, type, card, raw}
    """
    # Collect text lines from non-boilerplate pages
    all_lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text or _is_boilerplate_page(text):
                continue
            all_lines.extend(text.splitlines())

    transactions: list[dict] = []
    current_card   = "unknown"
    in_target_card = False          # True only while inside the target card's section
    current_date: date | None = None
    current_parts: list[str] = []  # first element = desc+amount from the transaction row
    current_fx:   list[str] = []  # FX annotation lines (kept separate from current_parts)
    in_txn_section = False

    def flush():
        nonlocal current_date, current_parts, current_fx
        if current_date is None or not current_parts or not in_target_card:
            current_date  = None
            current_parts = []
            current_fx    = []
            return

        # The amount is always at the tail of the FIRST accumulated line (Amount column).
        # Continuation lines extend the description and appear after the amount on-screen.
        first_line = current_parts[0]
        m = AMOUNT_TAIL_RE.search(first_line)
        if not m:
            # Fallback: try the full joined text
            full = " ".join(current_parts)
            m = AMOUNT_TAIL_RE.search(full)
            if not m:
                current_date  = None
                current_parts = []
                current_fx    = []
                return
            raw_amount = float(m.group(1).replace(",", ""))
            is_cr = bool(m.group(2))
            desc  = full[:m.start()].strip()
        else:
            raw_amount = float(m.group(1).replace(",", ""))
            is_cr      = bool(m.group(2))
            desc_first = first_line[:m.start()].strip()
            continuation = " ".join(current_parts[1:]).strip()
            desc = (desc_first + " " + continuation).strip() if continuation else desc_first

        if current_fx:
            desc = desc + " " + " ".join(current_fx)
        desc = re.sub(r"\s+", " ", desc).strip()

        amount   = raw_amount if is_cr else -raw_amount
        txn_type = "credit" if is_cr else "debit"

        transactions.append({
            "date":        current_date.isoformat(),
            "description": desc,
            "amount":      amount,
            "type":        txn_type,
            "card":        current_card,
            "raw":         " ".join(current_parts)[:200],
        })
        current_date  = None
        current_parts = []
        current_fx    = []

    for raw_line in all_lines:
        line  = raw_line.strip()
        if not line:
            continue
        lower = line.lower()

        # ── Transaction section boundaries ────────────────────────────────────
        if "your transaction details" in lower or "transaksi terperinci anda" in lower:
            in_txn_section = True
            continue
        if "your rewards points" in lower or "rumusan mata ganjaran" in lower:
            flush()
            in_txn_section = False
            continue
        if not in_txn_section:
            continue

        # ── Card section header ───────────────────────────────────────────────
        # e.g. "CARz Card Gold VISA (S) 4293 1307 0071 4622"
        # Card number never leads the line inside the transaction section
        m_card = CARD_NUM_RE.search(line)
        if m_card and m_card.start() > 0:
            flush()
            card_num  = m_card.group(1).replace(" ", "")
            card_name = line[:m_card.start()].strip()
            card_name = re.sub(r"\s*\([PS]u?\)\s*$", "", card_name).strip()
            current_card   = f"{card_name} {card_num}".strip()
            in_target_card = _card_matches_target(card_num)
            continue

        # ── Skip page chrome and repeating table headers ──────────────────────
        if re.match(r"^CONV\s+\d+\s+of\s+\d+", line, re.IGNORECASE):
            continue
        if any(p in lower for p in _SKIP_SUBSTRINGS):
            flush()
            continue

        # ── Transaction line: two "DD MMM YY" dates at the start ─────────────
        m_txn = TXN_LINE_RE.match(line)
        if m_txn:
            flush()
            day       = int(m_txn.group(1))
            month_num = MONTH_MAP[m_txn.group(2).lower()]
            year_2d   = int(m_txn.group(3))
            year      = 2000 + year_2d
            try:
                current_date = date(year, month_num, day)
            except ValueError:
                current_date = None
                continue
            rest = POSTING_DATE_RE.sub("", m_txn.group(4)).strip()
            current_parts = [rest]
            current_fx    = []
            continue

        # ── Continuation lines ────────────────────────────────────────────────
        if current_date is not None and in_target_card:
            m_fx = FX_LINE_RE.match(line)
            if m_fx:
                current_fx.append(f"[{m_fx.group(1)} {m_fx.group(2)}]")
            else:
                current_parts.append(line)

    flush()
    return transactions


def write_csv(transactions: list[dict], csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "description", "amount", "type", "card", "raw"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions → {csv_path}")


def process_pdf(pdf_path: Path, csv_path: Path | None = None):
    print(f"Parsing {pdf_path.name} ...")
    transactions = parse_statement(pdf_path)

    if csv_path is None:
        stem  = pdf_path.stem   # AMBCC-PDF-YYYY-MM
        parts = stem.split("-")
        csv_path = config.ambcc_csv_path(int(parts[2]), int(parts[3]))

    write_csv(transactions, csv_path)

    debits  = sum(1 for t in transactions if t["type"] == "debit")
    credits = sum(1 for t in transactions if t["type"] == "credit")
    print(f"  Charges  : {debits}   Payments/credits: {credits}")
    return transactions


def process_all(year: int | None = None):
    """Process all AmBank CC PDFs on file."""
    import glob as _glob
    pdf_dir = config.SAMUEL_AMBCC_DIR
    found = sorted(_glob.glob(str(pdf_dir / "AMBCC-PDF-*.pdf")))
    months = []
    for p in found:
        name = Path(p).stem  # AMBCC-PDF-YYYY-MM
        parts = name.split("-")
        try:
            months.append((int(parts[2]), int(parts[3])))
        except (IndexError, ValueError):
            pass
    for y, m in months:
        if year and y != year:
            continue
        pdf = config.ambcc_pdf_path(y, m)
        if pdf.exists():
            process_pdf(pdf)
        else:
            print(f"  MISSING: {pdf}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse AmBank Credit Card PDF statements to CSV (target card only)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf",    nargs="?", help="Path to a single PDF file")
    group.add_argument("--all",  action="store_true", help="Process all AmBank CC PDFs on file")
    group.add_argument("--year", type=int,            help="Process all PDFs for a given year")
    parser.add_argument("--output", help="Output CSV path (single-file mode only)")
    args = parser.parse_args()

    if args.all or args.year:
        process_all(year=args.year)
    elif args.pdf:
        out = Path(args.output) if args.output else None
        process_pdf(Path(args.pdf), out)
    else:
        parser.print_help()
