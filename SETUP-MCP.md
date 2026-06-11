# Household Finance MCP Server — Setup Guide

Connects Claude Desktop to your ledger data and pipeline scripts via 12 tools.

---

## 1. One-Time Setup

Run this once from the project directory:

```bash
cd "/Users/samuellie/Desktop/Household budget and Personal Finance"
python3 -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]"
pip install -r scripts/requirements.txt
```

> **HSBC OCR only** (optional): `brew install tesseract poppler`

---

## 2. Configure Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`.

If the file doesn't exist yet, create it. Add or merge the `mcpServers` block:

```json
{
  "mcpServers": {
    "household-finance": {
      "command": "/Users/samuellie/Desktop/Household budget and Personal Finance/.venv/bin/python3",
      "args": [
        "/Users/samuellie/Desktop/Household budget and Personal Finance/scripts/mcp_server.py"
      ]
    }
  }
}
```

Then **restart Claude Desktop**.

---

## 3. Verify

After restarting, open a new Claude Desktop conversation and type:

> "What are my account balances?"

Claude should call the `account_balances` tool and return AmBank, Maybank, and HSBC balances in MYR.

You can also test the server from the terminal:

```bash
source .venv/bin/activate
mcp dev scripts/mcp_server.py   # opens interactive tool inspector
```

---

## 4. Available Tools (12)

### Ledger Queries (read-only)

| Tool | Example prompt |
|------|----------------|
| `account_balances` | "What are my account balances?" |
| `spending_summary` | "How much did I spend in March 2026?" |
| `ledger_balance` | "Show monthly cash flow for the last 6 months" |
| `ledger_register` | "List all Grab transactions in March" |
| `ledger_custom` | "Run: bal Expenses:Food --monthly" |

### Pipeline (modifies files)

| Tool | Example prompt |
|------|----------------|
| `parse_statements` | "Parse all new AmBank statements" |
| `convert_to_ledger` | "Convert Maybank CSVs to journals and activate them" |
| `generate_report` | "Generate the March 2026 Excel report" |
| `run_pipeline` | "Run the full pipeline" |

### Categorisation

| Tool | Example prompt |
|------|----------------|
| `categorize_transaction` | "How would 'LOTUS'S STORE' be categorised?" |
| `list_categories` | "Show all categorisation rules" |
| `list_uncategorized` | "What transactions need manual categorisation?" |

---

## 5. Adding a New Bank Statement

1. Download the PDF into the correct folder (see folder structure in CLAUDE.md)
2. Ask Claude Desktop:
   - "Parse the new Maybank statement"
   - "Convert it to a journal and activate the include"
   - "Generate the April 2026 report"

Or run everything at once: "Run the full pipeline"

---

## 6. Updating Categorisation Rules

1. Ask Claude Desktop: "What uncategorised transactions do I have?"
2. Review the list, identify patterns
3. Ask: "Add a rule for 'LOTUS'S STORE' mapping to Expenses:Food:Groceries"
   - Claude will edit `ledger/categories.rules` directly
4. Ask: "Re-run convert_to_ledger for all banks with update_main=True"
5. Verify: "How would 'LOTUS'S STORE' be categorised now?"

---

## 7. Troubleshooting

**Tools don't appear in Claude Desktop**
- Check the config JSON is valid (no trailing commas)
- Verify the `.venv/bin/python3` path exists: `ls ".venv/bin/python3"`
- Restart Claude Desktop fully (quit and reopen)

**"ledger-cli not found" error**
- Install: `brew install ledger`
- Verify: `which ledger`

**"ERROR: ..." from a pipeline tool**
- The stderr output is included in the response — read it for the specific failure
- Run the failing script manually: `source .venv/bin/activate && python3 scripts/parse_ambank.py --all`

**HSBC OCR fails**
- Install dependencies: `brew install tesseract poppler`
- Reactivate venv and install Python packages:
  `pip install pytesseract pdf2image Pillow`
