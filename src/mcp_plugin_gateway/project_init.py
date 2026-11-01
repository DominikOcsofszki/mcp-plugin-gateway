"""Plan and apply a minimal MCP integration for an existing Python project."""

from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_plugin_gateway.discovery import ENTRY_POINT_GROUP

_PLUGIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\Z")
_REQUIRED_DEPENDENCIES = ("mcp-plugin-gateway", "fastmcp>=4.1.4")
_ENTRY_POINT_TABLE = f'[project.entry-points."{ENTRY_POINT_GROUP}"]'


@dataclass(frozen=True)
class ProjectInitPlan:
    """A fully inspected set of edits for one existing Python project."""

    project_root: Path
    plugin_name: str
    package_name: str
    pyproject_path: Path
    original_pyproject: str
    updated_pyproject: str
    module_path: Path
    module_content: str

    def diff(self) -> str:
        """Return a unified diff of the existing metadata and new module."""
        metadata_diff = "".join(
            difflib.unified_diff(
                self.original_pyproject.splitlines(keepends=True),
                self.updated_pyproject.splitlines(keepends=True),
                fromfile=str(self.pyproject_path),
                tofile=str(self.pyproject_path),
            )
        )
        module_diff = "".join(
            difflib.unified_diff(
                [],
                self.module_content.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=str(self.module_path),
            )
        )
        return metadata_diff + module_diff


def _table_bounds(lines: list[str], table: str) -> tuple[int, int] | None:
    """Find a TOML table's header and the next table boundary."""
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.split("#", 1)[0].strip() == table
        ),
        None,
    )
    if header_index is None:
        return None
    end_index = len(lines)
    for index in range(header_index + 1, len(lines)):
        stripped = lines[index].split("#", 1)[0].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end_index = index
            break
    return header_index, end_index


def _section_for(lines: list[str], table: str) -> tuple[int, int]:
    bounds = _table_bounds(lines, table)
    if bounds is None:
        raise ValueError(f"Unsupported pyproject.toml: missing {table} table")
    return bounds


def _array_close(text: str, opening: int) -> int:
    """Find the matching closing bracket for a TOML array."""
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(text)):
        character = text[index]
        if quote is not None:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unsupported pyproject.toml: unterminated dependencies array")


def _dependency_names(dependencies: Any) -> set[str]:
    """Return canonical package names from a PEP 621 dependencies array."""
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ValueError("Unsupported pyproject.toml: project.dependencies must be a string array")
    names: set[str] = set()
    for requirement in dependencies:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        if match:
            names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return names


def _add_dependencies(text: str, project: dict[str, Any]) -> str:
    dependencies = project.get("dependencies")
    existing = _dependency_names(dependencies) if dependencies is not None else set()
    missing = [
        requirement
        for requirement in _REQUIRED_DEPENDENCIES
        if re.sub(r"[-_.]+", "-", requirement.split(">=", 1)[0]).lower()
        not in existing
    ]
    if not missing:
        return text

    lines = text.splitlines(keepends=True)
    start, end = _section_for(lines, "[project]")
    if dependencies is None:
        addition = (
            "dependencies = [\n"
            + "".join(f"    {json.dumps(item)},\n" for item in missing)
            + "]\n"
        )
        if end > start and not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines.insert(end, addition)
        return "".join(lines)

    assignment_index = next(
        (
            index
            for index in range(start + 1, end)
            if re.match(r"\s*dependencies\s*=", lines[index])
        ),
        None,
    )
    if assignment_index is None:
        raise ValueError("Unsupported pyproject.toml: could not locate dependencies array")

    opening = text.find("[", sum(len(line) for line in lines[:assignment_index]))
    if opening < 0:
        raise ValueError("Unsupported pyproject.toml: dependencies must be an array")
    closing = _array_close(text, opening)
    closing_line = text.rfind("\n", opening, closing) + 1
    if text[closing_line:closing].strip() == "" and closing_line > opening + 1:
        prior_lines = text[opening:closing_line].splitlines()
        item_indent = next(
            (
                line[: len(line) - len(line.lstrip())]
                for line in reversed(prior_lines)
                if line.strip() and line.strip() != "["
            ),
            "    ",
        )
        addition = "".join(f"{item_indent}{json.dumps(item)},\n" for item in missing)
        return text[:closing_line] + addition + text[closing_line:]

    body = text[opening + 1 : closing]
    separator = "" if not body.strip() else (
        " " if body.rstrip().endswith(",") else ", "
    )
    addition = separator + ", ".join(json.dumps(item) for item in missing)
    return text[:closing] + addition + text[closing:]


def _add_entry_point(
    text: str, project: dict[str, Any], plugin_name: str, module_path: str
) -> str:
    entry_points = project.get("entry-points", {})
    if not isinstance(entry_points, dict):
        raise ValueError("Unsupported pyproject.toml: project.entry-points must be a table")
    group = entry_points.get(ENTRY_POINT_GROUP)
    if group is not None and not isinstance(group, dict):
        raise ValueError(
            f"Unsupported pyproject.toml: {ENTRY_POINT_GROUP} must be a table"
        )
    if isinstance(group, dict) and plugin_name in group:
        raise ValueError(
            f"Plugin entry point {plugin_name!r} already exists in {ENTRY_POINT_GROUP}"
        )

    lines = text.splitlines(keepends=True)
    bounds = _table_bounds(lines, _ENTRY_POINT_TABLE)
    declaration = f'{plugin_name} = "{module_path}:build_server"\n'
    if bounds is not None:
        _, end = bounds
        lines.insert(end, declaration)
        return "".join(lines)

    addition = (
        ("" if text.endswith("\n\n") else "\n")
        + _ENTRY_POINT_TABLE
        + "\n"
        + declaration
    )
    return text + addition


def _find_package(project_root: Path) -> tuple[Path, str]:
    source_root = project_root / "src"
    if not source_root.is_dir():
        source_root = project_root
    if not source_root.resolve().is_relative_to(project_root):
        raise ValueError("Unsupported project layout: source directory resolves outside project")

    packages = sorted(
        candidate
        for candidate in source_root.iterdir()
        if candidate.is_dir() and (candidate / "__init__.py").is_file()
    )
    if len(packages) != 1:
        raise ValueError(
            "Unsupported or ambiguous project layout: expected exactly one top-level "
            f"Python package under {source_root}, found {len(packages)}"
        )
    package_dir = packages[0]
    if not package_dir.resolve().is_relative_to(project_root):
        raise ValueError(
            f"Unsupported project layout: package resolves outside project: {package_dir}"
        )
    return package_dir, package_dir.name


def _module_source(plugin_name: str) -> str:
    return f'''"""MCP plugin entry point for {plugin_name}."""

from fastmcp import FastMCP


def build_server() -> FastMCP:
    """Create the MCP server for this project."""
    server = FastMCP("{plugin_name}")

    @server.tool
    def hello() -> str:
        """Confirm the project MCP server is available."""
        return "Hello from {plugin_name}."

    return server
'''


def build_project_init_plan(root: Path | None = None) -> ProjectInitPlan:
    """Inspect a PEP 621 project and return its complete proposed MCP edits."""
    project_root = (Path.cwd() if root is None else Path(root)).resolve()
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.is_file() or pyproject_path.is_symlink():
        raise ValueError(
            f"Unsupported project: expected a regular pyproject.toml at {pyproject_path}"
        )

    original = pyproject_path.read_text(encoding="utf-8")
    try:
        metadata = tomllib.loads(original)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid pyproject.toml: {exc}") from exc

    project = metadata.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        raise ValueError(
            "Unsupported project: pyproject.toml must contain PEP 621 [project].name"
        )
    plugin_name = re.sub(r"[-_.]+", "-", project["name"]).lower()
    if not _PLUGIN_NAME.fullmatch(plugin_name):
        raise ValueError(
            f"Project name {project['name']!r} cannot be used as a plugin name"
        )

    package_dir, package_name = _find_package(project_root)
    module_path = package_dir / "mcp_server.py"
    if module_path.exists() or module_path.is_symlink():
        raise ValueError(f"Refusing to overwrite existing module: {module_path}")

    updated = _add_dependencies(original, project)
    updated_metadata = tomllib.loads(updated).get("project", {})
    updated = _add_entry_point(
        updated,
        updated_metadata,
        plugin_name,
        f"{package_name}.mcp_server",
    )
    tomllib.loads(updated)

    return ProjectInitPlan(
        project_root=project_root,
        plugin_name=plugin_name,
        package_name=package_name,
        pyproject_path=pyproject_path,
        original_pyproject=original,
        updated_pyproject=updated,
        module_path=module_path,
        module_content=_module_source(plugin_name),
    )


def apply_project_init_plan(plan: ProjectInitPlan) -> list[Path]:
    """Apply a reviewed plan, atomically replacing metadata and cleaning up on failure."""
    staged_metadata: Path | None = None
    module_created = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".pyproject.toml.", dir=plan.project_root
        )
        staged_metadata = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as staged:
            os.chmod(staged_metadata, plan.pyproject_path.stat().st_mode & 0o777)
            staged.write(plan.updated_pyproject)
            staged.flush()
            os.fsync(staged.fileno())

        with plan.module_path.open("x", encoding="utf-8") as module:
            module_created = True
            module.write(plan.module_content)
            module.flush()
            os.fsync(module.fileno())

        os.replace(staged_metadata, plan.pyproject_path)
        staged_metadata = None
    except OSError as exc:
        cleanup_errors: list[str] = []
        if module_created:
            try:
                plan.module_path.unlink()
            except OSError as cleanup_exc:
                cleanup_errors.append(f"{plan.module_path}: {cleanup_exc}")
        if staged_metadata is not None:
            try:
                staged_metadata.unlink()
            except OSError as cleanup_exc:
                cleanup_errors.append(f"{staged_metadata}: {cleanup_exc}")
        details = f"Could not apply MCP project changes: {exc}"
        if cleanup_errors:
            details += "; cleanup failed: " + "; ".join(cleanup_errors)
        raise OSError(details) from exc

    return [plan.pyproject_path, plan.module_path]
