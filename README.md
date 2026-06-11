# Household Finance — Script Reference

Personal finance system for Samuel & Fui Yee. Built on [ledger-cli](https://ledger-cli.org/).

**Pipeline:** Bank statement PDFs → CSV → Ledger journals → Excel reports

---

## Prerequisites

### Required tools

| Tool | Install | Purpose |
|------|---------|---------|
| [ledger-cli](https://ledger-cli.org/) | `brew install ledger` | Double-entry accounting engine |
| Python 3.10+ | `brew install python` | Pipeline scripts |
| pdfplumber, openpyxl, xlsxwriter | `pip3 install --break-system-packages pdfplumber openpyxl xlsxwriter` | PDF parsing + Excel output |
| tesseract + poppler | `brew install tesseract poppler` | OCR for HSBC image-only PDFs |
| pytesseract, pdf2image, Pillow | `pip3 install --break-system-packages pytesseract pdf2image Pillow` | Python OCR bindings (HSBC only) |

### One-time setup
```bash
# Install Homebrew packages
brew install ledger tesseract poppler

# Install Python packages
pip3 install --break-system-packages pdfplumber openpyxl xlsxwriter pytesseract pdf2image Pillow

# Or use the requirements file
pip3 install --break-system-packages -r scripts/requirements.txt
```

Verify ledger is working:
```bash
ledger --version
```

---

## Git Workflow

Ledger journals and category rules are version-controlled. **After any pipeline run that changes journal files, commit the changes:**

```bash
# Stage all ledger and script changes
git add ledger/ scripts/ csv/ ledger/categories.rules CLAUDE.md

# Commit with a descriptive message
git commit -m "Add YYYY-MM statements — Bank Name"

# Push to remote
git push
```

**What is tracked (committed):**
- `ledger/` — all journal files (source of truth for account history)
- `csv/` — intermediate parsed CSVs (useful for debugging OCR / parser issues)
- `scripts/` — pipeline scripts and category rules
- `CLAUDE.md`, `README.md` — project documentation

**What is NOT tracked:**
- `Bank Statements/` — source PDFs (store locally; too large and sensitive for git)
- `reports/` — generated Excel files (regenerate anytime with `generate_reports.py`)

---

## Quick Start

### Full pipeline (parse everything and generate all reports)
```bash
./scripts/run_all.sh
```

### Process a single new statement
```bash
# 1. Parse the new PDF to CSV
python3 scripts/parse_maybank.py "Bank Statements/Samuel/Maybank/2026/158088-403774_20260430.pdf"

# 2. Convert CSV to ledger journal
python3 scripts/csv_to_ledger.py --bank maybank --input csv/maybank/2026-04.csv

# 3. Add the include line in ledger/main.journal:
#    include maybank/2026-04.journal

# 4. Generate the monthly report
python3 scripts/generate_reports.py --month 2026-04
```

---

## Scripts

### `parse_ambank.py` — AmBank PDF → CSV
Extracts transactions from AmBank account statement PDFs.

```bash
# Single file
python3 scripts/parse_ambank.py "Bank Statements/Samuel/Ambank/2025/AMB-PDF-2025-04.pdf"

# All AmBank statements on file
python3 scripts/parse_ambank.py --all

# One year only
python3 scripts/parse_ambank.py --year 2026
```

Output: `csv/ambank/YYYY-MM.csv`

---

### `parse_maybank.py` — Maybank PDF → CSV
Extracts transactions from Maybank Islamic savings account PDFs.

```bash
# Single file
python3 scripts/parse_maybank.py "Bank Statements/Samuel/Maybank/2025/158088-403774_20250430.pdf"

# All Maybank statements on file
python3 scripts/parse_maybank.py --all

# One year only
python3 scripts/parse_maybank.py --year 2026
```

Output: `csv/maybank/YYYY-MM.csv`

---

### `parse_hsbc.py` — HSBC Credit Card PDF → CSV (OCR)
HSBC statements are image-only PDFs. This script converts pages to images and runs OCR via Tesseract.

**Prerequisites (one-time setup):**
```bash
brew install tesseract poppler
pip3 install --break-system-packages pytesseract pdf2image Pillow
```

```bash
# Single file
python3 scripts/parse_hsbc.py "Bank Statements/Samuel/HSBC Ccard/HSBC-PDF-2026-04.pdf"

# All HSBC statements on file
python3 scripts/parse_hsbc.py --all

# One year only
python3 scripts/parse_hsbc.py --year 2026
```

Output: `csv/hsbc/YYYY-MM.csv`

A `_review.txt` file is created alongside each CSV if any transactions had low OCR confidence — check these manually before converting to ledger.

---

### `csv_to_ledger.py` — CSV → Ledger Journal
Reads a parsed CSV and produces a double-entry ledger journal file. Categories are applied automatically using `ledger/categories.rules`.

```bash
# Single file (bank + input required)
python3 scripts/csv_to_ledger.py --bank maybank --input csv/maybank/2026-04.csv

# All CSVs for one bank
python3 scripts/csv_to_ledger.py --bank ambank --all
python3 scripts/csv_to_ledger.py --bank maybank --all
python3 scripts/csv_to_ledger.py --bank hsbc --all

# Specify output path (single-file mode)
python3 scripts/csv_to_ledger.py --bank hsbc --input csv/hsbc/2026-04.csv --output ledger/hsbc/2026-04.journal

# Auto-activate include lines in main.journal for all journals that exist
python3 scripts/csv_to_ledger.py --bank ambank --update-main
```

Output: `ledger/{bank}/YYYY-MM.journal`

> After generating a new journal, either add `include {bank}/YYYY-MM.journal` to `ledger/main.journal` manually, or run `--update-main` to activate all existing journals at once.

---

### `categorize.py` — Category Engine (test / standalone)
Tests how a transaction description will be categorised, without running the full pipeline.

```bash
# Test a description (defaults to debit/expense)
python3 scripts/categorize.py "PETRONAS"
python3 scripts/categorize.py "GRAB FOOD"
python3 scripts/categorize.py "GOLDEN SCREEN CINEMAS"

# Test as a credit (income)
python3 scripts/categorize.py "TINKERVE TECHNOLOGY" --credit
```

Example output:
```
Account : Expenses:Transport:Fuel
Status  : MATCHED
```

To add or change category rules, edit `ledger/categories.rules` — it's a JSON file with `pattern` (regex), `account`, and `priority` fields. Higher priority wins when multiple rules match.

---

### `generate_reports.py` — Excel Report Generator
Queries ledger and produces formatted `.xlsx` reports in `reports/`.

```bash
# One month
python3 scripts/generate_reports.py --month 2026-03

# Multiple months
python3 scripts/generate_reports.py --month 2026-01 --month 2026-02 --month 2026-03

# Annual summary (trends + category totals + account balances)
python3 scripts/generate_reports.py --annual

# Everything (annual + all monthly)
python3 scripts/generate_reports.py --all
```

**Monthly report** (`reports/monthly_YYYY-MM.xlsx`) contains:
- **Summary** — total income, expenses, net savings, savings rate, account balances
- **Category Breakdown** — each expense category with amount and % of spending
- **Transactions** — full transaction list for the month

**Annual report** (`reports/annual_2025-2026.xlsx`) contains:
- **Monthly Trends** — income, expenses, net savings, savings rate by month
- **Category Annual** — each category's total by month (pivot table style)
- **Account Balances** — month-end balances for each account

---

### `run_all.sh` — Pipeline Orchestrator
Runs all stages in sequence. Supports selective execution.

```bash
# Full pipeline: parse → convert → validate → report
./scripts/run_all.sh

# Parse PDFs to CSV only
./scripts/run_all.sh parse

# Convert CSVs to ledger journals only
./scripts/run_all.sh convert

# Validate the ledger only
./scripts/run_all.sh validate

# Generate reports only
./scripts/run_all.sh report

# Generate one specific monthly report
./scripts/run_all.sh report --month 2026-04

# Generate annual report only
./scripts/run_all.sh report --annual
```

---

## Common Ledger Queries

Run these directly for ad-hoc analysis. All queries run against `ledger/main.journal`.

```bash
# Account balances
ledger -f ledger/main.journal bal Assets Liabilities --depth 3

# Expenses by category (all time)
ledger -f ledger/main.journal bal Expenses --depth 2

# Expenses in a specific month
ledger -f ledger/main.journal bal Expenses --period "2026-03" --depth 2

# Monthly cash flow (income vs expenses over time)
ledger -f ledger/main.journal bal Income Expenses --monthly --collapse

# All transactions in a month
ledger -f ledger/main.journal reg --period "2026-03"

# Spending in a category
ledger -f ledger/main.journal reg Expenses:Food

# Subscriptions breakdown
ledger -f ledger/main.journal bal Expenses:Subscriptions --depth 3

# All uncategorised transactions (review and add rules)
ledger -f ledger/main.journal reg Expenses:Uncategorized

# Year-to-date savings
ledger -f ledger/main.journal bal Income Expenses --period "from 2026-01-01"
```

---

## Improving Categories

When you see transactions in `Expenses:Uncategorized`, add rules to `ledger/categories.rules`:

1. Find uncategorised transactions:
   ```bash
   ledger -f ledger/main.journal reg Expenses:Uncategorized
   ```

2. Open `ledger/categories.rules` and add a rule, e.g.:
   ```json
   {"pattern": "MY NEW MERCHANT", "account": "Expenses:Food:DiningOut", "priority": 8}
   ```

3. Regenerate the affected bank's journals:
   ```bash
   python3 scripts/csv_to_ledger.py --bank hsbc --all
   ```

4. Optionally regenerate reports:
   ```bash
   python3 scripts/generate_reports.py --all
   ```

No need to re-parse PDFs — rules are applied at the CSV-to-journal step.

---

## Adding New Statements

When a new month's statement is downloaded:

1. Save the PDF following the existing naming convention (see `CLAUDE.md`)
2. Run the parser for that bank (e.g. `python3 scripts/parse_maybank.py --all`)
3. Convert to ledger: `python3 scripts/csv_to_ledger.py --bank maybank --all`
4. Activate the include: `python3 scripts/csv_to_ledger.py --bank maybank --update-main`
5. Generate the new monthly report: `python3 scripts/generate_reports.py --month YYYY-MM`

Or just run `./scripts/run_all.sh` to redo everything.

---

## File Locations

| What | Where |
|------|-------|
| Source PDFs | `Bank Statements/Samuel/{Bank}/` |
| Parsed CSVs (intermediate) | `csv/{ambank,maybank,hsbc}/` |
| Ledger journals | `ledger/{ambank,maybank,hsbc}/` |
| Category rules | `ledger/categories.rules` |
| Chart of accounts | `ledger/accounts.journal` |
| Master journal | `ledger/main.journal` |
| Excel reports | `reports/` |
| Script config | `scripts/config.py` |

---

## Troubleshooting

**`ledger: No such file or directory`**
Make sure you're running commands from the project root folder (where `CLAUDE.md` is).

**New month journal not showing in ledger queries**
The include line in `ledger/main.journal` may still be commented out. Run:
```bash
python3 scripts/csv_to_ledger.py --bank maybank --update-main
```

**HSBC OCR misses transactions or gets wrong amounts**
OCR accuracy varies. Check the `_review.txt` file next to the CSV. For persistent errors, edit the CSV directly before converting to ledger.

**`No module named 'pdfplumber'`**
```bash
pip3 install --break-system-packages pdfplumber openpyxl xlsxwriter
```

**`No module named 'pytesseract'`** (HSBC only)
```bash
brew install tesseract poppler
pip3 install --break-system-packages pytesseract pdf2image Pillow
```
