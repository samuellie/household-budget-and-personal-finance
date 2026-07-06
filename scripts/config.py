"""
Shared configuration for the household finance pipeline.
All scripts import from here — single source of truth for paths and constants.
"""

from pathlib import Path
import calendar

# Project root — one level up from this file (scripts/ -> project root)
BASE_DIR = Path(__file__).parent.parent

# Main directories
BANK_STATEMENTS_DIR = BASE_DIR / "Bank Statements"
CSV_DIR             = BASE_DIR / "csv"
LEDGER_DIR          = BASE_DIR / "ledger"
REPORTS_DIR         = BASE_DIR / "reports"

# Ledger files
MAIN_JOURNAL     = LEDGER_DIR / "main.journal"
ACCOUNTS_JOURNAL = LEDGER_DIR / "accounts.journal"
CATEGORIES_RULES = LEDGER_DIR / "categories.rules"

# Currency
CURRENCY = "MYR"

# Ledger account names (person-first structure: <Root>:<Person>:<...>)
# Samuel's accounts and Fui Yee's accounts are namespaced by owner so each
# person can be reviewed independently (bal Expenses:Samuel / Expenses:FuiYee)
# and combined (bal Expenses).
ACCOUNTS = {
    # Samuel
    "ambank":  "Assets:Samuel:Bank:AmBank",
    "maybank": "Assets:Samuel:Bank:Maybank",
    "hsbc":    "Liabilities:Samuel:CreditCard:HSBC",
    "ambcc":   "Liabilities:Samuel:CreditCard:AmBankCC",
    # Fui Yee
    "fy_cimb":   "Assets:FuiYee:Bank:CIMB",
    "fy_cimbcc": "Liabilities:FuiYee:CreditCard:CIMBCC",
    "fy_hsbc":   "Liabilities:FuiYee:CreditCard:HSBC",
    "fy_rhb":    "Liabilities:FuiYee:CreditCard:RHB",
    "fy_uob":    "Liabilities:FuiYee:CreditCard:UOB",
}

# Which person owns each bank — used to inject the person segment into
# categorised Income/Expense/Asset/Liability accounts. Equity stays shared.
OWNER = {
    "ambank": "Samuel", "maybank": "Samuel", "hsbc": "Samuel", "ambcc": "Samuel",
    "fy_cimb": "FuiYee", "fy_cimbcc": "FuiYee", "fy_hsbc": "FuiYee",
    "fy_rhb": "FuiYee", "fy_uob": "FuiYee",
}

# Bank "kind" — determines double-entry direction in csv_to_ledger.
#   "bank" = asset account (debit = money out); "card" = liability (debit = purchase)
BANK_KIND = {
    "ambank": "bank", "maybank": "bank", "fy_cimb": "bank",
    "hsbc": "card", "ambcc": "card",
    "fy_cimbcc": "card", "fy_hsbc": "card", "fy_rhb": "card", "fy_uob": "card",
}

# Statement source paths
SAMUEL_AMBANK_DIR  = BANK_STATEMENTS_DIR / "Samuel" / "Ambank"
SAMUEL_MAYBANK_DIR = BANK_STATEMENTS_DIR / "Samuel" / "Maybank"
SAMUEL_HSBC_DIR    = BANK_STATEMENTS_DIR / "Samuel" / "HSBC Ccard"
SAMUEL_AMBCC_DIR   = BANK_STATEMENTS_DIR / "Samuel" / "Ambank Ccard"

# Fui Yee statement source paths
FUIYEE_DIR         = BANK_STATEMENTS_DIR / "Fui Yee"
FY_CIMB_DIR        = FUIYEE_DIR / "CIMB"
FY_CIMBCC_DIR      = FUIYEE_DIR / "CIMB_CC"
FY_HSBC_DIR        = FUIYEE_DIR / "HSBC"
FY_RHB_DIR         = FUIYEE_DIR / "RHB"
FY_UOB_DIR         = FUIYEE_DIR / "UOB"

# Target card extracted from the consolidated AmBank CC statement
# CARz Card Gold VISA (S) — Samuel's supplementary card
AMBCC_TARGET_CARD = "4293130700714622"  # 16 digits, no spaces

# Maybank masked account number (used in filenames)
MAYBANK_ACCOUNT = "158088-403774"

# ── Path helpers ──────────────────────────────────────────────────────────────

def ambank_pdf_path(year: int, month: int) -> Path:
    return SAMUEL_AMBANK_DIR / str(year) / f"AMB-PDF-{year}-{month:02d}.pdf"

def ambank_csv_path(year: int, month: int) -> Path:
    return CSV_DIR / "ambank" / f"{year}-{month:02d}.csv"

def ambank_journal_path(year: int, month: int) -> Path:
    return LEDGER_DIR / "ambank" / f"{year}-{month:02d}.journal"


def maybank_pdf_path(year: int, month: int) -> Path:
    last_day = calendar.monthrange(year, month)[1]
    return SAMUEL_MAYBANK_DIR / str(year) / f"{MAYBANK_ACCOUNT}_{year}{month:02d}{last_day:02d}.pdf"

def maybank_csv_path(year: int, month: int) -> Path:
    return CSV_DIR / "maybank" / f"{year}-{month:02d}.csv"

def maybank_journal_path(year: int, month: int) -> Path:
    return LEDGER_DIR / "maybank" / f"{year}-{month:02d}.journal"


def hsbc_pdf_path(year: int, month: int) -> Path:
    return SAMUEL_HSBC_DIR / f"HSBC-PDF-{year}-{month:02d}.pdf"

def hsbc_csv_path(year: int, month: int) -> Path:
    return CSV_DIR / "hsbc" / f"{year}-{month:02d}.csv"

def hsbc_journal_path(year: int, month: int) -> Path:
    return LEDGER_DIR / "hsbc" / f"{year}-{month:02d}.journal"


def ambcc_pdf_path(year: int, month: int) -> Path:
    return SAMUEL_AMBCC_DIR / f"AMBCC-PDF-{year}-{month:02d}.pdf"

def ambcc_csv_path(year: int, month: int) -> Path:
    return CSV_DIR / "ambcc" / f"{year}-{month:02d}.csv"

def ambcc_journal_path(year: int, month: int) -> Path:
    return LEDGER_DIR / "ambcc" / f"{year}-{month:02d}.journal"


# ── Fui Yee generic helpers ─────────────────────────────────────────────────
# Fui Yee CSVs and journals live under a fuiyee/<bank> subfolder.
# `bank` is one of: cimb, cimbcc, hsbc, rhb, uob  (without the fy_ prefix).

def fy_csv_path(bank: str, year: int, month: int) -> Path:
    return CSV_DIR / "fuiyee" / bank / f"{year}-{month:02d}.csv"

def fy_journal_path(bank: str, year: int, month: int) -> Path:
    return LEDGER_DIR / "fuiyee" / bank / f"{year}-{month:02d}.journal"
