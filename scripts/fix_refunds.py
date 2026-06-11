#!/usr/bin/env python3
"""
fix_refunds.py — Reclassify Income:Refund postings to negate the matching expense account.

For each Income:Refund posting found in ledger/ journal files:
  - Matches it to the original expense transaction by merchant name
  - Rewrites the posting as a negative against that expense account
  - Flags unmatched entries for manual review (no auto-fix)
  - Skips government/tax refunds (legitimately income)

Creates .bak backups before modifying any files.

Usage:
    python3 scripts/fix_refunds.py --dry-run    # preview without modifying
    python3 scripts/fix_refunds.py              # apply (creates .bak backups)
"""

import argparse
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
LEDGER_DIR = ROOT / "ledger"

_DATE_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+[!*]?\s*(.*)")
_POSTING_RE = re.compile(r"^(\s+)(\S[^;]*?)\s{2,}(MYR\s+-?[\d,]+\.\d{2})(.*)")
_RAW_MERCHANT_RE = re.compile(r";\s*raw:[^|]*\|\s*([^|]+?)\s*\|")
_INCOME_REFUND_RE = re.compile(r"^Income:Refund$", re.IGNORECASE)
_TAX_SKIP_RE = re.compile(r"TAX[_\s]REFUND|KERAJAAN|LHDN", re.IGNORECASE)
_REFUND_PREFIX_RE = re.compile(
    r"^(?:PRE[-\s]AUTH\s+REFUND|REFUND\s+SALE|SALE\s+REFUND|REVERSAL|REFUND)\s+",
    re.IGNORECASE,
)
_EXPENSE_PREFIX_RE = re.compile(
    r"^(?:SALE\s+DEBIT|PRE[-\s]AUTH\s+DEBIT|PAYMENT\s+VIA\s+\w+|PURCHASE)\s+",
    re.IGNORECASE,
)
_SKIP_FILES = {"main.journal", "accounts.journal", "commodities.journal"}


@dataclass
class Posting:
    account: str
    amount_str: str  # e.g. "MYR 19,495.20" or "MYR -19,495.20"
    tail: str        # trailing text on same line (e.g. "  ; TODO: categorise")
    line_idx: int    # 0-based index into the file's lines list

    @property
    def amount(self) -> float:
        return float(self.amount_str.replace("MYR", "").replace(",", "").strip())


@dataclass
class Transaction:
    date: str
    payee: str
    postings: list
    raw_merchant: Optional[str]   # extracted from "; raw: ... | MERCHANT |" comment
    source_file: Path

    @property
    def expense_posting(self) -> Optional[Posting]:
        for p in self.postings:
            if p.account.startswith("Expenses:"):
                return p
        return None

    @property
    def income_refund_posting(self) -> Optional[Posting]:
        for p in self.postings:
            if _INCOME_REFUND_RE.match(p.account):
                return p
        return None

    @property
    def has_income_refund(self) -> bool:
        return self.income_refund_posting is not None


def _merchant_key(raw_merchant: Optional[str], payee: str) -> str:
    """Normalised merchant string for fuzzy matching."""
    if raw_merchant:
        k = raw_merchant.upper()
        # Strip trailing 2-char country codes (e.g. "SG", "US", "GB", "MY")
        k = re.sub(r"\s+[A-Z]{2}$", "", k).strip()
        return k
    # Fall back: strip transaction-type prefix from payee
    s = _REFUND_PREFIX_RE.sub("", payee)
    s = _EXPENSE_PREFIX_RE.sub("", s)
    return s.upper().strip()[:40]


def _merchant_overlap(key_a: str, key_b: str) -> bool:
    """True if the two merchant keys share at least one token of 4+ chars."""
    tokens_a = {t for t in key_a.split() if len(t) >= 4}
    tokens_b = {t for t in key_b.split() if len(t) >= 4}
    if tokens_a & tokens_b:
        return True
    # Also accept prefix overlap to handle truncated descriptions
    min_len = min(len(key_a), len(key_b), 8)
    return min_len >= 4 and key_a[:min_len] == key_b[:min_len]


def _days_apart(d1: str, d2: str) -> int:
    return abs((datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d2, "%Y-%m-%d")).days)


def parse_journal(path: Path) -> tuple:
    """Parse a journal file → (list[Transaction], list[str] lines)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    transactions = []
    i = 0
    while i < len(lines):
        m = _DATE_LINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        txn_date, txn_payee = m.group(1), m.group(2).strip()
        postings = []
        raw_merchant = None
        i += 1
        while i < len(lines) and lines[i].strip():
            pm = _POSTING_RE.match(lines[i])
            if pm:
                postings.append(Posting(
                    account=pm.group(2).strip(),
                    amount_str=pm.group(3).strip(),
                    tail=pm.group(4),
                    line_idx=i,
                ))
            rm = _RAW_MERCHANT_RE.search(lines[i])
            if rm and raw_merchant is None:
                raw_merchant = rm.group(1).strip()
            i += 1
        transactions.append(Transaction(
            date=txn_date,
            payee=txn_payee,
            postings=postings,
            raw_merchant=raw_merchant,
            source_file=path,
        ))
    return transactions, lines


def find_match(refund: Transaction, all_txns: list) -> Optional[Transaction]:
    """Find the best matching expense transaction for a refund."""
    ir = refund.income_refund_posting
    if ir is None:
        return None

    refund_amount = abs(ir.amount)
    refund_key = _merchant_key(refund.raw_merchant, refund.payee)
    # REFUND SALE = actual merchant refund → require same amount
    is_sale_refund = bool(re.match(r"REFUND\s+SALE|SALE\s+REFUND", refund.payee, re.IGNORECASE))

    candidates = []
    for txn in all_txns:
        if txn is refund:
            continue
        ep = txn.expense_posting
        if ep is None:
            continue
        txn_key = _merchant_key(txn.raw_merchant, txn.payee)
        if not _merchant_overlap(refund_key, txn_key):
            continue

        days = _days_apart(refund.date, txn.date)
        exact = abs(refund_amount - abs(ep.amount)) < 0.02

        if is_sale_refund and not exact:
            continue  # sale refunds require same amount

        candidates.append((not exact, days, txn))

    if not candidates:
        return None

    # Prefer: exact amount match first, then closest date
    candidates.sort(key=lambda x: (x[0], x[1]))
    no_exact, best_days, best = candidates[0]

    # Reject non-exact matches that are more than 90 days apart
    if no_exact and best_days > 90:
        return None

    return best


def run(ledger_dir: Path, dry_run: bool) -> None:
    mode = "DRY RUN — no files modified" if dry_run else "LIVE — files will be modified"
    print(f"fix_refunds.py  [{mode}]")
    print(f"Scanning: {ledger_dir}\n")

    all_txns: list[Transaction] = []
    file_lines: dict[Path, list] = {}

    for path in sorted(ledger_dir.rglob("*.journal")):
        if path.name in _SKIP_FILES:
            continue
        txns, lines = parse_journal(path)
        all_txns.extend(txns)
        file_lines[path] = lines

    print(f"Loaded {len(all_txns)} transactions from {len(file_lines)} files\n")

    fixed = []
    flagged = []
    skipped = []
    dirty_files: set[Path] = set()

    for txn in all_txns:
        if not txn.has_income_refund:
            continue
        ir = txn.income_refund_posting
        abs_amount = abs(ir.amount)
        label = f"{txn.source_file.parent.name}/{txn.source_file.name}  {txn.date}  {txn.payee[:55]}"

        if _TAX_SKIP_RE.search(txn.payee):
            skipped.append(f"  {label}  MYR {abs_amount:,.2f}")
            continue

        match = find_match(txn, all_txns)
        if match is None:
            flagged.append(f"  {label}  MYR {abs_amount:,.2f}\n    → no matching expense found")
            continue

        exp_account = match.expense_posting.account
        new_line = f"    {exp_account:<45}  MYR -{abs_amount:,.2f}\n"
        old_line = file_lines[txn.source_file][ir.line_idx].rstrip()

        if not dry_run:
            file_lines[txn.source_file][ir.line_idx] = new_line
            dirty_files.add(txn.source_file)

        fixed.append(
            f"  {label}\n"
            f"    OLD: {old_line}\n"
            f"    NEW: {new_line.rstrip()}\n"
            f"    matched: {match.source_file.parent.name}/{match.source_file.name}"
            f"  {match.date}  {match.payee[:45]}"
        )

    # Write modified files (with .bak backups)
    if not dry_run:
        for path in sorted(dirty_files):
            bak = path.with_suffix(".journal.bak")
            shutil.copy2(path, bak)
            path.write_text("".join(file_lines[path]), encoding="utf-8")
            print(f"Updated: {path.relative_to(ledger_dir.parent)}  (backup: {bak.name})")
        if dirty_files:
            print()

    print("=" * 65)
    if fixed:
        print(f"FIXED ({len(fixed)}):")
        for entry in fixed:
            print(entry)
    if flagged:
        print(f"\nFLAGGED — manual review needed ({len(flagged)}):")
        for entry in flagged:
            print(entry)
    if skipped:
        print(f"\nSKIPPED — tax/government refunds left as Income:Refund ({len(skipped)}):")
        for entry in skipped:
            print(entry)

    print(f"\nSummary: {len(fixed)} fixed, {len(flagged)} flagged, {len(skipped)} skipped")
    if dry_run:
        print("\nRun without --dry-run to apply changes.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without modifying any files (recommended first pass)",
    )
    parser.add_argument(
        "--ledger-dir", type=Path, default=LEDGER_DIR,
        help=f"Path to ledger directory (default: {LEDGER_DIR})",
    )
    args = parser.parse_args()
    run(args.ledger_dir, args.dry_run)


if __name__ == "__main__":
    main()
