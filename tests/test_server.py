"""Tests for aggregate/single-plugin servers and surface-tag filtering."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastmcp import Client, FastMCP

from mcp_plugin_gateway.discovery import PluginSpec
from mcp_plugin_gateway.registry import PluginRegistry
from mcp_plugin_gateway.server import (
    CLI_TAG,
    MCP_TAG,
    UnknownSurfaceTagError,
    build_aggregate_server,
    build_default_server,
    build_plugin_server,
    is_cli_visible,
    iter_cli_tools,
    is_mcp_visible,
    validate_plugin_tags,
)

from conftest import PluginHarness


def registry_for(harness: PluginHarness, name: str = "alpha") -> PluginRegistry:
    return PluginRegistry(
        [PluginSpec(name=name, loader=harness.build, distribution=f"{name}-dist")]
    )


async def test_mcp_lists_both_and_mcp_tools(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    server = build_aggregate_server(registry_for(harness))

    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}

    assert "alpha_ping" in names
    assert "alpha_remote" in names
    assert "alpha_secret" not in names


async def test_cli_lists_both_and_cli_tools(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    server = build_aggregate_server(registry_for(harness))

    async with Client(server) as client:
        mcp_names = {t.name for t in await client.list_tools()}

    # The CLI reads raw provider components, so it sees cli-tagged tools too.
    cli_names = {t.name for t in await iter_cli_tools(server)}

    assert "alpha_ping" in cli_names
    assert "alpha_secret" in cli_names
    assert "alpha_remote" not in cli_names
    assert "alpha_ping" in mcp_names
    assert "alpha_remote" in mcp_names
    assert "alpha_secret" not in mcp_names


async def test_unknown_tag_raises_naming_plugin_and_tool() -> None:
    server = FastMCP("alpha")

    @server.tool(tags={"clii"})
    def typo() -> str:
        return "x"

    tools = await server._local_provider.list_tools()

    with pytest.raises(UnknownSurfaceTagError) as excinfo:
        validate_plugin_tags("alpha", tools)

    message = str(excinfo.value)
    assert "'alpha'" in message
    assert "'typo'" in message
    assert "clii" in message


async def test_known_tags_validate_cleanly(harness: PluginHarness) -> None:
    server = harness.build()
    validate_plugin_tags("alpha", await server._local_provider.list_tools())


async def test_aggregate_namespaces_tool_names(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    server = build_aggregate_server(registry_for(harness))

    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}

    assert all(name.startswith("alpha_") for name in names)


async def test_single_plugin_server_uses_plain_names(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    registry = registry_for(harness)
    aggregate = build_aggregate_server(registry)
    single = build_plugin_server(registry, "alpha")

    async with Client(aggregate) as agg_client:
        agg_names = {t.name for t in await agg_client.list_tools()}
    async with Client(single) as single_client:
        single_names = {t.name for t in await single_client.list_tools()}

    # ``secret`` is cli-tagged, so neither MCP surface lists it.
    assert single_names == {"ping", "remote"}
    assert agg_names == {f"alpha_{name}" for name in single_names}


async def test_aggregate_forwards_prompt_and_resource(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    server = build_aggregate_server(registry_for(harness))

    async with Client(server) as client:
        prompts = {p.name for p in await client.list_prompts()}
        resources = {str(r.uri) for r in await client.list_resources()}

    assert "alpha_greet" in prompts
    assert "data://alpha/alpha/value" in resources


async def test_skip_excludes_plugin(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    server = build_aggregate_server(registry_for(harness), skip={"alpha"})

    async with Client(server) as client:
        assert await client.list_tools() == []


def test_surface_tag_constants() -> None:
    assert CLI_TAG == "cli"
    assert MCP_TAG == "mcp"


# --- 3.1-3.3 default MCP selection ---------------------------------------


def _two_plugin_registry() -> PluginRegistry:
    """Return a registry with two plugins, each exposing a ``ping`` tool."""
    from fastmcp import FastMCP

    def plugin(name: str) -> FastMCP:
        server = FastMCP(name)

        @server.tool
        def ping() -> str:
            return f"{name} pong"

        return server

    return PluginRegistry(
        [
            PluginSpec(name="alpha", loader=lambda: plugin("alpha"), distribution="a"),
            PluginSpec(name="beta", loader=lambda: plugin("beta"), distribution="b"),
        ]
    )


async def test_default_server_serves_lone_plugin_unqualified(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    """One installed plugin means ``mcp`` serves plain, unprefixed tool names."""
    server = build_default_server(registry_for(harness))

    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}

    assert names == {"ping", "remote"}
    assert not any(name.startswith("alpha_") for name in names)


async def test_default_server_serves_aggregate_for_multiple_plugins() -> None:
    """Two installed plugins keep the aggregate server's namespaced names."""
    server = build_default_server(_two_plugin_registry())

    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}

    assert names == {"alpha_ping", "beta_ping"}


async def test_explicit_plugin_serves_unqualified_in_both_environments(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    """Naming a plugin always serves only its tools without a prefix."""
    single = build_plugin_server(registry_for(harness), "alpha")
    multi = build_plugin_server(_two_plugin_registry(), "beta")

    async with Client(single) as single_client:
        single_names = {t.name for t in await single_client.list_tools()}
    async with Client(multi) as multi_client:
        multi_names = {t.name for t in await multi_client.list_tools()}

    assert single_names == {"ping", "remote"}
    assert multi_names == {"ping"}
