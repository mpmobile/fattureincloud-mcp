# fattureincloud-mcp

[![PyPI](https://img.shields.io/pypi/v/fattureincloud-mcp)](https://pypi.org/project/fattureincloud-mcp/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![MCPB](https://img.shields.io/badge/MCPB-compatible-blue)](https://github.com/modelcontextprotocol/mcpb)
[![Listed in italia-mcp-servers](https://img.shields.io/badge/listed%20in-italia--mcp--servers-blue)](https://github.com/bsab/italia-mcp-servers)

<!-- mcp-name: io.github.aringad/fattureincloud-mcp -->

by **[Mediaform s.c.r.l.](https://media-form.it)** — Genova, Italy

MCP server that connects Claude (Desktop, Code, or any MCP client) to **FattureInCloud**, the leading Italian SaaS for electronic invoicing. Manage invoices, credit notes, proformas, clients, suppliers, cost/revenue centers, and supplier expenses through natural language. Italy mandates B2B/B2C e-invoicing through the Sistema di Interscambio (SDI) — this server brings AI-assisted billing to that compliance-driven workflow.

> ⚠️ **Unofficial integration.** Not affiliated with, endorsed by, or sponsored by TeamSystem S.p.A., owner of the FattureInCloud trademark. The trademark is used here for descriptive purposes only.

## Features (24 tools)

| Tool | Description |
|------|-------------|
| `list_invoices` | List issued invoices / credit notes / proformas / quotes, filtered by client, date / date range / year-month, payment status (overdue, unpaid, ...) — paginated |
| `get_invoice` | Full document detail by ID |
| `get_pdf_url` | PDF URL and web link for a document |
| `list_clients` | List clients with optional name / VAT filter — paginated |
| `get_company_info` | Connected company info |
| `create_client` | Create a new client |
| `update_client` | Update an existing client |
| `create_invoice` | Create a draft invoice (optional `revenue_center`) |
| `create_credit_note` | Create a draft credit note (optional `revenue_center`) |
| `create_proforma` | Create a draft proforma (optional `revenue_center`) |
| `convert_proforma_to_invoice` | Convert a proforma into a draft electronic invoice (optional `revenue_center`) |
| `update_document` | Partial update of a draft document (optional `revenue_center`) |
| `duplicate_invoice` | Duplicate an invoice with a new date (optional `revenue_center`) |
| `delete_invoice` | Delete a draft document |
| `send_to_sdi` | Send invoice / credit note to the Italian e-invoice system (SDI) |
| `get_invoice_status` | E-invoice status for a document |
| `send_email` | Send a courtesy copy by email |
| `list_received_documents` | List supplier documents by date / date range / year-month, paginated (exposes `cost_center` when present) |
| `get_received_document` | Full detail of a received document by ID |
| `create_received_document` | Create a passive document / expense (optional `cost_center`) |
| `list_cost_centers` | List configured cost / revenue centers |
| `get_situation` | Yearly dashboard: net revenue, collected, outstanding, costs, margin |
| `get_client_situation` | Single-client situation: invoiced, credit notes, collected, outstanding, overdue, open invoices, proformas |
| `check_numeration` | Verify invoice numbering continuity |

> Marking payments as "paid" is intentionally not exposed: the FattureInCloud API requires a payment account that cannot be reliably retrieved through the SDK. Use the FattureInCloud web panel for that operation.

## Installation

### Option 1 — Claude Desktop (MCPB bundle, recommended)

1. Download the latest `fattureincloud.mcpb` from the [GitHub Releases page](https://github.com/aringad/fattureincloud-mcp/releases/latest).
2. Drag the `.mcpb` file onto Claude Desktop, or use **Settings → Extensions → Install from file**.
3. When prompted, fill in your FattureInCloud API token, company ID, and (optional) sender email.

That's it — no Python setup, no virtualenv.

### Option 2 — Manual install via PyPI

For Claude Code users or any MCP client that reads `claude_desktop_config.json`-style configuration:

```bash
pip install fattureincloud-mcp
```

Then add to your MCP client configuration (for Claude Desktop, `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "fattureincloud": {
      "command": "python",
      "args": ["-m", "fattureincloud_mcp"],
      "env": {
        "FIC_ACCESS_TOKEN": "a/xxxxx.yyyyy.zzzzz",
        "FIC_COMPANY_ID": "123456",
        "FIC_SENDER_EMAIL": "billing@yourcompany.com"
      }
    }
  }
}
```

Restart your MCP client after editing the config.

### Option 3 — From the source code, without git (Windows / macOS)

Use this if you want the latest code from this repository and don't use git. You need Python 3.10 or newer, Claude Desktop, and your FattureInCloud credentials ([how to get them](#getting-the-fattureincloud-credentials)).

#### Windows

**1. Check whether Python is installed.** Open the Start menu, type `PowerShell` and open it, then run:

```powershell
py --version
```

If it prints `Python 3.10` or higher (e.g. `Python 3.12.7`), go to step 3. If it says the command is not recognized, or prints a version lower than 3.10, install Python (step 2). Note: typing `python` on a PC without Python may open the Microsoft Store instead of printing a version; that also means Python is not installed.

**2. Install Python.** Go to [python.org/downloads](https://www.python.org/downloads/), download the latest Windows installer and run it. On the first screen, **tick "Add python.exe to PATH"**, then click *Install Now*. When it finishes, close PowerShell, open a new one and run `py --version` again to confirm.

**3. Download the server.** On this repository's GitHub page click the green **Code** button → **Download ZIP**. Extract the ZIP and move the extracted folder to `C:\fattureincloud-mcp` (rename it if it is called `fattureincloud-mcp-main`). The folder must directly contain `server.py` and `requirements.txt`.

**4. Install the dependencies.** In PowerShell:

```powershell
cd C:\fattureincloud-mcp
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

This creates a private Python environment inside the folder (`.venv`), so nothing is installed system-wide.

**5. Configure Claude Desktop.** In Claude Desktop open **Settings → Developer → Edit Config**: this opens the folder with `claude_desktop_config.json`. Open that file with Notepad and add the server (if the file already has an `mcpServers` section, add only the `"fattureincloud": {...}` block inside it):

```json
{
  "mcpServers": {
    "fattureincloud": {
      "command": "C:\\fattureincloud-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\fattureincloud-mcp\\server.py"],
      "env": {
        "FIC_ACCESS_TOKEN": "a/xxxxx.yyyyy.zzzzz",
        "FIC_COMPANY_ID": "123456",
        "FIC_SENDER_EMAIL": "billing@yourcompany.com"
      }
    }
  }
}
```

Paths in this file need **double backslashes** (`\\`). Replace the three values in `env` with your credentials.

**6. Restart Claude Desktop.** Closing the window is not enough: right-click the Claude icon in the system tray (bottom right, near the clock) → **Quit**, then reopen it. Go on with [Check that it works](#check-that-it-works).

#### macOS

**1. Check whether Python is installed.** Open **Terminal** (Applications → Utilities → Terminal) and run:

```bash
python3 --version
```

If it prints `Python 3.10` or higher, go to step 3. If it prints `Python 3.9.x` or lower, or a window asks you to install the "command line developer tools", install Python (step 2): the Python bundled with Apple's developer tools is 3.9, too old for this server.

**2. Install Python.** Go to [python.org/downloads](https://www.python.org/downloads/), download the latest macOS installer (`.pkg`) and run it. When it finishes, close Terminal, open a new window and run `python3 --version` again to confirm.

**3. Download the server.** On this repository's GitHub page click the green **Code** button → **Download ZIP**. Safari extracts it automatically into Downloads. Move the extracted folder into your home folder and rename it `fattureincloud-mcp`, so its path is `/Users/<your-username>/fattureincloud-mcp`. The folder must directly contain `server.py` and `requirements.txt`.

**4. Install the dependencies.** In Terminal:

```bash
cd ~/fattureincloud-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
pwd
```

The last command prints the folder's full path (e.g. `/Users/mario/fattureincloud-mcp`): you need it in the next step.

**5. Configure Claude Desktop.** In Claude Desktop open **Settings → Developer → Edit Config**: this shows `claude_desktop_config.json` in Finder. Open it with TextEdit and add the server (if the file already has an `mcpServers` section, add only the `"fattureincloud": {...}` block inside it), using the path printed by `pwd`:

```json
{
  "mcpServers": {
    "fattureincloud": {
      "command": "/Users/mario/fattureincloud-mcp/.venv/bin/python",
      "args": ["/Users/mario/fattureincloud-mcp/server.py"],
      "env": {
        "FIC_ACCESS_TOKEN": "a/xxxxx.yyyyy.zzzzz",
        "FIC_COMPANY_ID": "123456",
        "FIC_SENDER_EMAIL": "billing@yourcompany.com"
      }
    }
  }
}
```

Replace the three values in `env` with your credentials. If TextEdit changes the straight quotes `"` into curly ones, disable *Edit → Substitutions → Smart Quotes*, or the file won't be valid.

**6. Restart Claude Desktop.** Quit it completely with **Cmd+Q** (closing the window is not enough) and reopen it.

#### Check that it works

In Claude Desktop, **Settings → Developer** should list `fattureincloud` as running. Then ask Claude: *"Show me the info of the company connected to FattureInCloud"*. If it answers with your company name, the server and the credentials work.

If the server shows an error instead: check that the two paths in the config point to existing files, that the JSON is valid (commas, brackets, quotes), and that the token and company ID are correct. A `403 Forbidden` error means the token lacks a permission (see below).

To update to a newer version later, download the ZIP again, replace the folder's files (keep `.venv`) and re-run the `pip install -r requirements.txt` command.

## Getting the FattureInCloud credentials

The server needs an **access token** and your **company ID**. The FattureInCloud APIs are included in every plan, also in the free trial; sending e-invoices to SDI through the API requires a paid plan.

FattureInCloud issues personal tokens through a private "app" that you create in your own account. It takes a few minutes and is done once:

**1. Create a private app.** Log into [FattureInCloud](https://secure.fattureincloud.it/), go to **Impostazioni → Sviluppatore** and click **Nuova app**. Give it a name (e.g. `Claude MCP`) and a short description. Keep the visibility **private**, and in **"E-mail degli utenti che possono utilizzare l'applicazione"** enter the email of your FattureInCloud account: without it, the API answers with a 403 error. Save, and note the app's **Client ID** shown on its page.

**2. Generate the token.** Go to **Impostazioni → Applicazioni collegate** and click **Collega una nuova applicazione**. Enter the Client ID from step 1, select the company to connect and the permissions to grant (below), then copy the **access token** (it starts with `a/`). This is your `FIC_ACCESS_TOKEN`. The token does not expire; you can revoke it from the same page, and change its permissions later with **Gestisci → Modifica** without generating a new one.

**Permissions to grant.** Grant only what you'll use; a missing permission shows up as a `403 Forbidden` error on the related tool.

| To use… | Grant |
|---|---|
| clients (`list_clients`, `create_client`, `update_client`) | `entity.clients` |
| invoices, credit notes, proformas, quotes | `issued_documents.invoices`, `issued_documents.credit_notes`, `issued_documents.proformas`, `issued_documents.quotes` |
| supplier documents (`list_received_documents`, `create_received_document`, …) | `received_documents` |
| cost / revenue centers, company settings | `settings` |

Each permission can be granted read-only or with full access (read and write). For a read-only setup (reports, lists, situations) read-only is enough; creating, editing, sending or deleting documents needs full access on the related permission.

**3. Find your company ID.** It is the number in the browser's address bar after you open your company in FattureInCloud (see the [Configuration](#configuration) table). Once the server is configured, *"Show me the info of the company connected to FattureInCloud"* confirms you picked the right company.

## Configuration

| Env var | Required | Description |
|---|---|---|
| `FIC_ACCESS_TOKEN` | yes | Personal API access token (starts with `a/`) |
| `FIC_COMPANY_ID` | yes | Numeric company ID, visible in the URL when logged into FattureInCloud |
| `FIC_SENDER_EMAIL` | required for `send_email` | Sender mailbox for courtesy copies |
| `FIC_CACHE_DIR` | no | Override cache directory (default `~/.fattureincloud-mcp/cache`) |
| `FIC_CACHE_DISABLED` | no | Set to `1` to disable the local cache |

How to obtain the token and the company ID: see [Getting the FattureInCloud credentials](#getting-the-fattureincloud-credentials). The `COMPANY_ID` is in the URL after `/c/` once you select a company.

## Usage examples

### Example 1 — Create an invoice for a known client

> "Find client 'Acme Srl' and create a draft invoice for €1,500 + VAT for consulting services in November 2026, payable in 30 days, on revenue center 'Project Alpha'."

Claude will:
1. Call `list_clients` (cached) and pick the matching client
2. Call `list_cost_centers` (cached) to validate `Project Alpha` exists
3. Call `create_invoice` with `client_id`, items, dates, payment terms, and `revenue_center="Project Alpha"`
4. Return the draft number and ask whether to send it to SDI

### Example 2 — Cost-center analysis

> "How much did I bill on the 'Project Alpha' revenue center in 2025? Break down by month."

Claude will:
1. Call `list_cost_centers` to confirm the label
2. Call `list_invoices` for year 2025 (the result includes `revenue_center` per invoice when set)
3. Filter by `revenue_center == "Project Alpha"` and aggregate by month

### Example 3 — Recurring invoices replay

> "Duplicate every invoice issued to 'Recurring Customer Co' in October 2025, set the new dates in November, keep 30-day payment terms."

Claude will:
1. Call `list_invoices` with `query="Recurring Customer Co"` and `month=10`, `year=2025`
2. For each result, call `duplicate_invoice` with `new_date` set in November
3. Return the list of new draft invoices and ask before sending

## Caching

To minimize redundant calls to the FattureInCloud API, this server caches client lookups and the cost-centers list locally as JSON files (default location `~/.fattureincloud-mcp/cache/`, scoped per `company_id`, 24-hour TTL). The cache is transparent: tool signatures don't change.

```bash
# Force refresh:
rm -rf ~/.fattureincloud-mcp/cache

# Disable temporarily:
export FIC_CACHE_DISABLED=1
```

## Cost / Revenue Centers

FattureInCloud uses one shared registry for cost centers (on supplier documents) and revenue centers (on issued documents). With this server you can:

- `list_cost_centers` — read the registry
- `revenue_center="<label>"` — assign on `create_invoice`, `create_credit_note`, `create_proforma`, `convert_proforma_to_invoice`, `update_document`, `duplicate_invoice`
- `cost_center="<label>"` — assign on `create_received_document`

The label must already exist; centers are managed from FattureInCloud's web UI (Settings → Cost Centers). Passing an unknown label returns the list of valid labels in the error message.

## Privacy & Data Handling

- API calls go **directly** from your machine to FattureInCloud's servers (`api-v2.fattureincloud.it`). No data is routed through Mediaform or any third-party server.
- The local cache is plaintext JSON in your home directory. You control it.
- Credentials live only in your `.env` (gitignored), shell, or MCP client `user_config`. They are never logged or transmitted to anyone other than FattureInCloud.
- Full Privacy Policy: **https://media-form.it/privacy-policy.html**
- See also [`docs/PRIVACY.md`](docs/PRIVACY.md) for a mirrored copy of the policy.

## Known issues

- **Invoice duplication may fail for some clients.** A specific client configuration triggers a failure path that hasn't been reproduced yet. Workaround: duplicate manually from the FattureInCloud web panel. Tracked in [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) — please report a reproducible case via [GitHub issues](https://github.com/aringad/fattureincloud-mcp/issues).

## Contributing

Issues and pull requests welcome at https://github.com/aringad/fattureincloud-mcp.

For any code change:

1. Fork and branch from `main` (`feat/...`, `fix/...`, `docs/...`).
2. Run `pytest tests/` — all 39 tests must stay green.
3. New code targeting cost-center / cache / new tools should ship with tests (target ≥80% coverage on new modules).
4. Conventional commits style (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).
5. Open a PR. The maintainer reviews changes against the FattureInCloud API contract.

## Security

For vulnerability disclosure see [`SECURITY.md`](SECURITY.md). Preferred channel: [GitHub Security Advisories](https://github.com/aringad/fattureincloud-mcp/security/advisories/new).

## License

MIT — see [`LICENSE`](LICENSE).

## Trademark

"FattureInCloud" is a trademark of TeamSystem S.p.A. This is an independent, community-built integration. It is not affiliated with, endorsed by, or sponsored by TeamSystem S.p.A. The trademark is used solely for descriptive purposes (to indicate the third-party service this software interoperates with).

## Author / Support

- Maintainer: **[Mediaform s.c.r.l.](https://media-form.it)** — Genova, Italy
- Issues: https://github.com/aringad/fattureincloud-mcp/issues
- Email: assistenza@mediaform.it
