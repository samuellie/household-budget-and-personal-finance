"""
CIMB Credit Card PDF Statement Parser (Fui Yee)
================================================
Extracts transactions from Fui Yee's CIMB Credit Card statement PDFs and writes CSV.

Statement format (MM_YY_CIMB.PDF, "CREDIT CARD STATEMENT / PENYATA KAD KREDIT"):
  - "Cards Summary / Ringkasan Kad" table lists each card with its
    Statement Balance (RM) — the authoritative new balance per card.
  - "Transaction Details / Transaksi Terperinci" sections, one per card, each
    delimited by a "PREVIOUS BALANCE <amt>" line and a "STATEMENT BALANCE <amt>" line.
  - Transaction rows: <Posting DD MMM> <Transaction DD MMM> <Description> <Amount>[CR]
    Only the transaction (2nd) date is used for the CSV date.
  - Amounts: plain number = purchase/charge/fee (debit); number + "CR" = payment/refund/credit.
  - Fee/interest lines (FINANCE CHARGES, LATE CHARGES, SERVICE TAX) are dated debit
    rows and are captured normally.
  - Foreign-currency items add a continuation line, e.g. "840U.S. DOLLAR 63.48"
    (leading digits = MCC/country code); these carry no RM amount and are folded
    into the description, never treated as their own transaction.
  - Payment rows wrap onto noise lines: "FROM WONG FUI YEE", "Payment Desc",
    "Credit Card Payment", and stray echoes of the amount. These are filtered out.
  - Promotional blurbs may appear between transaction rows; they have no leading
    date pair and are ignored.

A single statement can contain more than one card section (e.g. a renewed card
number, or a second VISA card). All sections belong to Fui Yee, so transactions
from every section are captured. Year boundaries are handled by comparing each
transaction month against the statement month (a Dec-dated txn on a Jan statement
belongs to the prior year).

Amount convention in output CSV:
  - Negative  = charge/purchase/fee (balance owed increases)
  - Positive  = payment/credit/refund/cashback (balance owed decreases)

Usage:
    python3 parse_fy_cimbcc.py "Bank Statements/Fui Yee/CIMB_CC/2025/03_25_CIMB.PDF"
    python3 parse_fy_cimbcc.py --all
    python3 parse_fy_cimbcc.py --year 2025
"""

import argparse
import csv
import glob as _glob
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent))
import config

# ── Constants ───────────────────────────────────────────────────────────────

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

MON = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"

# Transaction row: <posting DD MMM> <txn DD MMM> <description> <amount>[CR]
# Groups: 1=post_day 2=post_mon 3=txn_day 4=txn_mon 5=description 6=amount 7=CR flag
TXN_LINE_RE = re.compile(
    rf"^(\d{{1,2}})\s+({MON})\s+(\d{{1,2}})\s+({MON})\s+(.*?)\s+([\d,]+\.\d{{2}})\s*(CR)?\s*$",
    re.IGNORECASE,
)

# Summary / balance markers inside a card transaction section
PREVIOUS_BALANCE_RE = re.compile(r"PREVIOUS BALANCE\s+([\d,]+\.\d{2})", re.IGNORECASE)
STATEMENT_BALANCE_RE = re.compile(r"STATEMENT BALANCE\s+([\d,]+\.\d{2})", re.IGNORECASE)

# Cards Summary row: "5521-1540-0848-6890 CASH REBATE PLATINUM MC 8,500.00 1,534.77 76.74"
# Captures the three trailing amounts: credit limit, statement balance, minimum payment.
SUMMARY_ROW_RE = re.compile(
    r"^(\d{4}-\d{4}-\d{4}-\d{4})\s+.*?"
    r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"
)

# Continuation / noise lines that belong to the previous transaction (payment rows).
_PAYMENT_NOISE_RE = re.compile(
    r"^(FROM WONG FUI YEE|Payment Desc|Credit Card Payment|TRANSFER / TOP-UP TH.*)$",
    re.IGNORECASE,
)

# Bare numeric echo line (e.g. a payment amount repeated on its own line)
_BARE_AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")

# Footer / boilerplate continuation fragments that can bleed into the last
# transaction on a page (privacy notice, minimum-payment warning, contact block).
# These must never be folded into a description.
_FOOTER_MARKERS = (
    "CIMB GROUP HAS ISSUED", "PLEASE CALL +603", "WARNING ON PAYING",
    "PEMBAYARAN MINIMA BULANAN", "IF YOU MADE ONLY", "IF YOU MAKE ONLY",
    "IT WILL TAKE YOU LONGER", "PLEASE REFER TO THE BACK",
    "ALTERNATIVELY, YOU MAY", "JIKA ANDA HANYA", "FAEDAH KENA BAYAR",
    "SILA RUJUK", "SELAIN ITU", "FOR LOST /", "UNTUKLAPORANKAD",
    "PERTANYAAN ATAU ADUAN", "PERSEKUTUAN;", "AVAILABLE ON OUR WEBSITE",
    "WWW.", "E-MAIL:", "CONTACT / HUBUNGI", "PRIVACY NOTICE",
    "PERSONAL DATA PROTECTION", "AMARAN JIKA",
)

# Instalment-plan "setup" line printed in the month a plan begins, e.g.
#   "MUSEE PLATINUM TOKYO-12M : 0/12 MY 3,351.10"
# The sequence number "0/NN" marks the remaining principal placed on instalment —
# informational only, NOT a charge against the statement balance. Real monthly
# instalments carry a non-zero sequence ("01/12", "02/12", ...) and ARE charges.
_INSTALMENT_INFO_RE = re.compile(r":\s*0/\d+\b")


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _clean_desc(desc: str) -> str:
    """Tidy a merchant description: strip trailing country code, collapse spaces."""
    desc = re.sub(r"\s+", " ", desc).strip()
    # Trailing " MY" / " AE" / " IE" etc. country codes are noise
    desc = re.sub(r"\s+[A-Z]{2}$", "", desc).strip()
    return desc


def _resolve_year(txn_month: int, stmt_year: int, stmt_month: int) -> int:
    """
    Statements only print DD MMM (no year). A transaction dated in a month that is
    'ahead' of the statement month by more than ~2 months must belong to the prior
    year (e.g. a DEC transaction shown on a JAN statement).
    """
    if txn_month - stmt_month > 2:
        return stmt_year - 1
    return stmt_year


def parse_statement(pdf_path: Path, stmt_year: int, stmt_month: int) -> dict:
    """
    Parse a single CIMB CC PDF.
    Returns:
        {
          transactions: [ {date, description, amount, type, balance, raw}, ... ],
          summary_balance: float | None,   # sum of Cards Summary statement balances
          sections: [ {previous, statement, debits, credits}, ... ],
        }
    """
    all_lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            # Stop before the boilerplate legal appendix
            if "IMPORTANT INFORMATION" in text and "MAKLUMAT PENTING" in text:
                # keep the portion before that page's boilerplate marker
                head, _, _ = text.partition("IMPORTANT INFORMATION")
                all_lines.extend(head.splitlines())
                break
            all_lines.extend(text.splitlines())

    transactions: list[dict] = []
    sections: list[dict] = []          # reconciliation info per card section
    summary_balances: list[float] = []  # from Cards Summary table
    in_cards_summary = False
    in_txn_section = False

    # State for the current card transaction section
    cur_prev: float | None = None
    cur_debits = 0.0
    cur_credits = 0.0

    # State for multi-line transaction accumulation (extend description only)
    last_txn: dict | None = None

    def close_section(stmt_balance: float | None):
        nonlocal cur_prev, cur_debits, cur_credits, last_txn
        if cur_prev is not None:
            sections.append({
                "previous": cur_prev,
                "statement": stmt_balance,
                "debits": round(cur_debits, 2),
                "credits": round(cur_credits, 2),
            })
        cur_prev = None
        cur_debits = 0.0
        cur_credits = 0.0
        last_txn = None

    for raw_line in all_lines:
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()

        # ── Cards Summary table ──────────────────────────────────────────────
        if "CARDS SUMMARY" in upper or "RINGKASAN KAD" in upper:
            in_cards_summary = True
            continue
        if in_cards_summary:
            m = SUMMARY_ROW_RE.match(line)
            if m:
                # group(3) is the Statement Balance (RM) column
                summary_balances.append(_to_float(m.group(3)))
                continue
            # Leave summary mode once we hit the transaction details header
            if "TRANSACTION DETAILS" in upper or "TRANSAKSI TERPERINCI" in upper:
                in_cards_summary = False
                in_txn_section = True
                last_txn = None
                continue
            # otherwise stay in summary mode scanning for more rows
            continue

        # ── Enter transaction details ────────────────────────────────────────
        if "TRANSACTION DETAILS" in upper or "TRANSAKSI TERPERINCI" in upper:
            in_txn_section = True
            last_txn = None
            continue
        if not in_txn_section:
            continue

        # ── Section boundaries: PREVIOUS BALANCE / STATEMENT BALANCE ─────────
        m_prev = PREVIOUS_BALANCE_RE.search(line)
        if m_prev:
            # A new card section begins. Close any prior open section that had no
            # explicit STATEMENT BALANCE (shouldn't happen, but be safe).
            if cur_prev is not None:
                close_section(None)
            cur_prev = _to_float(m_prev.group(1))
            cur_debits = 0.0
            cur_credits = 0.0
            last_txn = None
            continue

        m_stmt = STATEMENT_BALANCE_RE.search(line)
        if m_stmt:
            close_section(_to_float(m_stmt.group(1)))
            continue

        # ── Skip page chrome / repeated headers ──────────────────────────────
        if ("POSTING DATE" in upper or "TARIKH POS" in upper
                or "CONTINUED ON NEXT PAGE" in upper
                or "ON-GOING PROMOTION" in upper
                or upper.startswith("PAGE /")):
            continue

        # ── Transaction row ──────────────────────────────────────────────────
        m_txn = TXN_LINE_RE.match(line)
        if m_txn:
            txn_day = int(m_txn.group(3))
            txn_mon = MONTH_MAP[m_txn.group(4).lower()]
            desc = m_txn.group(5)
            amount_val = _to_float(m_txn.group(6))
            is_cr = bool(m_txn.group(7))

            # Instalment-plan setup line ("... : 0/NN <total remaining>") is
            # informational, not a charge — skip so the section reconciles.
            if _INSTALMENT_INFO_RE.search(desc):
                last_txn = None
                continue

            year = _resolve_year(txn_mon, stmt_year, stmt_month)
            try:
                d = date(year, txn_mon, txn_day)
            except ValueError:
                last_txn = None
                continue

            if is_cr:
                signed = amount_val
                txn_type = "credit"
                cur_credits += amount_val
            else:
                signed = -amount_val
                txn_type = "debit"
                cur_debits += amount_val

            txn = {
                "date": d.isoformat(),
                "description": _clean_desc(desc),
                "amount": round(signed, 2),
                "type": txn_type,
                "balance": "",
                "raw": line[:150],
            }
            transactions.append(txn)
            last_txn = txn
            continue

        # ── Continuation / noise lines ───────────────────────────────────────
        # Payment-detail noise, bare amount echoes, and FX/promo continuation
        # lines carry no independent amount — fold meaningful text into the
        # previous transaction's description, drop the rest.
        if last_txn is not None:
            # Footer / legal boilerplate marks the end of the page's transaction
            # flow — stop appending to the current transaction entirely.
            if any(k in upper for k in _FOOTER_MARKERS):
                last_txn = None
                continue
            if _PAYMENT_NOISE_RE.match(line) or _BARE_AMOUNT_RE.match(line):
                continue
            # Interest-rate disclosure line that trails a section (no amount)
            if "CURRENT MONTH FINANCE CHARGES RATE" in upper:
                last_txn = None
                continue
            # Skip promo blurbs (heuristic: contain typical promo markers)
            if any(k in upper for k in (
                    "T&C", "0% EASY PAY", "MIN SPEND", "MIN. SPEND", "MIN.SPEND",
                    "OFFER ENDS", "NOW TILL", "VALID TILL", "VALID FROM",
                    "CIMB DEALS", "DEALS.CIMB", "#CIMBDEALS", "% OFF",
                    "DEALS MY WEBSITE", "> SEARCH", ">SEARCH", "SAVE UP TO",
                    "GET A ", "ENJOY ", "TREAT YOURSELF", "DRESS TO IMPRESS",
                    "BE YOUR STYLE", "FLIP INTO", "YOUR CAR DESERVES",
                    "NO PURCHASE REQUIRE", "NO MINIMUM SPEND")):
                continue
            # Foreign-currency continuation, e.g. "840U.S. DOLLAR 63.48"
            # or a plain wrapped description fragment — append to description.
            frag = re.sub(r"\s+", " ", line).strip()
            if frag:
                last_txn["description"] = (last_txn["description"] + " " + frag).strip()
            continue

    # Close any dangling open section
    if cur_prev is not None:
        close_section(None)

    summary_total = round(sum(summary_balances), 2) if summary_balances else None

    return {
        "transactions": transactions,
        "summary_balance": summary_total,
        "summary_balances": summary_balances,
        "sections": sections,
    }


def validate(result: dict) -> tuple[bool, float, str]:
    """
    Reconcile each card section: previous + debits - credits == statement_balance.
    Returns (passed, worst_abs_diff, detail_message).
    """
    sections = result["sections"]
    if not sections:
        return False, 0.0, "no card sections found"

    worst = 0.0
    all_ok = True
    parts = []
    for i, s in enumerate(sections):
        if s["statement"] is None:
            all_ok = False
            parts.append(f"sec{i}: no STATEMENT BALANCE")
            continue
        expected = round(s["previous"] + s["debits"] - s["credits"], 2)
        diff = round(expected - s["statement"], 2)
        if abs(diff) > worst:
            worst = abs(diff)
        if abs(diff) > 0.01:
            all_ok = False
            parts.append(
                f"sec{i}: {s['previous']:.2f}+{s['debits']:.2f}"
                f"-{s['credits']:.2f}={expected:.2f} vs {s['statement']:.2f} (Δ{diff:+.2f})"
            )
    detail = "; ".join(parts) if parts else "all sections balanced"
    return all_ok, worst, detail


def write_csv(transactions: list[dict], csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "description", "amount", "type", "balance", "raw"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for txn in transactions:
            writer.writerow(txn)
    print(f"  Wrote {len(transactions)} transactions → {csv_path}")


def _parse_filename(pdf_path: Path) -> tuple[int, int]:
    """MM_YY_CIMB.pdf → (year, month). e.g. 03_25 → (2025, 3), 09_24 → (2024, 9)."""
    m = re.match(r"(\d{2})_(\d{2})_CIMB$", pdf_path.stem, re.IGNORECASE)
    if not m:
        raise ValueError(f"Unexpected filename format: {pdf_path.name}")
    month = int(m.group(1))
    year = 2000 + int(m.group(2))
    return year, month


def process_pdf(pdf_path: Path, csv_path: Path | None = None) -> dict:
    print(f"Parsing {pdf_path.name} ...")
    stmt_year, stmt_month = _parse_filename(pdf_path)
    result = parse_statement(pdf_path, stmt_year, stmt_month)

    if csv_path is None:
        csv_path = config.CSV_DIR / "fuiyee" / "cimbcc" / f"{stmt_year}-{stmt_month:02d}.csv"

    write_csv(result["transactions"], csv_path)

    txns = result["transactions"]
    debits = sum(1 for t in txns if t["type"] == "debit")
    credits = sum(1 for t in txns if t["type"] == "credit")
    print(f"  Charges/fees: {debits}   Payments/credits: {credits}")

    passed, worst, detail = validate(result)
    status = "PASS" if passed else "FAIL"
    print(f"  Validation: {status}  (worst Δ {worst:+.2f})  {detail}")

    result["stmt_year"] = stmt_year
    result["stmt_month"] = stmt_month
    result["passed"] = passed
    result["worst_diff"] = worst
    result["detail"] = detail
    return result


def _all_pdfs(year: int | None = None) -> list[Path]:
    base = config.BANK_STATEMENTS_DIR / "Fui Yee" / "CIMB_CC"
    pdfs = []
    for pat in ("*.PDF", "*.pdf"):
        pdfs.extend(_glob.glob(str(base / "*" / pat)))
    # Deduplicate (case-insensitive filesystems match both patterns) and sort by (year, month)
    seen = {}
    for p in pdfs:
        seen[Path(p).resolve()] = Path(p)
    result = []
    for p in seen.values():
        try:
            y, m = _parse_filename(p)
        except ValueError:
            continue
        if year and y != year:
            continue
        result.append((y, m, p))
    result.sort(key=lambda t: (t[0], t[1]))
    return [p for _, _, p in result]


def process_all(year: int | None = None):
    pdfs = _all_pdfs(year)
    if not pdfs:
        print("No CIMB CC PDFs found.")
        return

    summary = []
    for pdf in pdfs:
        res = process_pdf(pdf)
        summary.append(res)

    # ── Summary table ────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("VALIDATION SUMMARY")
    print("=" * 72)
    print(f"{'Statement':<12}{'Txns':>6}{'Result':>10}{'Worst Δ':>12}   Detail")
    print("-" * 72)
    n_pass = 0
    for r in summary:
        tag = f"{r['stmt_year']}-{r['stmt_month']:02d}"
        status = "PASS" if r["passed"] else "FAIL"
        if r["passed"]:
            n_pass += 1
        detail = "" if r["passed"] else r["detail"]
        print(f"{tag:<12}{len(r['transactions']):>6}{status:>10}{r['worst_diff']:>12.2f}   {detail}")
    total_txns = sum(len(r["transactions"]) for r in summary)
    print("-" * 72)
    print(f"{len(summary)} statements, {total_txns} transactions, "
          f"{n_pass} PASS / {len(summary) - n_pass} FAIL")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse Fui Yee's CIMB Credit Card PDF statements to CSV"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
    group.add_argument("--all", action="store_true", help="Process all CIMB CC PDFs on file")
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
