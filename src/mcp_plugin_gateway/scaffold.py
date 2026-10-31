"""Generate a standalone plugin distribution beneath a project root."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from mcp_plugin_gateway.discovery import ENTRY_POINT_GROUP

_DISTRIBUTION_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\Z")


def _render_files(distribution_name: str) -> dict[Path, str]:
    """Render the metadata, starter server, and instructions for a plugin."""
    module_name = distribution_name.replace("-", "_")
    pyproject = f'''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{distribution_name}"
version = "0.1.0"
description = "A plugin for mcp-plugin-gateway."
requires-python = ">=3.12"
dependencies = ["mcp-plugin-gateway", "fastmcp>=4.1.4"]

[project.entry-points."{ENTRY_POINT_GROUP}"]
{distribution_name} = "{module_name}:build_server"

[tool.hatch.build.targets.wheel]
packages = ["src/{module_name}"]
'''
    module = f'''"""Starter plugin server for {distribution_name}."""

from fastmcp import FastMCP


def build_server() -> FastMCP:
    """Create the plugin's FastMCP server."""
    server = FastMCP("{distribution_name}")

    @server.tool
    def hello(name: str) -> str:
        """Return a friendly greeting."""
        return f"Hello, {{name}}!"

    return server
'''
    readme = f"""# {distribution_name}

A standalone plugin for `mcp-plugin-gateway`.

## Install

From this directory, install the distribution into the same Python environment
that runs the `mcp-plugin-gateway` command:

```console
python -m pip install -e .
```

## Verify discovery

List the installed plugins and their tools:

```console
mcp-plugin-gateway llm
```

The `{distribution_name}` plugin should list its `hello` tool.

## Use the CLI

Call the starter tool with:

```console
mcp-plugin-gateway {distribution_name} hello Ada
```

## Use over MCP

Start the MCP stdio server with:

```console
mcp-plugin-gateway mcp
```

Configure your MCP client to launch that command using its stdio transport.
"""
    return {
        Path("pyproject.toml"): pyproject,
        Path("README.md"): readme,
        Path("src") / module_name / "__init__.py": module,
    }


def scaffold_plugin(distribution_name: str, *, root: Path | None = None) -> Path:
    """Create a standalone plugin project under ``root/plugins``.

    Names use a restricted, path-safe distribution-name grammar and must also
    map directly to a valid Python package identifier after hyphen conversion.
    A failed write is reported as an ``OSError`` and the newly created project
    directory is removed when possible.
    """
    if not isinstance(distribution_name, str) or not _DISTRIBUTION_NAME.fullmatch(
        distribution_name
    ):
        raise ValueError(
            "Invalid distribution name. Use a letter followed by letters or "
            "digits, with optional single hyphen-separated alphanumeric segments."
        )

    project_root = (Path.cwd() if root is None else Path(root)).resolve()
    plugins_dir = project_root / "plugins"
    target = plugins_dir / distribution_name

    try:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        if not plugins_dir.resolve().is_relative_to(project_root):
            raise OSError(
                f"Plugin directory resolves outside project root: {plugins_dir}"
            )
        target.mkdir()
    except FileExistsError as exc:
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Plugin project already exists: {target}") from None
        raise OSError(f"Cannot create plugin project under {plugins_dir}: {exc}") from exc
    except OSError as exc:
        raise OSError(
            f"Could not create plugin project under {plugins_dir}: {exc}"
        ) from exc

    try:
        for relative_path, content in _render_files(distribution_name).items():
            path = target / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    except OSError as exc:
        try:
            shutil.rmtree(target)
        except OSError:
            pass
        raise OSError(f"Could not write plugin project at {target}: {exc}") from exc

    return target
