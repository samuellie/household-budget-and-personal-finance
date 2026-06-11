"""
Transaction categorisation engine.

Reads ledger/categories.rules and matches transaction descriptions
against regex patterns in priority order.

Usage as a module:
    from categorize import categorize
    account = categorize("SPOTIFY", is_debit=True)

Usage from command line (for testing):
    python3 categorize.py "GRAB FOOD delivery"
    python3 categorize.py "TINKERVE TECHNOLOGY" --credit
"""

import json
import re
import sys
from pathlib import Path

import config

_rules_cache = None


def _load_rules():
    global _rules_cache
    if _rules_cache is None:
        with open(config.CATEGORIES_RULES, encoding="utf-8") as f:
            data = json.load(f)
        rules = sorted(data["rules"], key=lambda r: r["priority"], reverse=True)
        _rules_cache = (rules, data["default_expense"], data["default_income"])
    return _rules_cache


def categorize(description: str, is_debit: bool = True) -> str:
    """Return the ledger account name for a transaction."""
    account, _ = categorize_with_confidence(description, is_debit)
    return account


def categorize_with_confidence(description: str, is_debit: bool = True) -> tuple:
    """
    Returns (account, matched) where matched=False means it fell through to default.
    """
    rules, default_expense, default_income = _load_rules()
    for rule in rules:
        if re.search(rule["pattern"], description, re.IGNORECASE):
            return rule["account"], True
    default = default_expense if is_debit else default_income
    return default, False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test transaction categorisation")
    parser.add_argument("description", help="Transaction description to classify")
    parser.add_argument("--credit", action="store_true", help="Treat as a credit (income)")
    args = parser.parse_args()

    account, matched = categorize_with_confidence(args.description, is_debit=not args.credit)
    status = "MATCHED" if matched else "DEFAULT (no rule matched)"
    print(f"Account : {account}")
    print(f"Status  : {status}")
