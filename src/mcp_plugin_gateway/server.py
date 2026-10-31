"""Aggregate and single-plugin MCP servers built from registered plugins.

Surface control is tag based. A tool tagged ``cli`` is hidden from MCP clients
through a :class:`fastmcp.server.transforms.Visibility` transform; a tool tagged
``mcp`` is hidden from the CLI by the generator, which reads the raw provider
list. An untagged tool appears on both surfaces. Unknown tags fail loudly at
registration so a typo cannot silently change visibility.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from fastmcp import FastMCP
from fastmcp.server.transforms import Visibility

from mcp_plugin_gateway.provider import LazyPluginProvider

if TYPE_CHECKING:
    from fastmcp.tools.base import Tool

    from mcp_plugin_gateway.registry import PluginRegistry

CLI_TAG = "cli"
"""Tag that hides a tool from MCP clients while keeping it on the CLI."""

MCP_TAG = "mcp"
"""Tag that hides a tool from the CLI while keeping it on MCP clients."""

SURFACE_TAGS = frozenset({CLI_TAG, MCP_TAG})
"""Every tag the system understands. Anything else is a typo."""


class UnknownSurfaceTagError(ValueError):
    """A tool carries a tag the system does not understand."""

    def __init__(self, plugin: str, tool: str, tags: Iterable[str]) -> None:
        self.plugin = plugin
        self.tool = tool
        self.tags = frozenset(tags)
        known = ", ".join(sorted(SURFACE_TAGS))
        unknown = ", ".join(sorted(self.tags))
        super().__init__(
            f"Plugin {plugin!r} tool {tool!r} carries unknown surface tag(s) "
            f"{unknown}. Known tags are: {known}."
        )


def validate_surface_tags(plugin: str, tool: "Tool") -> None:
    """Reject a tool whose tags include anything outside :data:`SURFACE_TAGS`."""
    tags = set(tool.tags or ())
    unknown = tags - SURFACE_TAGS
    if unknown:
        raise UnknownSurfaceTagError(plugin, tool.name, unknown)


def validate_plugin_tags(plugin: str, tools: Sequence["Tool"]) -> None:
    """Validate every tool's surface tags for one plugin."""
    for tool in tools:
        validate_surface_tags(plugin, tool)


def is_cli_visible(tool: "Tool") -> bool:
    """Whether the CLI should offer a command for ``tool``."""
    return MCP_TAG not in set(tool.tags or ())


def is_mcp_visible(tool: "Tool") -> bool:
    """Whether MCP clients should see ``tool``."""
    return CLI_TAG not in set(tool.tags or ())


async def iter_all_tools(server: FastMCP) -> list["Tool"]:
    """Return every tool the server's providers expose, with tags intact.

    The server-level ``Visibility`` transform hides ``cli``-tagged tools from MCP
    clients, so ``server.list_tools()`` cannot see them. Reading each provider's
    own listing keeps every tool visible and keeps its tags, which is what the
    usage document needs in order to say which surface each tool is reachable
    from.

    Unlike ``server.list_tools()``, a provider that fails to construct raises
    here instead of being logged and skipped, so the caller can report the
    failure against the plugin that caused it.
    """
    tools: list[Tool] = []
    for provider in server.providers:
        tools.extend(await provider.list_tools())
    return tools


async def iter_cli_tools(server: FastMCP) -> list["Tool"]:
    """Return the tools the CLI should offer, read from raw providers.

    The server-level ``Visibility`` transform hides ``cli``-tagged tools from MCP
    clients, so ``server.list_tools()`` cannot see them. The CLI reads each
    provider's own listing instead, which keeps namespacing and tags intact.
    """
    return [tool for tool in await iter_all_tools(server) if is_cli_visible(tool)]


def _hide_cli_tagged(server: FastMCP) -> None:
    """Hide ``cli``-tagged tools from MCP clients."""
    server.add_transform(Visibility(False, tags={CLI_TAG}))


def build_aggregate_server(
    registry: "PluginRegistry", skip: Iterable[str] = ()
) -> FastMCP:
    """Return a server mounting every plugin under its own namespace."""
    skipped = set(skip)
    server = FastMCP("mcp_plugin_gateway")
    _hide_cli_tagged(server)
    for spec in registry:
        if spec.name in skipped:
            continue
        server.add_provider(
            LazyPluginProvider(spec.name, spec.loader), namespace=spec.name
        )
    return server


def build_plugin_server(
    registry: "PluginRegistry", name: str, *, hide_cli_tools: bool = True
) -> FastMCP:
    """Return a server exposing one plugin's components without a namespace.

    ``hide_cli_tools`` applies the MCP-side ``Visibility`` transform. The CLI
    generator passes ``False`` so its in-memory client can call ``cli``-tagged
    tools, which that transform would otherwise hide from the client.
    """
    spec = registry.get(name)
    server = FastMCP(name)
    if hide_cli_tools:
        _hide_cli_tagged(server)
    server.add_provider(LazyPluginProvider(spec.name, spec.loader))
    return server


def build_default_server(
    registry: "PluginRegistry", skip: Iterable[str] = ()
) -> FastMCP:
    """Return the server the ``mcp`` subcommand serves when no plugin is named.

    A lone plugin is served without a namespace so a standalone distribution
    exposes plain tool names. Two or more plugins keep the aggregate server's
    namespaced names. ``skip`` only applies to the aggregate server, because a
    lone plugin cannot be both served and skipped.
    """
    if len(registry) == 1:
        return build_plugin_server(registry, registry.names()[0])
    return build_aggregate_server(registry, skip=skip)


def serve_stdio(server: FastMCP) -> None:
    """Serve ``server`` over stdio."""
    server.run(transport="stdio")
