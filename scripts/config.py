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

# Ledger account names
ACCOUNTS = {
    "ambank":  "Assets:Bank:AmBank",
    "maybank": "Assets:Bank:Maybank",
    "hsbc":    "Liabilities:CreditCard:HSBC",
    "ambcc":   "Liabilities:CreditCard:AmBankCC",
}

# Statement source paths
SAMUEL_AMBANK_DIR  = BANK_STATEMENTS_DIR / "Samuel" / "Ambank"
SAMUEL_MAYBANK_DIR = BANK_STATEMENTS_DIR / "Samuel" / "Maybank"
SAMUEL_HSBC_DIR    = BANK_STATEMENTS_DIR / "Samuel" / "HSBC Ccard"
SAMUEL_AMBCC_DIR   = BANK_STATEMENTS_DIR / "Samuel" / "Ambank Ccard"

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
