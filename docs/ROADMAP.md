# Roadmap

Planned work that is not scheduled yet.

## Release tooling (`scripts/validate.sh`)

- **Check that manifest tools match the server.** Today the script only prints how many tools `manifest.json` declares. It should load the tool list from the server (`fattureincloud_mcp.app.mcp.list_tools()`) and fail when names differ, so a new or renamed tool cannot ship without its manifest entry.
- **Smoke-test the built bundle.** Unpack `dist/fattureincloud.mcpb`, start `server.py` with `PYTHONPATH=lib` from a clean Python, and fail unless it answers `list_tools`. This would have caught the `mcp` 2.x incompatibility: a bundle built while dependencies were unpinned passed every check but could not start.

## Packaging

- **Per-platform MCPB bundles.** `build.sh` installs dependencies for the build machine, and `pydantic_core` is a native extension, so a bundle built on a Mac runs only on Macs with the same architecture and Python 3.12. `manifest.json` nevertheless declares `darwin`, `linux` and `win32`. Options: build on each OS (e.g. a GitHub Actions matrix) or cross-download wheels with `pip install --platform <tag> --python-version 3.12 --only-binary=:all:`, producing one `.mcpb` per platform.
- **Migrate to `mcp` 2.x.** Dependencies are pinned to `mcp>=1.26,<2`. Version 2 renames `FastMCP` to `MCPServer` and changes other APIs ([migration guide](https://py.sdk.modelcontextprotocol.io/v2/migration/)).

## Tools

- **`get_exposure_report`**: outstanding amounts across all clients, ranked. Calling `get_client_situation` once per client does not scale (hundreds of clients, 1000 requests/hour rate limit); the report should fetch all unpaid invoices and matured proformas in a few paginated calls and group them by client.
- **Fix the `create_credit_note` description.** It says amounts are "negated automatically", but the tool sends positive amounts (FIC applies the sign from the document type). The same wording is in `manifest.json`.

## Code

- **Split `tools/issued_documents.py` if it keeps growing** (about 550 lines today), e.g. reading/editing vs creation.
- **Build request bodies with the SDK models.** Write tools send plain dicts. The SDK coerces them into its own Pydantic models (`IssuedDocument`, `Entity`, `IssuedDocumentItemsListItem`, ...), so wrong values are rejected (e.g. `type: "fattura"`), but unknown field names are dropped silently: a typo such as `rc_centr` disappears without an error, also when building the SDK model directly. Constructing the SDK models instead of dicts lets the editor's type checker flag such typos; runtime behaviour is unchanged. Do it the next time the write tools are reworked, and re-run the write-tool snapshot comparison.
- **Typed tool outputs (structured output).** Return Pydantic models and publish an `outputSchema` per tool, so MCP clients know the response shape in advance. Start with the shared document row (`issued_doc_row`, used by `list_invoices` and `get_client_situation`) and the client situation. Pair it with a single naming convention for output keys, which today mix Italian (`get_situation`: `fatturato_lordo`) and English (`list_invoices`: `total`, `documents`). Renaming keys changes what Claude receives, so it needs a check of how the tools are used afterwards.
