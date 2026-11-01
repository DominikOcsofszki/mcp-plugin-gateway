"""Tests for safely integrating an existing Python project as an MCP plugin."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pytest

from mcp_plugin_gateway.project_init import (
    apply_project_init_plan,
    build_project_init_plan,
)


def _project(root: Path, packages: tuple[str, ...] = ("sample_project",)) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\n'
        'name = "sample-project"\n'
        'version = "1.2.3"\n'
        'dependencies = ["requests>=2"]\n\n'
        '[build-system]\n'
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n',
        encoding="utf-8",
    )
    for package in packages:
        package_dir = root / "src" / package
        package_dir.mkdir(parents=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    return root


def test_plan_adds_dependencies_entry_point_and_starter_module(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)

    plan = build_project_init_plan(root)
    metadata = tomllib.loads(plan.updated_pyproject)

    assert plan.plugin_name == "sample-project"
    assert plan.package_name == "sample_project"
    assert plan.module_path == root / "src" / "sample_project" / "mcp_server.py"
    assert metadata["project"]["dependencies"] == [
        "requests>=2",
        "mcp-plugin-gateway",
        "fastmcp>=4.1.4",
    ]
    assert metadata["project"]["entry-points"]["mcp_plugin_gateway.plugins"] == {
        "sample-project": "sample_project.mcp_server:build_server"
    }
    assert "def build_server() -> FastMCP:" in plan.module_content
    assert "return server" in plan.module_content
    assert not plan.module_path.exists()


def test_plan_does_not_duplicate_existing_dependencies_or_entry_points(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        .replace(
            'dependencies = ["requests>=2"]',
            'dependencies = ["requests>=2", "MCP_plugin_gateway", "fastmcp>=4"]',
        )
        .replace(
            '[build-system]',
            '[project.entry-points."mcp_plugin_gateway.plugins"]\n'
            'other = "other_plugin:build_server"\n\n[build-system]',
        ),
        encoding="utf-8",
    )

    plan = build_project_init_plan(root)
    metadata = tomllib.loads(plan.updated_pyproject)

    assert metadata["project"]["dependencies"] == [
        "requests>=2",
        "MCP_plugin_gateway",
        "fastmcp>=4",
    ]
    assert metadata["project"]["entry-points"]["mcp_plugin_gateway.plugins"] == {
        "other": "other_plugin:build_server",
        "sample-project": "sample_project.mcp_server:build_server",
    }


def test_plan_rejects_missing_or_ambiguous_projects_without_writing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(ValueError, match="pyproject.toml"):
        build_project_init_plan(missing)
    assert list(missing.iterdir()) == []

    ambiguous = _project(tmp_path / "ambiguous", ("one", "two"))
    with pytest.raises(ValueError, match="exactly one top-level Python package"):
        build_project_init_plan(ambiguous)
    assert not (ambiguous / "pyproject.toml").read_text().endswith("entry-points")


def test_plan_rejects_conflicting_entry_point_and_existing_module(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        .replace(
            "[build-system]",
            '[project.entry-points."mcp_plugin_gateway.plugins"]\n'
            'sample-project = "old_module:build_server"\n\n[build-system]',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="entry point .* already exists"):
        build_project_init_plan(root)

    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            '[project.entry-points."mcp_plugin_gateway.plugins"]\n'
            'sample-project = "old_module:build_server"\n\n',
            "",
        ),
        encoding="utf-8",
    )
    existing_module = root / "src" / "sample_project" / "mcp_server.py"
    existing_module.write_text("# user-owned\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        build_project_init_plan(root)
    assert existing_module.read_text(encoding="utf-8") == "# user-owned\n"


def test_apply_writes_project_metadata_and_server_module(tmp_path: Path) -> None:
    root = _project(tmp_path)
    original_mode = (root / "pyproject.toml").stat().st_mode & 0o777
    plan = build_project_init_plan(root)

    changed = apply_project_init_plan(plan)

    assert changed == [root / "pyproject.toml", plan.module_path]
    assert tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "entry-points"
    ]["mcp_plugin_gateway.plugins"]["sample-project"]
    assert plan.module_path.is_file()
    assert (root / "pyproject.toml").stat().st_mode & 0o777 == original_mode


def test_apply_cleans_up_new_module_if_metadata_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    original = (root / "pyproject.toml").read_text(encoding="utf-8")
    plan = build_project_init_plan(root)

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr("mcp_plugin_gateway.project_init.os.replace", fail_replace)

    with pytest.raises(OSError, match="Could not apply MCP project changes"):
        apply_project_init_plan(plan)

    assert (root / "pyproject.toml").read_text(encoding="utf-8") == original
    assert not plan.module_path.exists()
    assert list(root.glob(".pyproject.toml.*")) == []


async def test_generated_server_module_imports_and_builds_fastmcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    plan = build_project_init_plan(root)
    apply_project_init_plan(plan)
    monkeypatch.syspath_prepend(str(root / "src"))
    monkeypatch.delitem(sys.modules, "sample_project.mcp_server", raising=False)

    from sample_project.mcp_server import build_server

    server = build_server()

    assert server.name == "sample-project"
    assert [tool.name for tool in await server.list_tools()] == ["hello"]
