"""Tests for generating standalone plugin distributions."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_plugin_gateway.discovery import ENTRY_POINT_GROUP
from mcp_plugin_gateway.scaffold import scaffold_plugin


def test_scaffold_generates_plugin_project(tmp_path: Path) -> None:
    project = scaffold_plugin("sample-plugin", root=tmp_path)
    module = project / "src" / "sample_plugin" / "__init__.py"

    assert project == tmp_path / "plugins" / "sample-plugin"
    assert (project / "pyproject.toml").is_file()
    assert (project / "README.md").is_file()
    assert module.is_file()

    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "sample-plugin"' in pyproject
    assert f'[project.entry-points."{ENTRY_POINT_GROUP}"]' in pyproject
    assert 'sample-plugin = "sample_plugin:build_server"' in pyproject
    assert 'packages = ["src/sample_plugin"]' in pyproject
    assert 'dependencies = ["mcp-plugin-gateway", "fastmcp>=4.1.4"]' in pyproject

    source = module.read_text(encoding="utf-8")
    assert 'FastMCP("sample-plugin")' in source
    assert "def hello(name: str) -> str:" in source
    assert "return f\"Hello, {name}!\"" in source

    readme = (project / "README.md").read_text(encoding="utf-8")
    assert "python -m pip install -e ." in readme
    assert "mcp-plugin-gateway llm" in readme
    assert "mcp-plugin-gateway sample-plugin hello Ada" in readme
    assert "mcp-plugin-gateway mcp" in readme


def test_scaffold_defaults_to_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    project = scaffold_plugin("sample")

    assert project == tmp_path / "plugins" / "sample"


@pytest.mark.parametrize(
    "distribution_name",
    [
        "",
        "../outside",
        "nested/name",
        "has space",
        "-leading",
        "trailing-",
        "two--hyphens",
        "1starts-with-digit",
        "under_score",
        "dot.name",
    ],
)
def test_scaffold_rejects_invalid_name_without_writing(
    tmp_path: Path, distribution_name: str
) -> None:
    with pytest.raises(ValueError, match="Invalid distribution name"):
        scaffold_plugin(distribution_name, root=tmp_path)

    assert not (tmp_path / "plugins").exists()


def test_scaffold_refuses_to_overwrite_existing_project(tmp_path: Path) -> None:
    target = tmp_path / "plugins" / "sample"
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("unchanged", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Plugin project already exists"):
        scaffold_plugin("sample", root=tmp_path)

    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in target.iterdir()) == ["keep.txt"]


def test_scaffold_reports_write_error_without_returning_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write_text = Path.write_text

    def fail_readme_write(
        path: Path, data: str, *args: object, **kwargs: object
    ) -> int:
        if path.name == "README.md":
            raise PermissionError("read-only filesystem")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_readme_write)

    with pytest.raises(OSError, match="Could not write plugin project") as excinfo:
        scaffold_plugin("sample", root=tmp_path)

    assert isinstance(excinfo.value.__cause__, PermissionError)
    assert not (tmp_path / "plugins" / "sample").exists()


def test_scaffold_refuses_plugins_directory_symlink_outside_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    try:
        (project / "plugins").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(OSError, match="outside project root"):
        scaffold_plugin("sample", root=project)

    assert list(outside.iterdir()) == []
