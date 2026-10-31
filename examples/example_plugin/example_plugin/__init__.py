"""Example plugin exercising all three surface-tag cases.

Install this distribution to see one untagged tool on both surfaces, a
``cli``-tagged tool only on the command line, and an ``mcp``-tagged tool only
for MCP clients.
"""

from __future__ import annotations

from enum import Enum

from fastmcp import FastMCP


class Unit(str, Enum):
    """Temperature unit."""

    CELSIUS = "celsius"
    FAHRENHEIT = "fahrenheit"


def make_plugin() -> FastMCP:
    """Return the example plugin's server."""
    server = FastMCP("example")

    @server.tool
    def greet(name: str, greeting: str = "Hello") -> str:
        """Greet someone. Available on both surfaces."""
        return f"{greeting}, {name}!"

    @server.tool
    def convert(value: float, unit: Unit = Unit.CELSIUS) -> str:
        """Convert a temperature. Available on both surfaces."""
        if unit is Unit.CELSIUS:
            return f"{value * 9 / 5 + 32:.1f} fahrenheit"
        return f"{(value - 32) * 5 / 9:.1f} celsius"

    @server.tool(tags={"cli"})
    def local_status() -> str:
        """Report local status. CLI only."""
        return "local status: ok"

    @server.tool(tags={"mcp"})
    def remote_status() -> str:
        """Report remote status. MCP only."""
        return "remote status: ok"

    return server
