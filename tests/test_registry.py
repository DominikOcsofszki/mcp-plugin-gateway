"""Tests for the plugin registry."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from mcp_plugin_gateway.discovery import DuplicatePluginError, PluginSpec
from mcp_plugin_gateway.registry import (
    AmbiguousPluginError,
    NoPluginsError,
    PluginRegistry,
    build_registry,
)


def build_alpha() -> FastMCP:
    return FastMCP("alpha")


def spec(name: str, distribution: str) -> PluginSpec:
    return PluginSpec(name=name, loader=build_alpha, distribution=distribution)


def test_register_and_lookup() -> None:
    registry = PluginRegistry([spec("alpha", "alpha-dist")])

    assert registry.names() == ["alpha"]
    assert "alpha" in registry
    assert len(registry) == 1
    assert registry.get("alpha").distribution == "alpha-dist"


def test_duplicate_registration_names_both_distributions() -> None:
    registry = PluginRegistry([spec("alpha", "alpha-one")])

    with pytest.raises(DuplicatePluginError) as excinfo:
        registry.register(spec("alpha", "alpha-two"))

    message = str(excinfo.value)
    assert "alpha-one" in message
    assert "alpha-two" in message
    assert "'alpha'" in message


def test_unknown_plugin_raises_key_error_listing_known() -> None:
    registry = PluginRegistry([spec("alpha", "alpha-dist")])

    with pytest.raises(KeyError) as excinfo:
        registry.get("missing")

    message = str(excinfo.value)
    assert "missing" in message
    assert "alpha" in message


def test_build_constructs_plugin_server() -> None:
    registry = PluginRegistry([spec("alpha", "alpha-dist")])

    server = registry.build("alpha")

    assert isinstance(server, FastMCP)
    assert server.name == "alpha"


def test_iteration_is_sorted_by_name() -> None:
    registry = PluginRegistry(
        [spec("zeta", "z-dist"), spec("alpha", "a-dist"), spec("mid", "m-dist")]
    )

    assert [s.name for s in registry] == ["alpha", "mid", "zeta"]


def test_resolve_returns_sole_plugin_when_name_omitted() -> None:
    registry = PluginRegistry([spec("alpha", "alpha-dist")])

    resolved = registry.resolve()

    assert resolved.name == "alpha"
    assert resolved.distribution == "alpha-dist"


def test_resolve_returns_named_plugin_in_multi_plugin_registry() -> None:
    registry = PluginRegistry([spec("alpha", "a-dist"), spec("beta", "b-dist")])

    assert registry.resolve("beta").name == "beta"


def test_resolve_raises_ambiguity_listing_every_plugin_name() -> None:
    registry = PluginRegistry(
        [spec("alpha", "a-dist"), spec("beta", "b-dist"), spec("gamma", "g-dist")]
    )

    with pytest.raises(AmbiguousPluginError) as excinfo:
        registry.resolve()

    message = str(excinfo.value)
    assert "alpha" in message
    assert "beta" in message
    assert "gamma" in message
    assert "Ambiguous" in message


def test_resolve_raises_when_no_plugin_is_installed() -> None:
    with pytest.raises(NoPluginsError) as excinfo:
        PluginRegistry().resolve()

    message = str(excinfo.value)
    assert "No plugins" in message
    assert "mcp_plugin_gateway.plugins" in message


def test_build_registry_uses_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_plugin_gateway import registry as registry_module

    monkeypatch.setattr(
        registry_module, "discover_plugins", lambda: {"alpha": spec("alpha", "a-dist")}
    )

    registry = build_registry()

    assert registry.names() == ["alpha"]
