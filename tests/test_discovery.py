"""Tests for entry-point discovery and plugin contract validation."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from mcp_plugin_gateway import discovery
from mcp_plugin_gateway.discovery import (
    ENTRY_POINT_GROUP,
    DiscoveryResult,
    DuplicatePluginError,
    PluginContractError,
    PluginSpec,
    discover_plugins,
    discover_plugins_with_count,
    load_plugin,
)

from conftest import make_entry_point, make_entry_points


def patch_entry_points(monkeypatch: pytest.MonkeyPatch, *entry_points) -> None:
    """Replace ``importlib.metadata.entry_points`` with a fake container."""
    container = make_entry_points(*entry_points)
    monkeypatch.setattr(discovery.metadata, "entry_points", lambda: container)


def build_alpha() -> FastMCP:
    return FastMCP("alpha")


def test_discovery_returns_name_to_loader_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(
        monkeypatch,
        make_entry_point("alpha", build_alpha, distribution="alpha-dist"),
        make_entry_point("beta", build_alpha, distribution="beta-dist"),
    )

    specs = discover_plugins()

    assert sorted(specs) == ["alpha", "beta"]
    assert all(isinstance(spec, PluginSpec) for spec in specs.values())
    assert specs["alpha"].distribution == "alpha-dist"
    assert specs["beta"].distribution == "beta-dist"
    assert specs["alpha"].loader() is not None


def test_discovery_does_not_import_or_construct_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def build() -> FastMCP:
        calls.append("construct")
        return FastMCP("alpha")

    patch_entry_points(monkeypatch, make_entry_point("alpha", build, distribution="d"))
    discover_plugins()

    assert calls == []


def test_discovery_empty_group_returns_empty_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_entry_points(monkeypatch)
    assert discover_plugins() == {}


def test_discovery_ignores_unrelated_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(
        monkeypatch,
        make_entry_point("alpha", build_alpha, distribution="alpha-dist"),
        make_entry_point(
            "console", build_alpha, distribution="other-dist", group="console_scripts"
        ),
    )

    specs = discover_plugins()

    assert sorted(specs) == ["alpha"]


def test_count_query_reports_zero_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(monkeypatch)

    result = discover_plugins_with_count()

    assert isinstance(result, DiscoveryResult)
    assert result.plugins == {}
    assert result.count == 0
    assert result.names == []


def test_count_query_reports_one_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(
        monkeypatch, make_entry_point("alpha", build_alpha, distribution="alpha-dist")
    )

    result = discover_plugins_with_count()

    assert result.count == 1
    assert result.names == ["alpha"]
    assert result.plugins["alpha"].distribution == "alpha-dist"


def test_count_query_reports_three_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(
        monkeypatch,
        make_entry_point("alpha", build_alpha, distribution="alpha-dist"),
        make_entry_point("beta", build_alpha, distribution="beta-dist"),
        make_entry_point("gamma", build_alpha, distribution="gamma-dist"),
    )

    result = discover_plugins_with_count()

    assert result.count == 3
    assert result.names == ["alpha", "beta", "gamma"]


def test_count_query_reads_entry_points_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The count comes from the same read as the plugin list, not a second scan."""
    calls: list[str] = []
    container = make_entry_points(
        make_entry_point("alpha", build_alpha, distribution="alpha-dist")
    )

    def fake_entry_points():
        calls.append("called")
        return container

    monkeypatch.setattr(discovery.metadata, "entry_points", fake_entry_points)
    result = discover_plugins_with_count()

    assert calls == ["called"]
    assert result.count == 1


def test_discovery_uses_real_entry_point_group(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    container = make_entry_points(
        make_entry_point("alpha", build_alpha, distribution="alpha-dist")
    )

    def fake_entry_points():
        seen.append("called")
        return container

    monkeypatch.setattr(discovery.metadata, "entry_points", fake_entry_points)
    discover_plugins()

    assert seen == ["called"]
    assert ENTRY_POINT_GROUP == "mcp_plugin_gateway.plugins"


def test_duplicate_names_raise_naming_both_distributions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_entry_points(
        monkeypatch,
        make_entry_point("alpha", build_alpha, distribution="alpha-one"),
        make_entry_point("alpha", build_alpha, distribution="alpha-two"),
    )

    with pytest.raises(DuplicatePluginError) as excinfo:
        discover_plugins()

    message = str(excinfo.value)
    assert "alpha-one" in message
    assert "alpha-two" in message
    assert "'alpha'" in message


def test_load_plugin_returns_fastmcp(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(
        monkeypatch, make_entry_point("alpha", build_alpha, distribution="alpha-dist")
    )
    spec = discover_plugins()["alpha"]

    server = load_plugin(spec)

    assert isinstance(server, FastMCP)
    assert server.name == "alpha"


def test_contract_error_names_offending_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(
        monkeypatch,
        make_entry_point("alpha", lambda: "not a server", distribution="alpha-dist"),
    )
    spec = discover_plugins()["alpha"]

    with pytest.raises(PluginContractError) as excinfo:
        load_plugin(spec)

    message = str(excinfo.value)
    assert "'alpha'" in message
    assert "alpha-dist" in message
    assert "str" in message
    assert "FastMCP" in message


def test_contract_error_when_callable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> FastMCP:
        raise RuntimeError("kaboom")

    patch_entry_points(
        monkeypatch, make_entry_point("alpha", boom, distribution="alpha-dist")
    )
    spec = discover_plugins()["alpha"]

    with pytest.raises(PluginContractError) as excinfo:
        load_plugin(spec)

    message = str(excinfo.value)
    assert "'alpha'" in message
    assert "RuntimeError" in message
    assert "kaboom" in message


def test_contract_error_when_entry_point_not_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_entry_points(
        monkeypatch, make_entry_point("alpha", "module:attr", distribution="alpha-dist")
    )
    spec = discover_plugins()["alpha"]

    with pytest.raises(PluginContractError) as excinfo:
        load_plugin(spec)

    assert "'alpha'" in str(excinfo.value)
    assert "not callable" in str(excinfo.value)


def test_contract_error_when_import_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_entry_points(
        monkeypatch,
        make_entry_point(
            "alpha", ImportError("no such module"), distribution="alpha-dist"
        ),
    )
    spec = discover_plugins()["alpha"]

    with pytest.raises(PluginContractError) as excinfo:
        load_plugin(spec)

    message = str(excinfo.value)
    assert "'alpha'" in message
    assert "could not be imported" in message
