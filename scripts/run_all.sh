#!/usr/bin/env bash
# ============================================================
# Household Finance Pipeline — full run or selective stages
# ============================================================
# Usage:
#   ./scripts/run_all.sh                   — full pipeline
#   ./scripts/run_all.sh parse             — only parse PDFs to CSV
#   ./scripts/run_all.sh convert           — convert CSVs to ledger + fix refunds
#   ./scripts/run_all.sh fix-refunds       — reclassify Income:Refund entries only
#   ./scripts/run_all.sh report            — only generate reports
#   ./scripts/run_all.sh report --month 2026-01
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON=python3

cd "$BASE_DIR"

STAGE="${1:-all}"
shift 2>/dev/null || true  # remaining args passed to report step

# ── Stage: parse ─────────────────────────────────────────────────────────────
run_parse() {
  echo ""
  echo "━━━ Step 1: Parse bank statement PDFs to CSV ━━━━━━━━━━━━━━━━━━━━━━━━━"
  $PYTHON "$SCRIPT_DIR/parse_ambank.py"  --all
  $PYTHON "$SCRIPT_DIR/parse_maybank.py" --all
  $PYTHON "$SCRIPT_DIR/parse_ambcc.py"   --all
  echo ""
  echo "Note: HSBC requires OCR (brew install tesseract poppler first)."
  echo "      Run manually when ready: python3 scripts/parse_hsbc.py --all"
}

# ── Stage: convert ────────────────────────────────────────────────────────────
run_convert() {
  echo ""
  echo "━━━ Step 2: Convert CSVs to ledger journals ━━━━━━━━━━━━━━━━━━━━━━━━━━"
  $PYTHON "$SCRIPT_DIR/csv_to_ledger.py" --bank ambank  --all
  $PYTHON "$SCRIPT_DIR/csv_to_ledger.py" --bank maybank --all
  $PYTHON "$SCRIPT_DIR/csv_to_ledger.py" --bank hsbc    --all 2>/dev/null || true
  $PYTHON "$SCRIPT_DIR/csv_to_ledger.py" --bank ambcc   --all

  echo ""
  echo "━━━ Step 3: Activate journal includes in main.journal ━━━━━━━━━━━━━━━━"
  $PYTHON "$SCRIPT_DIR/csv_to_ledger.py" --bank ambank --update-main
}

# ── Stage: fix-refunds ────────────────────────────────────────────────────────
run_fix_refunds() {
  echo ""
  echo "━━━ Step 3b: Reclassify Income:Refund → negate original expense ━━━━━━━"
  $PYTHON "$SCRIPT_DIR/fix_refunds.py"
}

# ── Stage: validate ───────────────────────────────────────────────────────────
run_validate() {
  echo ""
  echo "━━━ Step 4: Validate ledger ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  ledger -f "$BASE_DIR/ledger/main.journal" bal > /dev/null
  echo "  ✓ Ledger parsed without errors."
  echo ""
  echo "  Account balances:"
  ledger -f "$BASE_DIR/ledger/main.journal" bal Assets Liabilities --depth 2
  echo ""
  UNCATEGORIZED=$(ledger -f "$BASE_DIR/ledger/main.journal" bal Expenses:Uncategorized 2>/dev/null | grep -c "MYR" || echo 0)
  if [ "$UNCATEGORIZED" -gt 0 ]; then
    echo "  ⚠ There are uncategorised transactions. Run:"
    echo "    ledger -f ledger/main.journal reg Expenses:Uncategorized"
  fi
}

# ── Stage: report ─────────────────────────────────────────────────────────────
run_report() {
  echo ""
  echo "━━━ Step 5: Generate Excel reports ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  if [ $# -gt 0 ]; then
    $PYTHON "$SCRIPT_DIR/generate_reports.py" "$@"
  else
    $PYTHON "$SCRIPT_DIR/generate_reports.py" --all
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
case "$STAGE" in
  parse)
    run_parse
    ;;
  convert)
    run_convert
    run_fix_refunds
    ;;
  fix-refunds)
    run_fix_refunds
    ;;
  validate)
    run_validate
    ;;
  report)
    run_report "$@"
    ;;
  all|"")
    run_parse
    run_convert
    run_fix_refunds
    run_validate
    run_report
    echo ""
    echo "━━━ Done ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Reports saved to: $BASE_DIR/reports/"
    ;;
  *)
    echo "Usage: $0 [parse|convert|fix-refunds|validate|report|all] [options]"
    exit 1
    ;;
esac
