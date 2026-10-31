"""Verify the documented example plugin registers verbatim and honours the contract.

Builds a throwaway distribution on disk -- module plus ``.dist-info`` with an
``entry_points.txt`` -- puts it on ``sys.path``, and runs real discovery against
it. The module source comes from :data:`mcp_plugin_gateway.context.AUTHORING_EXAMPLE`,
the same constant the ``authoring`` document renders, so the document and the
contract can never describe different code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from mcp_plugin_gateway.context import (
    AUTHORING_EXAMPLE,
    authoring_entry_points,
)
from mcp_plugin_gateway.discovery import PluginContractError, discover_plugins, load_plugin


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> Path:
    """Put a plugin module and its distribution metadata on ``sys.path``."""
    package = tmp_path / "my_plugin"
    package.mkdir()
    (package / "__init__.py").write_text(source)

    dist_info = tmp_path / "my_plugin-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: my-plugin\nVersion: 0.1.0\n"
    )
    (dist_info / "entry_points.txt").write_text(authoring_entry_points())

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "my_plugin", raising=False)
    return tmp_path


@pytest.fixture
def installed_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install the documented example as a fake distribution on ``sys.path``."""
    yield _install(tmp_path, monkeypatch, AUTHORING_EXAMPLE)
    sys.modules.pop("my_plugin", None)


def test_documented_example_registers_verbatim(installed_example: Path) -> None:
    specs = discover_plugins()

    assert "my-plugin" in specs
    spec = specs["my-plugin"]
    assert spec.distribution == "my-plugin"
    assert "my_plugin" not in sys.modules, "discovery must not import the plugin"


def test_documented_example_satisfies_the_contract(installed_example: Path) -> None:
    """The documented example must still yield a bare ``FastMCP`` server.

    If the example were edited to return anything else, ``load_plugin`` raises
    ``PluginContractError`` and this test fails.
    """
    spec = discover_plugins()["my-plugin"]

    server = load_plugin(spec)

    assert isinstance(server, FastMCP)


def test_contract_rejects_a_non_fastmcp_return_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin that returns something other than ``FastMCP`` is rejected."""
    _install(tmp_path, monkeypatch, "def build_server():\n    return {'not': 'a server'}\n")
    try:
        spec = discover_plugins()["my-plugin"]

        with pytest.raises(PluginContractError) as excinfo:
            load_plugin(spec)

        assert "my-plugin" in str(excinfo.value)
    finally:
        sys.modules.pop("my_plugin", None)


async def test_documented_example_serves_its_tool(installed_example: Path) -> None:
    spec = discover_plugins()["my-plugin"]

    server = load_plugin(spec)

    assert isinstance(server, FastMCP)
    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}
        result = await client.call_tool("add", {"a": 1, "b": 2})

    assert names == {"add"}
    assert result.data == 3
