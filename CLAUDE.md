# Household Budget & Personal Finance

## Project Purpose

Personal finance project for Samuel and Fui Yee's household. Currency: **MYR**.

Goals: track income/expenses across all accounts, analyse spending, support budgeting decisions.

---

## Project Structure

```
Household budget and Personal Finance/
├── CLAUDE.md                              ← this file
├── Household budget and tracking.gsheet  ← Google Sheets budget tracker
├── Bank Statements/                       ← source PDFs (do not rename)
│   └── Samuel/
│       ├── Ambank/{Year}/AMB-PDF-YYYY-MM.pdf
│       ├── HSBC Ccard/HSBC-PDF-YYYY-MM.pdf
│       └── Maybank/{Year}/158088-403774_YYYYMMDD.pdf
│
├── scripts/                               ← Python + shell pipeline scripts
│   ├── config.py                          ← shared paths and account names
│   ├── parse_ambank.py                    ← AmBank PDF → CSV
│   ├── parse_maybank.py                   ← Maybank PDF → CSV
│   ├── parse_hsbc.py                      ← HSBC PDF → CSV (OCR — needs tesseract)
│   ├── categorize.py                      ← pattern-based category engine
│   ├── csv_to_ledger.py                   ← CSV → ledger journal entries
│   ├── generate_reports.py               ← ledger queries → Excel reports
│   ├── run_all.sh                         ← full pipeline orchestration
│   └── requirements.txt                  ← Python dependencies
│
├── ledger/                                ← accounting database
│   ├── main.journal                       ← master file (includes all sub-journals)
│   ├── accounts.journal                   ← chart of accounts
│   ├── commodities.journal               ← MYR currency definition
│   ├── categories.rules                  ← JSON rules: description → account mapping
│   ├── ambank/YYYY-MM.journal
│   ├── maybank/YYYY-MM.journal
│   └── hsbc/YYYY-MM.journal
│
├── csv/                                   ← intermediate parsed CSVs (for debugging)
│   ├── ambank/, maybank/, hsbc/
│
└── reports/                               ← generated Excel reports
    ├── monthly_YYYY-MM.xlsx
    └── annual_YYYY-YYYY.xlsx
```

---

## Git Workflow

This project is tracked at `git@github.com:samuellie/household-budget-and-personal-finance.git`.

**After every pipeline run or manual journal edit, commit and push the changes:**

```bash
git add ledger/ csv/ scripts/ ledger/categories.rules CLAUDE.md README.md
git commit -m "Add YYYY-MM statements — BankName"
git push
```

**What is committed:** `ledger/` journals, `csv/` intermediates, `scripts/`, config files, docs.

**What is NOT committed:** `Bank Statements/` PDFs (store locally), `reports/` Excel files (generated), `.venv/`.

> Claude Code instruction: whenever journal files are created or modified as part of a task, always create a git commit for those changes and push to origin before reporting the task complete.

---

## Mortgage

| Field | Value |
|-------|-------|
| Amount | **MYR 3,551.00 / month** |
| Payee | Wong Fui Yee (CIMB bank account) |
| Ledger account | `Expenses:Housing:Mortgage` |
| Started | July 2025 |

**Auto-detection rules (both must match for amount-based rule):**
1. **Text match** (categories.rules, priority 10): description contains "WONG FUI YEE" + "HOUSE", "LOAN", or "MORTGAGE"
2. **Amount match** (csv_to_ledger.py): payee = WONG FUI YEE + debit = exactly MYR 3,551.00

**Known edge cases:**
- July 2025 first payment from AmBank was MYR 3,387.73 (possibly prorated) — not auto-detected; reclassify manually if needed
- Lump-sum payments (e.g. MYR 12,102 = 3 months + aircond) require manual split

---

## Samuel's Accounts

| Account | Type | Ledger name | Coverage |
|---------|------|-------------|----------|
| AmBank 8881020737788 | Savings | `Assets:Bank:AmBank` | Apr 2025 – May 2026 |
| Maybank 158088-403774 | Savings-i | `Assets:Bank:Maybank` | Mar 2025 – May 2026 |
| HSBC Credit Card | Credit card | `Liabilities:CreditCard:HSBC` | Sep 2025 – Jun 2026 |
| AmBank CC (CARz Gold VISA) | Credit card | `Liabilities:CreditCard:AmBankCC` | Oct 2025 – Jun 2026 |

Fui Yee: no statements on file yet. Add under `Bank Statements/Fui Yee/` when available.

---

## How to Run the Pipeline

### Full pipeline (parse → convert → validate → report)
```bash
./scripts/run_all.sh
```

### Individual stages
```bash
# 1. Parse new PDFs to CSV
python3 scripts/parse_ambank.py --all
python3 scripts/parse_maybank.py --all
python3 scripts/parse_hsbc.py --all   # requires: brew install tesseract poppler

# 2. Convert CSVs to ledger journals + activate includes
python3 scripts/csv_to_ledger.py --bank ambank --all
python3 scripts/csv_to_ledger.py --bank maybank --all
python3 scripts/csv_to_ledger.py --bank ambank --update-main   # activates all includes

# 3. Generate reports
python3 scripts/generate_reports.py --month 2026-03
python3 scripts/generate_reports.py --annual
python3 scripts/generate_reports.py --all
```

### Process a single new statement
```bash
python3 scripts/parse_maybank.py "Bank Statements/Samuel/Maybank/2026/158088-403774_20260430.pdf"
python3 scripts/csv_to_ledger.py --bank maybank --input csv/maybank/2026-04.csv
# Then manually add:  include maybank/2026-04.journal  in ledger/main.journal
python3 scripts/generate_reports.py --month 2026-04
```

---

## Common Ledger Queries (for ad-hoc analysis)

```bash
# Balance overview
ledger -f ledger/main.journal bal Assets Liabilities --depth 3

# Expenses by category (all time)
ledger -f ledger/main.journal bal Expenses --depth 2

# Monthly cash flow
ledger -f ledger/main.journal bal Income Expenses --monthly --collapse

# Spending in a specific month
ledger -f ledger/main.journal bal Expenses --period "2026-03" --flat

# All uncategorised transactions (needs review)
ledger -f ledger/main.journal reg Expenses:Uncategorized

# Register for a specific category
ledger -f ledger/main.journal reg Expenses:Subscriptions

# Food spending trend by month
ledger -f ledger/main.journal bal Expenses:Food --monthly
```

---

## Adding New Accounts

When a new ledger account is introduced (e.g. a new income type or expense category), **two files must be updated**:

1. **`ledger/accounts.journal`** — declare the account
2. **`scripts/generate_reports.py` → `PNL_STRUCTURE`** (around line 64) — add it to the correct income or expense line item

If step 2 is skipped, the account will be silently excluded from the annual P&L report totals even though the raw ledger balance is correct.

---

## Categorisation

Rules live in `ledger/categories.rules` (JSON). Edit this file to:
- Add new merchants/patterns
- Reclassify existing patterns
- Change category for a specific transaction type

After editing rules, regenerate journals:
```bash
python3 scripts/csv_to_ledger.py --bank maybank --all
python3 scripts/csv_to_ledger.py --bank ambank --all
```

Test a single description:
```bash
python3 scripts/categorize.py "PETRONAS"
python3 scripts/categorize.py "TINKERVE TECHNOLOGY" --credit
```

---

## HSBC Credit Card (OCR)

HSBC PDFs are image-only. OCR setup (one-time):
```bash
brew install tesseract poppler
pip3 install --break-system-packages pytesseract pdf2image Pillow
```

Then:
```bash
python3 scripts/parse_hsbc.py --all
```

OCR output creates a `_review.txt` file alongside each CSV — check it for low-confidence extractions before converting to ledger.

---

## File Naming Conventions (source PDFs)

| Bank | Pattern | Date |
|------|---------|------|
| AmBank | `AMB-PDF-YYYY-MM.pdf` | Statement month |
| HSBC | `HSBC-PDF-YYYY-MM.pdf` | Statement month |
| Maybank | `AcctNo_YYYYMMDD.pdf` | Last day of month |

Keep filenames exactly as downloaded from the bank portals.

---

## Installing Dependencies

```bash
pip3 install --break-system-packages pdfplumber openpyxl xlsxwriter
# For HSBC OCR:
brew install tesseract poppler
pip3 install --break-system-packages pytesseract pdf2image Pillow
```
