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
│   ├── Samuel/
│   │   ├── Ambank/{Year}/AMB-PDF-YYYY-MM.pdf
│   │   ├── HSBC Ccard/HSBC-PDF-YYYY-MM.pdf
│   │   ├── Maybank/{Year}/158088-403774_YYYYMMDD.pdf
│   │   └── Ambank Ccard/AMBCC-PDF-YYYY-MM.pdf
│   └── Fui Yee/
│       ├── CIMB/{Year}/{Mon YY}.pdf        (savings)
│       ├── CIMB_CC/{Year}/MM_YY_CIMB.pdf   (credit card)
│       ├── HSBC/{Year}/YYYY-MM-12_Statement.pdf  (OCR)
│       ├── RHB/{Year}/RHB_MonYY.pdf        (credit card, multi-card)
│       └── UOB/{Year}/{Mon YY}.pdf         (credit card)
│
├── scripts/                               ← Python + shell pipeline scripts
│   ├── config.py                          ← shared paths, ACCOUNTS, OWNER, BANK_KIND
│   ├── parse_ambank.py / parse_maybank.py / parse_hsbc.py / parse_ambcc.py   (Samuel)
│   ├── parse_fy_cimb.py / parse_fy_cimbcc.py / parse_fy_hsbc.py              (Fui Yee)
│   ├── parse_fy_rhb.py / parse_fy_uob.py                                     (Fui Yee)
│   ├── dedup_fy_cimb.py                    ← de-dup CIMB overlapping monthly+quarterly statements
│   ├── categorize.py                      ← pattern-based category engine (person-neutral)
│   ├── csv_to_ledger.py                   ← CSV → ledger; injects person, detects inter-spouse transfers
│   ├── generate_reports.py               ← ledger queries → Excel (--scope combined|samuel|fuiyee|all)
│   ├── run_all.sh                         ← full pipeline orchestration
│   └── requirements.txt                  ← Python dependencies
│
├── ledger/                                ← accounting database (person-first accounts)
│   ├── main.journal                       ← master file (includes all sub-journals)
│   ├── accounts.journal                   ← chart of accounts (auto-generated, person-first)
│   ├── commodities.journal               ← MYR currency definition
│   ├── categories.rules                  ← JSON rules: description → account mapping
│   ├── ambank/ maybank/ hsbc/ ambcc/      ← Samuel YYYY-MM.journal
│   └── fuiyee/{cimb,cimbcc,hsbc,rhb,uob}/  ← Fui Yee YYYY-MM.journal (+ opening-balance.journal)
│
├── csv/                                   ← intermediate parsed CSVs (for debugging)
│   ├── ambank/ maybank/ hsbc/ ambcc/
│   └── fuiyee/{cimb,cimbcc,hsbc,rhb,uob}/  (cimb/_statements/ holds raw pre-dedup CSVs)
│
└── reports/                               ← generated Excel reports (per scope)
    ├── monthly_{combined,samuel,fuiyee}_YYYY-MM.xlsx
    └── annual_{combined,samuel,fuiyee}_YYYY-YYYY.xlsx
```

**Account structure is person-first:** `<Root>:<Person>:<...>` (e.g. `Expenses:Samuel:Groceries`,
`Assets:FuiYee:Bank:CIMB`). `Equity` is shared/person-neutral. This lets each person be reviewed
independently (`bal Expenses:Samuel`) and combined (`bal Expenses`). `categories.rules` outputs
person-neutral accounts; `csv_to_ledger.py` injects the owner segment based on `config.OWNER[bank]`.

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
| Samuel's contribution | **MYR 3,551.00 / month** to Wong Fui Yee's CIMB → `Expenses:Samuel:Housing:Mortgage` |
| Real bank payment | CIMB `I-PYMT TO LOAN/FINANCING` auto-deduction (variable) → `Expenses:FuiYee:Housing:Mortgage` |
| Started | July 2025 |

**How the money flows (both are tracked now that Fui Yee's CIMB is in the ledger):**
1. Samuel transfers RM3,551/mth into Fui Yee's CIMB (`Expenses:Samuel:Housing:Mortgage`, amount-match rule in csv_to_ledger.py).
2. The bank auto-deducts the housing loan from CIMB (`I-PYMT TO LOAN/FINANCING` → `Expenses:FuiYee:Housing:Mortgage`, categories.rules priority 10).
3. Samuel's funding *arriving* in CIMB is detected as an inter-spouse transfer (payee `LIE ZHI HOU`) → `Equity:Transfer`, so Fui Yee's income isn't inflated.

> **Per user decision: the mortgage is intentionally counted on BOTH sides** — Samuel's per-person report
> shows his RM3,551/mth contribution, Fui Yee's shows the actual loan payments. The **combined** report
> therefore double-counts the mortgage (~RM66.8k vs ~RM52.6k real). If you later want it counted once,
> reclassify Samuel's `Expenses:Samuel:Housing:Mortgage` to `Equity:Transfer`.

**Known edge cases:**
- July 2025 first payment from AmBank was MYR 3,387.73 (possibly prorated) — not auto-detected; reclassify manually if needed
- Lump-sum payments (e.g. MYR 12,102 = 3 months + aircond) require manual split

---

## Accounts

**Samuel**

| Account | Type | Ledger name | Coverage |
|---------|------|-------------|----------|
| AmBank 8881020737788 | Savings | `Assets:Samuel:Bank:AmBank` | Apr 2025 – May 2026 |
| Maybank 158088-403774 | Savings-i | `Assets:Samuel:Bank:Maybank` | Mar 2025 – May 2026 |
| HSBC Credit Card | Credit card | `Liabilities:Samuel:CreditCard:HSBC` | Sep 2025 – Jun 2026 |
| AmBank CC (CARz Gold VISA) | Credit card | `Liabilities:Samuel:CreditCard:AmBankCC` | Oct 2025 – Jun 2026 |

**Fui Yee** (added Jul 2026; all balances reconcile to statements)

| Account | Type | Ledger name | Coverage | Current bal |
|---------|------|-------------|----------|-------------|
| CIMB Savings 70-1778898-5 | Savings | `Assets:FuiYee:Bank:CIMB` | Dec 2024 – Jun 2026 | 5,720.48 |
| CIMB Credit Card | Credit card | `Liabilities:FuiYee:CreditCard:CIMBCC` | Sep 2024 – Jun 2026 | −1,876.75 |
| HSBC Credit Card | Credit card (OCR) | `Liabilities:FuiYee:CreditCard:HSBC` | Aug 2025 – Jun 2026 | −1,305.77 |
| RHB (Shell Visa + MC Cashback) | Credit card | `Liabilities:FuiYee:CreditCard:RHB` | Aug 2025 – Jun 2026 | −1,581.62 |
| UOB Credit Card | Credit card | `Liabilities:FuiYee:CreditCard:UOB` | Aug 2025 – Jun 2026 | −1,032.15 |

**Opening balances:** CIMB savings 54,260.18 (before Dec 2024); CIMBCC −687.96 (Sep 2024 prev balance).
Stored as `ledger/fuiyee/<bank>/opening-balance.journal`.

**Data notes / gaps:** UOB `June 25.pdf` is a mislabeled duplicate of May 2026 → **June 2025 UOB is missing**.
HSBC 2026-02 is off by RM0.02 (cosmetic OCR misread of previous-balance digit; transactions correct).
Fui Yee's `Income:FuiYee:Other` and `Expenses:FuiYee:Uncategorized` still hold ~RM210k / ~RM34k of
CIMB flows pending categorisation (largest: `IBG CREDIT` inflows RM108k — source unknown, needs review).

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

# 1b. Parse Fui Yee's statements
python3 scripts/parse_fy_cimb.py --all
python3 scripts/dedup_fy_cimb.py           # REQUIRED: CIMB issues overlapping monthly+quarterly statements
python3 scripts/parse_fy_cimbcc.py --all
python3 scripts/parse_fy_hsbc.py --all     # OCR
python3 scripts/parse_fy_rhb.py --all
python3 scripts/parse_fy_uob.py --all

# 2. Convert CSVs to ledger journals + activate includes
python3 scripts/csv_to_ledger.py --bank ambank --all
python3 scripts/csv_to_ledger.py --bank maybank --all
for b in fy_cimb fy_cimbcc fy_hsbc fy_rhb fy_uob; do python3 scripts/csv_to_ledger.py --bank $b --all; done
python3 scripts/csv_to_ledger.py --bank ambank --update-main   # activates all commented includes

# 3. Generate reports (all three scopes by default)
python3 scripts/generate_reports.py --annual                       # combined + samuel + fuiyee
python3 scripts/generate_reports.py --all --scope combined
python3 scripts/generate_reports.py --month 2026-06 --scope fuiyee
```

> **CIMB dedup is mandatory.** Fui Yee's CIMB issues both monthly and quarterly statements that overlap.
> `parse_fy_cimb.py` writes per-statement CSVs; `dedup_fy_cimb.py` archives them to `csv/fuiyee/cimb/_statements/`,
> de-dups by `(date, amount, balance)`, and regroups by transaction month. Run it before `csv_to_ledger`.

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
# Combined household balance overview
ledger -f ledger/main.journal bal Assets Liabilities --depth 4

# Per-person review (independent)
ledger -f ledger/main.journal bal Income:Samuel Expenses:Samuel
ledger -f ledger/main.journal bal Income:FuiYee Expenses:FuiYee

# Combined P&L by category (both people aggregated)
ledger -f ledger/main.journal bal Income Expenses --depth 3

# One person's net worth
ledger -f ledger/main.journal bal Assets:FuiYee Liabilities:FuiYee

# Spending in a specific month (person or combined)
ledger -f ledger/main.journal bal Expenses:FuiYee --period "2026-06" --flat

# Uncategorised (needs review) — per person
ledger -f ledger/main.journal reg Expenses:FuiYee:Uncategorized

# Inter-account / inter-spouse transfers
ledger -f ledger/main.journal reg Equity:Transfer
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

Rules output **person-neutral** accounts (e.g. `Expenses:Groceries`, `Income:Salary`); `csv_to_ledger.py`
injects the owner (`Samuel`/`FuiYee`) based on `config.OWNER[bank]`. `Equity:*` stays shared.
Inter-spouse transfers are auto-detected (Samuel = `LIE ZHI HOU`, Fui Yee = `WONG FUI YEE`) → `Equity:Transfer`.

After editing rules, regenerate the affected journals:
```bash
python3 scripts/csv_to_ledger.py --bank maybank --all
python3 scripts/csv_to_ledger.py --bank ambank --all
for b in fy_cimb fy_cimbcc fy_hsbc fy_rhb fy_uob; do python3 scripts/csv_to_ledger.py --bank $b --all; done
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
| AmBank (Samuel) | `AMB-PDF-YYYY-MM.pdf` | Statement month |
| HSBC (Samuel) | `HSBC-PDF-YYYY-MM.pdf` | Statement month |
| Maybank (Samuel) | `AcctNo_YYYYMMDD.pdf` | Last day of month |
| AmBank CC (Samuel) | `AMBCC-PDF-YYYY-MM.pdf` | Statement month |
| CIMB savings (Fui Yee) | `Mon YY.pdf` (e.g. `July 25.pdf`) | Statement month |
| CIMB CC (Fui Yee) | `MM_YY_CIMB.pdf` | Statement month |
| HSBC (Fui Yee) | `YYYY-MM-12_Statement.pdf` | Statement date |
| RHB (Fui Yee) | `RHB_MonYY.pdf` | Statement month |
| UOB (Fui Yee) | `Mon YY.pdf` | Statement month |

Keep filenames exactly as downloaded from the bank portals.
Parsers derive statement month from the filename/PDF; the pipeline keys journals/CSVs as `YYYY-MM`.

---

## Installing Dependencies

```bash
pip3 install --break-system-packages pdfplumber openpyxl xlsxwriter
# For HSBC OCR:
brew install tesseract poppler
pip3 install --break-system-packages pytesseract pdf2image Pillow
```
