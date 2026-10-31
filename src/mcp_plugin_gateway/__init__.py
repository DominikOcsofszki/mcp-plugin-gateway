"""A plugin system where each plugin is a bare FastMCP server.

Plugins are discovered through the ``mcp_plugin_gateway.plugins`` entry-point group
and exposed on two surfaces generated from one definition: an MCP server and a
command-line interface derived from each tool's input schema.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
