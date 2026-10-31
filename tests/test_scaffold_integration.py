"""Verify generated plugin projects satisfy real entry-point discovery."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from mcp_plugin_gateway.discovery import discover_plugins, load_plugin
from mcp_plugin_gateway.scaffold import scaffold_plugin


async def test_scaffolded_plugin_is_discovered_and_serves_its_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffold_plugin("demo-plugin", root=tmp_path)
    project_data = tomllib.loads((project / "pyproject.toml").read_text())
    metadata = project_data["project"]
    module, factory = project_data["project"]["entry-points"][
        "mcp_plugin_gateway.plugins"
    ]["demo-plugin"].split(":")
    source_root = project / "src"

    dist_info = tmp_path / f"{metadata['name']}-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        f"Name: {metadata['name']}\n"
        f"Version: {metadata['version']}\n"
    )
    (dist_info / "entry_points.txt").write_text(
        "[mcp_plugin_gateway.plugins]\n"
        f"demo-plugin = {module}:{factory}\n"
    )

    monkeypatch.syspath_prepend(str(source_root))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, module, raising=False)

    specs = discover_plugins()
    assert "demo-plugin" in specs
    server = load_plugin(specs["demo-plugin"])
    assert isinstance(server, FastMCP)

    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool("hello", {"name": "Ada"})

    assert {tool.name for tool in tools} == {"hello"}
    assert result.data == "Hello, Ada!"
