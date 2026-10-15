"""FastMCP server instance and entry point.

Tools register themselves with `@mcp.tool(...)` in the `tools` modules; importing
them below is what makes them available. The SDK derives each tool's input schema
from the function signature and its description from the docstring.
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("fattureincloud", log_level="WARNING")


def ann(read_only=False, destructive=False, idempotent=False, open_world=True):
    """Shorthand for MCP tool annotations. openWorld defaults to True since
    every tool talks to the FattureInCloud API."""
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def tool(**kwargs):
    """`mcp.tool` with plain-text JSON output, as MCP clients received before FastMCP.

    The docstring becomes the tool description, collapsed to a single line so it can
    be wrapped freely in the source."""
    def decorator(fn):
        description = " ".join((fn.__doc__ or "").split())
        return mcp.tool(description=description, structured_output=False, **kwargs)(fn)
    return decorator


def main():
    mcp.run()


from .tools import clients, company, dashboard, e_invoice, issued_documents, received, reports  # noqa: E402,F401
