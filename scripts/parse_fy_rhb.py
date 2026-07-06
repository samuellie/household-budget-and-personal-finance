"""
RHB Credit Card PDF Statement Parser (Fui Yee)
================================================
Extracts transactions from Fui Yee's RHB Credit Card statement PDFs and writes CSV.

The RHB statement is a *combined* statement covering MULTIPLE cards
(e.g. SHELL VISA CARD 4570-..., MC CASHBACK CARD 5400-...). All cards belong to
the single RHB ledger account, so ALL cards' transactions are combined into one
output CSV. The originating card's last-4 is recorded in the `raw` field.

Statement format (RHB_MmmYY.pdf, e.g. RHB_Aug25.pdf, RHB_June26.pdf):
  - RHB's extracted text frequently has NO SPACES between words, e.g.
        StatementDate/TarikhPenyata:22Aug2025
        4570-6628-0130-6688WONGFUIYEE
    so regexes anchor on digits / dates / amount patterns, not word boundaries.
  - Statement date from header: "StatementDate/TarikhPenyata:22Aug2025"
  - Account-details table lists each card + OutstandingBalance(RM) (= new balance)
    and a Total/Jumlah row (= combined new balance).
  - Transaction section (per card):
        <cardnum>WONGFUIYEE
        OPENINGBALANCE/BAKIMULA          <amount>
        CURRENTRETAILINTERESTIS/...   (skip)
        18.00%                        (skip)
        <PostDate> <TxnDate> <Description> <Amount>[CR]
        ...
        CLOSINGBALANCE/BAKIAKHIR         <amount>
  - Dates appear as "DDMmm" (e.g. "07Aug", "29Jul") — no year. Year is inferred
    from the statement period, handling the Dec->Jan and month-before boundary.
  - Amounts: plain number for charges; number + "CR" for payments/refunds/credits.

Amount convention in output CSV:
  - Negative  = charge/purchase (you owe more)
  - Positive  = payment/refund/credit (CR — reduces what you owe)

Usage:
    python3 parse_fy_rhb.py "Bank Statements/Fui Yee/RHB/2025/RHB_Aug25.pdf"
    python3 parse_fy_rhb.py --all
    python3 parse_fy_rhb.py --year 2025
"""

import argparse
import csv
import glob
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
import config

# ── Directories ───────────────────────────────────────────────────────────────
FY_RHB_DIR = config.BANK_STATEMENTS_DIR / "Fui Yee" / "RHB"
FY_RHB_CSV_DIR = config.CSV_DIR / "fuiyee" / "rhb"


def fy_rhb_csv_path(year: int, month: int) -> Path:
    return FY_RHB_CSV_DIR / f"{year}-{month:02d}.csv"


MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # long / alternate forms found in filenames
    "june": 6, "july": 7, "sept": 9,
}

MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"

# Statement date in header, e.g. "StatementDate/TarikhPenyata:22Aug2025"
STMT_DATE_RE = re.compile(
    rf"StatementDate\s*/?\s*TarikhPenyata\s*:?\s*(\d{{1,2}})\s*({MON})\s*(\d{{4}})",
    re.IGNORECASE,
)

# Card section header inside the transaction area, e.g.
#   "4570-6628-0130-6688WONGFUIYEE"
CARD_HEADER_RE = re.compile(r"^(\d{4}-\d{4}-\d{4}-\d{4})\s*[A-Z]")

# Card row in the account-details table, e.g.
#   "4570-6628-0130-6688 SHELLVISACARD 0.00 0.00 0.00"
CARD_TABLE_ROW_RE = re.compile(
    r"^(\d{4}-\d{4}-\d{4}-\d{4})\s+.*?([\d,]+\.\d{2})\s+[\d,]+\.\d{2}\s+[\d,]+\.\d{2}\s*$"
)

# Opening / closing balance lines (may have no space before the amount).
OPENING_RE = re.compile(r"OPENINGBALANCE\s*/?\s*BAKIMULA\s*([\d,]+\.\d{2})", re.IGNORECASE)
CLOSING_RE = re.compile(r"CLOSINGBALANCE\s*/?\s*BAKIAKHIR\s*([\d,]+\.\d{2})", re.IGNORECASE)

# Transaction line: two leading "DDMmm" dates, description, trailing amount[CR].
#   groups: 1=post_day 2=post_mon 3=txn_day 4=txn_mon 5=desc 6=amount 7=CR
TXN_RE = re.compile(
    rf"^(\d{{1,2}})\s*({MON})\s+(\d{{1,2}})\s*({MON})\s+(.*?)\s+([\d,]+\.\d{{2}})\s*(CR)?\s*$",
    re.IGNORECASE,
)

# Amount at end of an arbitrary line (used when a txn wraps / for continuation)
AMOUNT_TAIL_RE = re.compile(r"([\d,]+\.\d{2})\s*(CR)?\s*$", re.IGNORECASE)

# Lines to skip inside a card section (interest-rate annotations, headers).
_SKIP_SUBSTR = [
    "currentretailinterest", "kadarfaedahsemasa",
    "yourtransactiondetails", "transaksiterperinci",
    "postingdate", "transactiondate", "tarikhpos", "tarikhtransaksi",
    "description", "amount(rm)", "deskripsi", "amaun",
]


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _stmt_period_from_filename(pdf_path: Path) -> tuple[int, int]:
    """RHB_Aug25.pdf -> (2025, 8); RHB_June26.pdf -> (2026, 6)."""
    stem = pdf_path.stem  # RHB_Aug25
    m = re.match(r"RHB[_-]([A-Za-z]+)(\d{2})$", stem)
    if not m:
        raise ValueError(f"Cannot parse statement month from filename: {pdf_path.name}")
    mon_key = m.group(1).lower()
    if mon_key not in MONTH_MAP:
        raise ValueError(f"Unknown month token '{m.group(1)}' in {pdf_path.name}")
    month = MONTH_MAP[mon_key]
    year = 2000 + int(m.group(2))
    return year, month


def _infer_year(txn_month: int, stmt_year: int, stmt_month: int) -> int:
    """
    Transactions carry no year. A statement dated in `stmt_month` can contain
    transactions from the previous month (e.g. an Aug statement listing 29Jul).
    If the transaction month is *ahead* of the statement month, it belongs to
    the previous calendar year (Dec txns on a Jan statement).
    """
    if txn_month > stmt_month:
        return stmt_year - 1
    return stmt_year


def parse_statement(pdf_path: Path) -> dict:
    """Parse a single RHB combined statement PDF."""
    file_year, file_month = _stmt_period_from_filename(pdf_path)

    lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines.extend(text.splitlines())

    # ── Statement date (for year inference) ───────────────────────────────────
    stmt_year, stmt_month = file_year, file_month
    for line in lines:
        m = STMT_DATE_RE.search(line.replace(" ", ""))
        if m:
            stmt_year = int(m.group(3))
            stmt_month = MONTH_MAP[m.group(2).lower()[:3]]
            break

    # ── Account-details table: per-card new (outstanding) balances + total ─────
    # These live BEFORE the transaction section. We capture them separately.
    table_new_balances: dict[str, float] = {}
    combined_new_balance: float | None = None
    in_txn_section = False

    for line in lines:
        stripped = line.strip()
        if "YOURTRANSACTIONDETAILS" in stripped.replace(" ", "").upper():
            in_txn_section = True
        if in_txn_section:
            break
        m = CARD_TABLE_ROW_RE.match(stripped)
        if m:
            table_new_balances[m.group(1)] = _money(m.group(2))
            continue
        # Total/Jumlah row: "Total/Jumlah 167.21 0.00 50.00"
        if stripped.replace(" ", "").upper().startswith("TOTAL/JUMLAH"):
            amts = re.findall(r"[\d,]+\.\d{2}", stripped)
            if amts:
                combined_new_balance = _money(amts[0])

    # ── Transaction section: iterate per card ─────────────────────────────────
    transactions: list[dict] = []
    cards: dict[str, dict] = {}  # cardnum -> {opening, closing, debits, credits}

    current_card: str | None = None
    in_txn = False

    def ensure_card(cardnum: str):
        if cardnum not in cards:
            cards[cardnum] = {
                "opening": None, "closing": None,
                "debits": 0.0, "credits": 0.0,
            }

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        nospace_upper = line.replace(" ", "").upper()

        if "YOURTRANSACTIONDETAILS" in nospace_upper:
            in_txn = True
            continue
        # End of transaction area (footer / notes) — stop consuming.
        if "IMPORTANTNOTES" in nospace_upper or "CREDITCARDFEATURESFORYOU" in nospace_upper:
            in_txn = False
            current_card = None
            continue
        if not in_txn:
            continue

        # ── Card section header: "<cardnum>WONGFUIYEE" ────────────────────────
        m_card = CARD_HEADER_RE.match(line)
        if m_card:
            current_card = m_card.group(1)
            ensure_card(current_card)
            continue

        if current_card is None:
            continue

        # ── Opening / closing balances ────────────────────────────────────────
        m_open = OPENING_RE.search(line.replace(" ", ""))
        if m_open:
            cards[current_card]["opening"] = _money(m_open.group(1))
            continue
        m_close = CLOSING_RE.search(line.replace(" ", ""))
        if m_close:
            cards[current_card]["closing"] = _money(m_close.group(1))
            continue

        low = line.lower().replace(" ", "")
        if any(s.replace(" ", "") in low for s in _SKIP_SUBSTR):
            continue
        if nospace_upper.startswith("TOTALOUTSTANDINGBALANCE"):
            continue

        # ── Transaction line ──────────────────────────────────────────────────
        m_txn = TXN_RE.match(line)
        if m_txn:
            txn_day = int(m_txn.group(3))
            txn_mon = MONTH_MAP[m_txn.group(4).lower()[:3]]
            desc = m_txn.group(5).strip()
            amount_val = _money(m_txn.group(6))
            is_cr = bool(m_txn.group(7))

            year = _infer_year(txn_mon, stmt_year, stmt_month)
            try:
                d = date(year, txn_mon, txn_day)
            except ValueError:
                continue

            signed = amount_val if is_cr else -amount_val
            txn_type = "credit" if is_cr else "debit"
            if is_cr:
                cards[current_card]["credits"] += amount_val
            else:
                cards[current_card]["debits"] += amount_val

            desc = re.sub(r"\s+", " ", desc).strip()
            last4 = current_card[-4:]
            transactions.append({
                "date": d.isoformat(),
                "description": desc,
                "amount": round(signed, 2),
                "type": txn_type,
                "balance": "",
                "raw": f"[{last4}] {line}"[:200],
            })
            continue

    # Prefer the account-details table's new balances (authoritative); fall back
    # to the per-section CLOSINGBALANCE if the table row was missing.
    for cardnum, info in cards.items():
        if info["closing"] is None:
            info["closing"] = table_new_balances.get(cardnum)

    return {
        "stmt_year": stmt_year,
        "stmt_month": stmt_month,
        "file_year": file_year,
        "file_month": file_month,
        "transactions": transactions,
        "cards": cards,
        "table_new_balances": table_new_balances,
        "combined_new_balance": combined_new_balance,
    }


def validate(result: dict) -> tuple[bool, float, list[str]]:
    """
    Reconcile each card: opening + debits - credits == closing.
    Returns (all_pass, combined_difference, per_card_detail_lines).
    """
    detail = []
    all_pass = True
    total_diff = 0.0
    for cardnum, info in sorted(result["cards"].items()):
        opening = info["opening"] or 0.0
        closing = info["closing"] if info["closing"] is not None else 0.0
        expected = opening + info["debits"] - info["credits"]
        diff = round(expected - closing, 2)
        total_diff += diff
        ok = abs(diff) < 0.01
        all_pass = all_pass and ok
        detail.append(
            f"    card {cardnum[-4:]}: open {opening:>10,.2f} "
            f"+ dr {info['debits']:>9,.2f} - cr {info['credits']:>9,.2f} "
            f"= {expected:>10,.2f} vs close {closing:>10,.2f}  "
            f"diff {diff:>7,.2f}  {'OK' if ok else 'MISMATCH'}"
        )
    return all_pass, round(total_diff, 2), detail


def write_csv(transactions: list[dict], csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "description", "amount", "type", "balance", "raw"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions -> {csv_path}")


def process_pdf(pdf_path: Path, csv_path: Path | None = None) -> dict:
    print(f"Parsing {pdf_path.name} ...")
    result = parse_statement(pdf_path)

    if csv_path is None:
        csv_path = fy_rhb_csv_path(result["stmt_year"], result["stmt_month"])

    write_csv(result["transactions"], csv_path)

    debits = sum(1 for t in result["transactions"] if t["type"] == "debit")
    credits = sum(1 for t in result["transactions"] if t["type"] == "credit")
    print(f"  Cards: {len(result['cards'])}   Charges: {debits}   Payments/credits: {credits}")

    all_pass, total_diff, detail = validate(result)
    for line in detail:
        print(line)
    status = "PASS" if all_pass else "FAIL"
    print(f"  RECONCILE {status}  (combined diff {total_diff:,.2f})")

    result["validation_pass"] = all_pass
    result["validation_diff"] = total_diff
    return result


def _discover_pdfs(year: int | None = None) -> list[Path]:
    found = []
    for pat in ("2025", "2026", "*"):
        found.extend(glob.glob(str(FY_RHB_DIR / pat / "RHB_*.pdf")))
    # de-dupe while preserving order
    seen = set()
    paths = []
    for p in sorted(found):
        if p in seen:
            continue
        seen.add(p)
        pp = Path(p)
        try:
            y, _ = _stmt_period_from_filename(pp)
        except ValueError:
            continue
        if year and y != year:
            continue
        paths.append(pp)
    return paths


def process_all(year: int | None = None):
    pdfs = _discover_pdfs(year)
    if not pdfs:
        print("No RHB PDFs found.")
        return

    summary = []
    for pdf in pdfs:
        result = process_pdf(pdf)
        summary.append((
            f"{result['stmt_year']}-{result['stmt_month']:02d}",
            len(result["transactions"]),
            "PASS" if result["validation_pass"] else "FAIL",
            result["validation_diff"],
        ))
        print()

    print("=" * 60)
    print("VALIDATION SUMMARY")
    print(f"{'Month':<10}{'Txns':>6}{'Status':>8}{'Diff':>12}")
    print("-" * 60)
    total_txns = 0
    for month, n, status, diff in summary:
        total_txns += n
        print(f"{month:<10}{n:>6}{status:>8}{diff:>12,.2f}")
    print("-" * 60)
    fails = [s for s in summary if s[2] == "FAIL"]
    print(f"{'TOTAL':<10}{total_txns:>6}{'':>8}")
    print(f"Statements: {len(summary)}   Passed: {len(summary) - len(fails)}   Failed: {len(fails)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse Fui Yee's RHB combined Credit Card PDF statements to CSV"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all RHB PDFs on file")
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
