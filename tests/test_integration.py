"""Integration tests: real MCP clients against generated servers."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from mcp_plugin_gateway.discovery import PluginSpec
from mcp_plugin_gateway.registry import PluginRegistry
from mcp_plugin_gateway.server import build_aggregate_server, build_plugin_server


def _plugin(name: str, *, broken: bool = False) -> "FastMCP":
    if broken:
        raise ValueError(f"{name.upper()}_TOKEN is required")

    server = FastMCP(name)

    @server.tool
    def ping() -> str:
        """Ping."""
        return f"{name}-pong"

    @server.tool(tags={"mcp"})
    def remote_only() -> str:
        """MCP only."""
        return "mcp"

    @server.tool(tags={"cli"})
    def local_only() -> str:
        """CLI only."""
        return "cli"

    return server


def _registry(*specs: tuple[str, bool]) -> PluginRegistry:
    return PluginRegistry(
        [
            PluginSpec(name, (lambda n=name, b=broken: _plugin(n, broken=b)), "demo")
            for name, broken in specs
        ]
    )


# --- 6.2 aggregate server over stdio -------------------------------------


async def test_aggregate_server_lists_expected_tools() -> None:
    registry = _registry(("alpha", False), ("beta", False))
    server = build_aggregate_server(registry)

    async with Client(server) as client:
        names = sorted(t.name for t in await client.list_tools())

    assert names == [
        "alpha_ping",
        "alpha_remote_only",
        "beta_ping",
        "beta_remote_only",
    ]


async def test_aggregate_server_calls_a_tool() -> None:
    server = build_aggregate_server(_registry(("alpha", False)))

    async with Client(server) as client:
        result = await client.call_tool("alpha_ping", {})

    assert "alpha-pong" in json.dumps(result.structured_content)


# --- 6.3 --skip-plugin ---------------------------------------------------


async def test_skip_plugin_lets_client_connect_with_broken_plugin() -> None:
    registry = _registry(("alpha", False), ("broken", True))
    server = build_aggregate_server(registry, skip=["broken"])

    async with Client(server) as client:
        names = sorted(t.name for t in await client.list_tools())

    assert names == ["alpha_ping", "alpha_remote_only"]


async def test_broken_plugin_without_skip_fails_connection() -> None:
    registry = _registry(("alpha", False), ("broken", True))
    server = build_aggregate_server(registry)

    with pytest.raises(Exception) as excinfo:
        async with Client(server) as client:
            await client.list_tools()

    assert "BROKEN_TOKEN" in str(excinfo.value)


# --- 6.5 per-plugin serving ---------------------------------------------


async def test_single_plugin_server_sees_only_its_tools() -> None:
    registry = _registry(("alpha", False), ("beta", False))
    server = build_plugin_server(registry, "alpha")

    async with Client(server) as client:
        names = sorted(t.name for t in await client.list_tools())

    # The MCP surface hides ``cli``-tagged tools, so ``local_only`` is absent.
    assert names == ["ping", "remote_only"]


async def test_single_plugin_server_calls_unprefixed_tool() -> None:
    registry = _registry(("alpha", False))
    server = build_plugin_server(registry, "alpha")

    async with Client(server) as client:
        result = await client.call_tool("ping", {})

    assert "alpha-pong" in json.dumps(result.structured_content)


# --- 6.1 console script --------------------------------------------------


def _run_cli(
    *args: str, cwd: Path | None = None, pythonpath: Path | None = None
) -> "subprocess.CompletedProcess[str]":
    import os
    import subprocess

    env = dict(os.environ)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    return subprocess.run(
        [sys.executable, "-m", "mcp_plugin_gateway.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=60,
    )


def test_console_script_lists_installed_plugins(tmp_path: Path) -> None:
    """``--help`` lists plugins discovered from installed entry points."""
    _install_fake_plugin(tmp_path, "demo")

    result = _run_cli("--help", cwd=tmp_path, pythonpath=tmp_path)

    assert result.returncode == 0
    assert "demo" in result.stdout


def _install_fake_plugin(root: Path, name: str) -> None:
    """Write a throwaway plugin distribution onto ``root``."""
    module = root / f"{name}_plugin"
    module.mkdir(parents=True, exist_ok=True)
    (module / "__init__.py").write_text(
        "from fastmcp import FastMCP\n"
        "\n"
        "def make_plugin() -> FastMCP:\n"
        f"    server = FastMCP({name!r})\n"
        "\n"
        "    @server.tool\n"
        "    def ping() -> str:\n"
        "        \"\"\"Ping.\"\"\"\n"
        "        return 'pong'\n"
        "\n"
        "    return server\n"
    )
    dist = root / f"{name}_plugin-0.1.0.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        f"Name: {name}-plugin\n"
        "Version: 0.1.0\n"
    )
    (dist / "entry_points.txt").write_text(
        "[mcp_plugin_gateway.plugins]\n"
        f"{name} = {name}_plugin:make_plugin\n"
    )


# --- 4.2 standalone plugin distribution ----------------------------------


def _core_project_root() -> Path:
    """Return the directory holding the core distribution's ``pyproject.toml``."""
    return Path(__file__).resolve().parents[1]


def _write_standalone_distribution(root: Path, name: str) -> Path:
    """Write a plugin distribution that depends on ``mcp-plugin-gateway``."""
    module = f"{name.replace('-', '_')}_plugin"
    package = root / module
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        "from fastmcp import FastMCP\n"
        "\n"
        "def build_server() -> FastMCP:\n"
        f"    server = FastMCP({name!r})\n"
        "\n"
        "    @server.tool\n"
        "    def add(a: int, b: int) -> int:\n"
        "        \"\"\"Add two integers.\"\"\"\n"
        "        return a + b\n"
        "\n"
        "    return server\n"
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        'dependencies = ["mcp-plugin-gateway", "fastmcp"]\n'
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[tool.hatch.build.targets.wheel]\n"
        f'packages = ["{module}"]\n'
        "\n"
        '[project.entry-points."mcp_plugin_gateway.plugins"]\n'
        f'{name} = "{module}:build_server"\n'
    )
    return root


def _uv(*args: str) -> "subprocess.CompletedProcess[str]":
    """Run ``uv`` offline so the test never needs network access."""
    import subprocess

    return subprocess.run(
        ["uv", *args], capture_output=True, text=True, timeout=600
    )


def _run_console_script(
    python: Path, *args: str
) -> "subprocess.CompletedProcess[str]":
    """Run an installed CLI script next to ``python``."""
    import subprocess

    script = python.parent / "mcp-plugin-gateway"
    return subprocess.run(
        [str(script), *args], capture_output=True, text=True, timeout=120
    )


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv is required to build offline"
)
def test_standalone_distribution_serves_both_surfaces(tmp_path: Path) -> None:
    """A plugin installed alone in a fresh venv needs no host project."""
    import subprocess

    venv = tmp_path / "venv"
    created = _uv("venv", "--python", sys.executable, str(venv))
    assert created.returncode == 0, created.stderr
    python = venv / "bin" / "python"

    core = _uv("pip", "install", "--offline", "--python", str(python), str(_core_project_root()))
    assert core.returncode == 0, core.stderr
    plugin = _uv(
        "pip",
        "install",
        "--offline",
        "--python",
        str(python),
        str(_write_standalone_distribution(tmp_path / "dist", "solo")),
    )
    assert plugin.returncode == 0, plugin.stderr

    # CLI surface: the plugin name is optional because it is the only plugin.
    unnamed = _run_console_script(python, "add", "1", "2")
    named = _run_console_script(python, "solo", "add", "1", "2")
    assert unnamed.returncode == 0, unnamed.stderr
    assert named.returncode == 0, named.stderr
    assert unnamed.stdout.strip() == named.stdout.strip() == "3"
    assert (python.parent / "mcp-plugin-gateway").is_file()
    assert not (python.parent / "py-plugin-system").exists()

    # MCP surface: the default server serves the lone plugin unqualified.
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import asyncio\n"
        "from fastmcp import Client\n"
        "from mcp_plugin_gateway.registry import build_registry\n"
        "from mcp_plugin_gateway.server import build_default_server\n"
        "\n"
        "async def main() -> None:\n"
        "    server = build_default_server(build_registry())\n"
        "    async with Client(server) as client:\n"
        "        names = sorted(t.name for t in await client.list_tools())\n"
        "        result = await client.call_tool('add', {'a': 1, 'b': 2})\n"
        "    print(names)\n"
        "    print(result.structured_content)\n"
        "\n"
        "asyncio.run(main())\n"
    )
    served = subprocess.run(
        [str(python), str(probe)], capture_output=True, text=True, timeout=120
    )
    assert served.returncode == 0, served.stderr
    assert "['add']" in served.stdout
    assert "'result': 3" in served.stdout
