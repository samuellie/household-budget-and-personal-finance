"""
MCP Server for Household Finance

Exposes ledger-cli queries and pipeline scripts as tools for Claude Desktop.
12 tools across 4 groups: ledger queries, pipeline, categorisation, convenience.

Start: python3 scripts/mcp_server.py
Config: ~/Library/Application Support/Claude/claude_desktop_config.json
"""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated

# Make sibling scripts importable (same pattern as csv_to_ledger.py)
sys.path.insert(0, str(Path(__file__).parent))
import config
import categorize as _categorize_module
from categorize import categorize_with_confidence

from mcp.server.fastmcp import FastMCP

# ── Constants ─────────────────────────────────────────────────────────────────

BASE_DIR = config.BASE_DIR
MAIN_JOURNAL = str(config.MAIN_JOURNAL)
SCRIPTS_DIR = Path(__file__).parent
PYTHON = sys.executable  # venv Python

ALLOWED_LEDGER_SUBCOMMANDS = {
    "bal", "balance", "reg", "register", "print",
    "stats", "accounts", "payees", "commodities",
}

ALLOWED_SCRIPTS = {
    "parse_ambank.py",
    "parse_maybank.py",
    "parse_hsbc.py",
    "csv_to_ledger.py",
    "generate_reports.py",
}

ALLOWED_BANKS = {"ambank", "maybank", "hsbc"}
ALLOWED_PIPELINE_STAGES = {"parse", "convert", "validate", "report", "all"}

SHELL_METACHAR_RE = re.compile(r"[;&|`$(){}<>]")

mcp = FastMCP(
    "household-finance",
    instructions=(
        "Tools for querying Samuel and Fui Yee's household finances (MYR). "
        "Covers AmBank, Maybank, and HSBC credit card. "
        "Use ledger_balance / ledger_register for read-only queries. "
        "Use parse_statements → convert_to_ledger → generate_report for the pipeline. "
        "Use account_balances and spending_summary for quick summaries."
    ),
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_ledger(args: list[str], timeout: int = 30) -> str:
    """Run ledger -f main.journal <args>. Returns stdout or ERROR: ... string."""
    cmd = ["ledger", "-f", MAIN_JOURNAL] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
        )
    except FileNotFoundError:
        return "ERROR: ledger-cli not found. Install with: brew install ledger"
    except subprocess.TimeoutExpired:
        return f"ERROR: ledger query timed out after {timeout}s."
    if result.returncode != 0:
        err = result.stderr.strip() or "(no error output)"
        return f"ERROR: {err}"
    output = result.stdout.strip()
    return output if output else "(no output — no matching transactions)"


def _run_script(script_name: str, args: list[str], timeout: int = 120) -> str:
    """Run scripts/<script_name> with PYTHON. Returns combined stdout+stderr."""
    if script_name not in ALLOWED_SCRIPTS:
        return f"ERROR: '{script_name}' is not an allowed script."
    cmd = [PYTHON, str(SCRIPTS_DIR / script_name)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
        )
    except FileNotFoundError:
        return f"ERROR: Could not execute {script_name}. Python not found at {PYTHON}."
    except subprocess.TimeoutExpired:
        return f"ERROR: {script_name} timed out after {timeout}s."
    combined = []
    if result.stdout.strip():
        combined.append(result.stdout.strip())
    if result.stderr.strip():
        combined.append("--- stderr ---\n" + result.stderr.strip())
    output = "\n".join(combined)
    if result.returncode != 0:
        return f"ERROR (exit {result.returncode}):\n{output}"
    return output if output else "(completed with no output)"


def _validate_bank(bank: str) -> str | None:
    """Returns an error string if bank is invalid, else None."""
    if bank not in ALLOWED_BANKS:
        return f"ERROR: bank must be one of {sorted(ALLOWED_BANKS)}, got '{bank}'."
    return None


def _truncate(text: str, max_lines: int, label: str = "output") -> str:
    lines = text.splitlines()
    if len(lines) > max_lines:
        kept = "\n".join(lines[:max_lines])
        return f"{kept}\n\n[{label} truncated — {len(lines) - max_lines} more lines omitted]"
    return text


# ── Ledger Query Tools ────────────────────────────────────────────────────────

@mcp.tool()
def ledger_balance(
    accounts: Annotated[str, "Space-separated account filter, e.g. 'Expenses' or 'Assets Liabilities'"] = "Assets Liabilities",
    depth: Annotated[int | None, "Limit hierarchy depth (e.g. 2 or 3)"] = None,
    period: Annotated[str | None, "Period filter, e.g. '2026-03' or 'this month'"] = None,
    monthly: Annotated[bool, "Show monthly breakdown (--monthly --collapse)"] = False,
    flat: Annotated[bool, "Flat output instead of tree (--flat)"] = False,
) -> str:
    """
    Run a ledger balance query. Returns account balances in MYR.

    Examples:
    - All account balances: accounts="Assets Liabilities", depth=3
    - Monthly expenses: accounts="Expenses", depth=2, monthly=True
    - March 2026 spending: accounts="Expenses", period="2026-03", flat=True
    - Cash flow trend: accounts="Income Expenses", monthly=True
    """
    args = ["bal"] + accounts.split()
    if depth is not None:
        args += ["--depth", str(depth)]
    if period:
        args += ["--period", period]
    if monthly:
        args += ["--monthly", "--collapse"]
    if flat:
        args += ["--flat"]
    return _run_ledger(args)


@mcp.tool()
def ledger_register(
    account: Annotated[str, "Account to show transactions for, e.g. 'Expenses:Food' or 'Expenses:Uncategorized'"],
    period: Annotated[str | None, "Period filter, e.g. '2026-03'"] = None,
    payee: Annotated[str | None, "Filter by payee/description regex, e.g. 'GRAB'"] = None,
    limit: Annotated[int, "Max rows to return (default 50)"] = 50,
) -> str:
    """
    Show individual transactions for an account (ledger register).

    Examples:
    - All food transactions: account="Expenses:Food"
    - March subscriptions: account="Expenses:Subscriptions", period="2026-03"
    - Grab rides: account="Expenses:Transport", payee="GRAB"
    - Review uncategorised: account="Expenses:Uncategorized"
    """
    args = ["reg", account]
    if period:
        args += ["--period", period]
    if payee:
        args += ["--payee", payee]
    if limit and limit > 0:
        args += ["--head", str(limit)]
    return _run_ledger(args)


@mcp.tool()
def ledger_custom(
    command: Annotated[str, "ledger subcommand + args as a string, e.g. 'bal Expenses:Food --monthly --begin 2025-09'"],
) -> str:
    """
    Run an arbitrary ledger command for advanced queries.

    The first word must be a known subcommand: bal, balance, reg, register,
    print, stats, accounts, payees, commodities.

    Examples:
    - "bal Expenses:Food --monthly"
    - "reg Assets:Bank:AmBank --begin 2026-01 --end 2026-03"
    - "stats"
    - "accounts"
    """
    if SHELL_METACHAR_RE.search(command):
        return "ERROR: command contains disallowed characters (shell metacharacters are not permitted)."
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return f"ERROR: could not parse command: {e}"
    if not tokens:
        return "ERROR: empty command."
    subcommand = tokens[0].lower()
    if subcommand not in ALLOWED_LEDGER_SUBCOMMANDS:
        return (
            f"ERROR: '{subcommand}' is not an allowed ledger subcommand. "
            f"Allowed: {sorted(ALLOWED_LEDGER_SUBCOMMANDS)}"
        )
    output = _run_ledger(tokens)
    return _truncate(output, max_lines=500, label="ledger output")


# ── Pipeline Tools ────────────────────────────────────────────────────────────

@mcp.tool()
def parse_statements(
    bank: Annotated[str, "Bank to parse: 'ambank', 'maybank', or 'hsbc'"],
    all_statements: Annotated[bool, "Parse all available PDFs for this bank"] = True,
    year: Annotated[int | None, "Parse only a specific year, e.g. 2026"] = None,
    file_path: Annotated[str | None, "Parse a single specific PDF (path relative to project root)"] = None,
) -> str:
    """
    Parse bank statement PDFs into CSV files.

    Outputs CSV files to csv/{bank}/YYYY-MM.csv. For HSBC, also creates
    a _review.txt file for low-confidence OCR items.

    Note: HSBC requires tesseract and poppler (brew install tesseract poppler).

    Examples:
    - Parse all AmBank statements: bank="ambank", all_statements=True
    - Parse all 2026 Maybank: bank="maybank", year=2026
    - Parse one file: bank="maybank", file_path="Bank Statements/Samuel/Maybank/2026/158088-403774_20260430.pdf"
    """
    err = _validate_bank(bank)
    if err:
        return err

    script = f"parse_{bank}.py"
    args: list[str] = []

    if file_path:
        # Validate path stays within project
        resolved = (BASE_DIR / file_path).resolve()
        if not str(resolved).startswith(str(BASE_DIR.resolve())):
            return "ERROR: file_path must be inside the project directory."
        args.append(str(resolved))
    elif year is not None:
        args += ["--year", str(year)]
    elif all_statements:
        args.append("--all")
    else:
        return "ERROR: specify all_statements=True, a year, or a file_path."

    return _run_script(script, args)


@mcp.tool()
def convert_to_ledger(
    bank: Annotated[str, "Bank to convert: 'ambank', 'maybank', or 'hsbc'"],
    all_csvs: Annotated[bool, "Convert all CSVs for this bank"] = True,
    input_file: Annotated[str | None, "Convert a single CSV (path relative to project root)"] = None,
    update_main: Annotated[bool, "Activate include lines in ledger/main.journal after converting"] = False,
) -> str:
    """
    Convert parsed CSV files into double-entry ledger journal files.

    Outputs journal files to ledger/{bank}/YYYY-MM.journal.
    Set update_main=True to automatically uncomment the matching include lines
    in ledger/main.journal so the new journals are picked up by ledger-cli.

    Examples:
    - Convert all AmBank CSVs: bank="ambank", all_csvs=True
    - Convert one file: bank="maybank", input_file="csv/maybank/2026-04.csv"
    - Convert + activate: bank="ambank", all_csvs=True, update_main=True
    """
    err = _validate_bank(bank)
    if err:
        return err

    args = ["--bank", bank]

    if input_file:
        resolved = (BASE_DIR / input_file).resolve()
        if not str(resolved).startswith(str(BASE_DIR.resolve())):
            return "ERROR: input_file must be inside the project directory."
        args += ["--input", str(resolved)]
    elif all_csvs:
        args.append("--all")
    else:
        return "ERROR: specify all_csvs=True or an input_file."

    if update_main:
        args.append("--update-main")

    return _run_script("csv_to_ledger.py", args)


@mcp.tool()
def generate_report(
    months: Annotated[list[str] | None, "List of months to generate, e.g. ['2026-03', '2026-02']"] = None,
    annual: Annotated[bool, "Generate the annual summary report"] = False,
    all_reports: Annotated[bool, "Generate all monthly + annual reports"] = False,
) -> str:
    """
    Generate Excel reports from ledger data.

    Monthly reports saved to reports/monthly_YYYY-MM.xlsx (3 sheets:
    Summary, Category Breakdown, Transactions).
    Annual report saved to reports/annual_2025-2026.xlsx (3 sheets:
    Monthly Trends, Category Annual, Account Balances).

    Examples:
    - March 2026 report: months=["2026-03"]
    - Annual report: annual=True
    - Everything: all_reports=True
    - Two months: months=["2026-02", "2026-03"]
    """
    args: list[str] = []

    if all_reports:
        args.append("--all")
    else:
        if months:
            for m in months:
                args += ["--month", m]
        if annual:
            args.append("--annual")
        if not args:
            return "ERROR: specify months, annual=True, or all_reports=True."

    return _run_script("generate_reports.py", args, timeout=120)


@mcp.tool()
def run_pipeline(
    stage: Annotated[str, "Pipeline stage: 'parse', 'convert', 'validate', 'report', or 'all'"] = "all",
) -> str:
    """
    Run the full pipeline or a specific stage via run_all.sh.

    Stages:
    - parse: Parse all bank PDFs to CSV
    - convert: Convert all CSVs to ledger journals + activate includes
    - validate: Run ledger balance check, warn on uncategorised transactions
    - report: Generate all Excel reports
    - all: Run all stages in sequence (parse → convert → validate → report)

    Tip: after adding new bank statements, run stage='all' to fully refresh.
    """
    if stage not in ALLOWED_PIPELINE_STAGES:
        return (
            f"ERROR: stage must be one of {sorted(ALLOWED_PIPELINE_STAGES)}, got '{stage}'."
        )
    run_all = SCRIPTS_DIR / "run_all.sh"
    try:
        result = subprocess.run(
            ["bash", str(run_all), stage],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(BASE_DIR),
        )
    except FileNotFoundError:
        return "ERROR: bash not found."
    except subprocess.TimeoutExpired:
        return "ERROR: pipeline timed out after 300s."
    combined = []
    if result.stdout.strip():
        combined.append(result.stdout.strip())
    if result.stderr.strip():
        combined.append("--- stderr ---\n" + result.stderr.strip())
    output = "\n".join(combined) or "(no output)"
    if result.returncode != 0:
        return f"ERROR (exit {result.returncode}):\n{output}"
    return _truncate(output, max_lines=200, label="pipeline output")


# ── Categorisation Tools ──────────────────────────────────────────────────────

@mcp.tool()
def categorize_transaction(
    description: Annotated[str, "Transaction description text, e.g. 'GRAB FOOD delivery'"],
    is_credit: Annotated[bool, "True for income/credit transactions, False for expenses/debits"] = False,
) -> str:
    """
    Test how a transaction description would be categorised.

    Returns the matched ledger account and whether a specific rule matched
    (or whether it fell through to the default).

    Useful for:
    - Checking why a transaction ended up in Expenses:Uncategorized
    - Verifying a new rule in categories.rules works correctly
    - Understanding which category a merchant maps to

    Examples:
    - categorize_transaction("SPOTIFY") → Expenses:Subscriptions:Spotify
    - categorize_transaction("PETRONAS") → Expenses:Transport:Fuel
    - categorize_transaction("TINKERVE TECHNOLOGY", is_credit=True) → Income:Salary:Samuel
    """
    # Clear cache so edits to categories.rules are picked up without restart
    _categorize_module._rules_cache = None
    account, matched = categorize_with_confidence(description, is_debit=not is_credit)
    return json.dumps({
        "account": account,
        "matched": matched,
        "status": "rule matched" if matched else "no rule matched — using default",
    }, indent=2)


@mcp.tool()
def list_categories() -> str:
    """
    Show all categorisation rules from ledger/categories.rules.

    Rules are sorted by priority (highest first). Each rule has:
    - pattern: regex matched against transaction descriptions (case-insensitive)
    - account: the ledger account it maps to
    - priority: higher number = checked first
    - note: optional description of what merchants this covers

    Use this to understand existing rules before adding new ones.
    """
    with open(config.CATEGORIES_RULES, encoding="utf-8") as f:
        data = json.load(f)
    # Sort by priority descending for readability
    data["rules"] = sorted(data["rules"], key=lambda r: r["priority"], reverse=True)
    return json.dumps(data, indent=2)


@mcp.tool()
def list_uncategorized() -> str:
    """
    List all transactions currently mapped to Expenses:Uncategorized.

    These are transactions that didn't match any rule in categories.rules
    and need manual review. After identifying patterns, add rules to
    ledger/categories.rules and re-run convert_to_ledger to reclassify them.
    """
    output = _run_ledger(["reg", "Expenses:Uncategorized"])
    if output.startswith("(no output"):
        return "No uncategorized transactions found."
    return output


# ── Convenience Tools ─────────────────────────────────────────────────────────

@mcp.tool()
def account_balances() -> str:
    """
    Quick summary of all account balances (Assets and Liabilities, depth 3).

    Shows:
    - Assets:Bank:AmBank — savings account balance
    - Assets:Bank:Maybank — savings-i account balance
    - Liabilities:CreditCard:HSBC — credit card outstanding balance
    - Net total

    All amounts in MYR.
    """
    return _run_ledger(["bal", "Assets", "Liabilities", "--depth", "3"])


@mcp.tool()
def spending_summary(
    period: Annotated[str | None, "Month to summarise, e.g. '2026-03'. Omit for all-time totals."] = None,
) -> str:
    """
    Expense breakdown by category (Expenses at depth 2).

    Shows how much was spent in each top-level category (Food, Transport,
    Subscriptions, etc.) for the given period or all time.

    Examples:
    - March 2026 spending: period="2026-03"
    - All-time totals: no period
    - Last quarter: period="2026-Q1" (ledger quarter syntax)
    """
    args = ["bal", "Expenses", "--depth", "2"]
    if period:
        args += ["--period", period]
    return _run_ledger(args)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
