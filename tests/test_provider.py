"""Tests for the lazy plugin provider."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastmcp import Client, FastMCP

from mcp_plugin_gateway.provider import LazyPluginProvider

from conftest import PluginHarness


def test_loader_not_called_at_registration(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)
    server = FastMCP("aggregate")
    server.add_provider(provider, namespace="alpha")

    assert harness.events == []
    assert provider.constructed is False
    assert "lazy" in repr(provider)


async def test_loader_called_exactly_once_on_first_access(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)
    server = FastMCP("aggregate")
    server.add_provider(provider, namespace="alpha")

    async with Client(server) as client:
        await client.list_tools()
        await client.list_tools()
        await client.list_prompts()
        await client.list_resources()

    assert harness.events.count("construct") == 1
    assert provider.constructed is True


async def test_prompts_and_resources_forwarded(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)
    server = FastMCP("aggregate")
    server.add_provider(provider, namespace="alpha")

    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
        prompts = {p.name for p in await client.list_prompts()}
        resources = {str(r.uri) for r in await client.list_resources()}

    assert "alpha_ping" in tools
    assert "alpha_greet" in prompts
    assert "data://alpha/alpha/value" in resources


async def test_tool_call_reaches_plugin(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)
    server = FastMCP("aggregate")
    server.add_provider(provider, namespace="alpha")

    async with Client(server) as client:
        result = await client.call_tool("alpha_ping", {"x": 7})

    assert result.data == "pong 7"


async def test_lifespan_startup_and_teardown(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)
    server = FastMCP("aggregate")
    server.add_provider(provider, namespace="alpha")

    async with Client(server) as client:
        await client.list_tools()
        assert "startup" in harness.events

    assert harness.events == ["construct", "startup", "teardown"]


def test_required_extensions_empty_before_construction(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)

    assert provider.required_extensions() == ()
    assert harness.events == []


async def test_required_extensions_forwarded_after_construction(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)
    server = FastMCP("aggregate")
    server.add_provider(provider, namespace="alpha")

    async with Client(server) as client:
        await client.list_tools()

    assert provider.constructed is True
    assert list(provider.required_extensions()) == []


async def test_get_tool_by_name(
    harness: PluginHarness, loader: Callable[[], FastMCP]
) -> None:
    provider = LazyPluginProvider("alpha", loader)

    tool = await provider.get_tool("ping")

    assert tool is not None
    assert tool.name == "ping"
    assert harness.events.count("construct") == 1
