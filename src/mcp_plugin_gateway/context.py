"""LLM-facing documents rendered from live introspection.

Two audiences, two renderers:

* :func:`render_usage` describes an *install* -- the plugins that are present and
  the tools they expose on each surface.
* :func:`render_authoring` describes the *contract* a plugin author has to
  satisfy, including a worked example.

The authoring example lives here as a module-level constant rather than in the
renderer or the test, because the document and the contract test must describe
the same source. If the constant drifts out of contract, the test suite fails
instead of the document quietly lying.

The facts that can drift -- the entry-point group name and the permitted
surface tags -- are read from the modules that define them at render time
rather than restated here, so a change to either constant reaches the document
without a second edit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastmcp.tools.base import Tool

AUTHORING_EXAMPLE = '''\
from fastmcp import FastMCP


def build_server() -> FastMCP:
    server = FastMCP("my-plugin")

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    return server
'''
"""The plugin module source the authoring document shows and the test installs."""

AUTHORING_ENTRY_POINTS = """\
[{group}]
my-plugin = my_plugin:build_server
"""
"""Template for the ``entry_points.txt`` body.

``{group}`` stands for the entry-point group, which is resolved at render time
from :data:`mcp_plugin_gateway.discovery.ENTRY_POINT_GROUP`. Keeping the group
out of the literal is what lets the document follow the constant: a second
copy of the name here would be a second thing to forget to update.
"""


def authoring_entry_points() -> str:
    """Return the ``entry_points.txt`` body with the current group name."""
    from mcp_plugin_gateway.discovery import ENTRY_POINT_GROUP

    return AUTHORING_ENTRY_POINTS.format(group=ENTRY_POINT_GROUP)


# --- usage entries --------------------------------------------------------


@dataclass(frozen=True)
class ToolEntry:
    """One tool as the usage document describes it.

    The tool's JSON schema is carried verbatim in ``parameters`` so the
    document can show the same argument contract an MCP client would see,
    rather than a prose approximation of it.
    """

    name: str
    description: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    tags: frozenset[str] = frozenset()

    @property
    def surfaces(self) -> list[str]:
        """Return the surfaces this tool is reachable from.

        An untagged tool is reachable from both. A ``cli``-tagged tool is
        hidden from MCP clients; an ``mcp``-tagged tool is hidden from the CLI.
        """
        from mcp_plugin_gateway.server import CLI_TAG, MCP_TAG

        tags = set(self.tags)
        if CLI_TAG in tags:
            return ["cli"]
        if MCP_TAG in tags:
            return ["mcp"]
        return ["cli", "mcp"]


@dataclass(frozen=True)
class PluginEntry:
    """One installed plugin as the usage document describes it.

    A healthy plugin carries its distribution and its tools. An unusable one
    carries the failure reason instead, so a broken plugin is reported in the
    document rather than aborting it. A plugin whose name collides with a
    built-in command carries the conflict instead of being registered.
    """

    name: str
    distribution: str | None = None
    tools: tuple[ToolEntry, ...] = ()
    error: str | None = None
    conflict: str | None = None

    @property
    def usable(self) -> bool:
        """Whether the plugin was constructed successfully."""
        return self.error is None


def tool_entry(tool: "Tool") -> ToolEntry:
    """Return the usage entry for one tool."""
    return ToolEntry(
        name=tool.name,
        description=tool.description,
        parameters=dict(tool.parameters or {}),
        output_schema=(
            dict(tool.output_schema) if tool.output_schema is not None else None
        ),
        tags=frozenset(tool.tags or ()),
    )


def _schema_type(schema: dict[str, Any]) -> str:
    """Return a concise type label, inferring object and array shapes."""
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(str(item) for item in kind)
    if isinstance(kind, str):
        return kind
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return "any"


def _schema_lines(
    schema: dict[str, Any],
    *,
    label: str,
    required: bool = False,
    depth: int = 0,
) -> list[str]:
    """Summarize a schema node and its nested fields for Markdown."""
    details = [_schema_type(schema)]
    if required:
        details.append("required")
    else:
        details.append("optional")
        if "default" in schema:
            details.append(f"default `{schema['default']}`")

    line = f"{'  ' * depth}- {label} ({', '.join(details)})"
    enum = schema.get("enum")
    if isinstance(enum, list):
        line += f"; choices: {json.dumps(enum, ensure_ascii=False)}"
    lines = [line]

    properties = schema.get("properties") or {}
    if isinstance(properties, dict):
        required_properties = set(schema.get("required") or ())
        for name, prop in properties.items():
            if isinstance(prop, dict):
                lines.extend(
                    _schema_lines(
                        prop,
                        label=f"`{name}`",
                        required=name in required_properties,
                        depth=depth + 1,
                    )
                )

    items = schema.get("items")
    if isinstance(items, dict):
        lines.extend(_schema_lines(items, label="Items", depth=depth + 1))
    return lines


def _parameter_lines(parameters: dict[str, Any]) -> list[str]:
    """Return a recursive Markdown summary of tool input parameters."""
    properties = parameters.get("properties") or {}
    required = set(parameters.get("required") or ())
    lines: list[str] = []
    if isinstance(properties, dict):
        for name, prop in properties.items():
            if isinstance(prop, dict):
                lines.extend(
                    _schema_lines(
                        prop,
                        label=f"`{name}`",
                        required=name in required,
                    )
                )
    return lines


def usage_facts(entries: "Sequence[PluginEntry]") -> dict[str, Any]:
    """Return the usage document as structured data.

    The JSON output of the ``llm`` command is this document, and the markdown
    is a rendering of it -- not a second code path that could drift.
    """
    plugins: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.usable:
            plugins.append({"name": entry.name, "error": entry.error})
            continue
        plugin: dict[str, Any] = {
            "name": entry.name,
            "distribution": entry.distribution,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "surfaces": tool.surfaces,
                    "parameters": tool.parameters,
                    "output_schema": tool.output_schema,
                }
                for tool in entry.tools
            ],
        }
        if entry.conflict:
            plugin["conflict"] = entry.conflict
        plugins.append(plugin)
    return {"plugins": plugins}


def render_usage(entries: "Sequence[PluginEntry]") -> str:
    """Return the markdown document describing the installed plugins.

    Every installed plugin appears, healthy or not. A plugin that could not be
    constructed is listed with its failure reason, so one broken plugin never
    hides the healthy ones from the reader.

    The markdown is rendered from :func:`usage_facts`, the same structure the
    JSON output carries, so the two cannot describe different installs.
    """
    facts = usage_facts(entries)
    plugins = facts["plugins"]

    lines: list[str] = ["# Installed plugins", ""]
    if not plugins:
        lines += [
            "No plugins are installed.",
            "",
            "Run `mcp-plugin-gateway authoring` for the contract a plugin must",
            "satisfy.",
            "",
        ]
        return "\n".join(lines)

    healthy = [p for p in plugins if "error" not in p]
    broken = [p for p in plugins if "error" in p]
    lines.append(
        f"{len(healthy)} plugin(s) installed"
        + (f", {len(broken)} unusable." if broken else ".")
    )
    lines.append("")

    for plugin in healthy:
        lines.append(f"## {plugin['name']}")
        lines.append("")
        if plugin.get("distribution"):
            lines.append(f"- Distribution: `{plugin['distribution']}`")
        if plugin.get("conflict"):
            lines.append(f"- Conflict: {plugin['conflict']}")
        tools = plugin.get("tools") or []
        if not tools:
            lines.append("- No tools.")
        for tool in tools:
            lines.append("")
            lines.append(f"### {tool['name']}")
            lines.append("")
            if tool.get("description"):
                lines.append(tool["description"])
                lines.append("")
            lines.append(f"- Surfaces: {', '.join(tool['surfaces'])}")
            parameters = _parameter_lines(tool.get("parameters") or {})
            if parameters:
                lines.append("- Parameters:")
                lines.extend(f"  {line}" for line in parameters)
            else:
                lines.append("- Parameters: none")
            output_schema = tool.get("output_schema")
            if isinstance(output_schema, dict):
                lines.extend(_schema_lines(output_schema, label="Output"))
        lines.append("")

    for plugin in broken:
        lines.append(f"## {plugin['name']}")
        lines.append("")
        lines.append(f"- Unusable: {plugin['error']}")
        lines.append("")

    return "\n".join(lines)


def _surface_tag_notes() -> dict[str, str]:
    """Return a one-line explanation for each permitted surface tag.

    The tag names come from the set the system accepts; the explanation for the
    two tags the system defines is keyed by the constants that name them, so a
    renamed tag keeps its explanation. A tag the system accepts but does not
    define yet gets a generic note rather than being dropped from the document.
    """
    from mcp_plugin_gateway import server

    known = {
        server.CLI_TAG: "Hidden from MCP clients; reachable from the CLI.",
        server.MCP_TAG: "Hidden from the CLI; reachable from MCP clients.",
    }
    return {
        tag: known.get(tag, "Restricts this tool to the named surface.")
        for tag in sorted(server.SURFACE_TAGS)
    }


def authoring_facts() -> dict[str, Any]:
    """Return the authoring contract as structured data.

    The JSON output of the ``authoring`` command is this document, and the
    markdown is a rendering of it -- not a second code path that could drift.
    """
    from mcp_plugin_gateway import discovery, server

    return {
        "entry_point_group": discovery.ENTRY_POINT_GROUP,
        "entry_point_value": "module:attribute",
        "callable": "zero-argument callable returning a fastmcp.FastMCP instance",
        "surface_tags": sorted(server.SURFACE_TAGS),
        "surface_tag_notes": _surface_tag_notes(),
        "example": AUTHORING_EXAMPLE,
        "entry_points": authoring_entry_points(),
        "pyproject_example": "\n".join(
            [
                "[build-system]",
                'requires = ["hatchling"]',
                'build-backend = "hatchling.build"',
                "",
                "[project]",
                'name = "my-plugin"',
                'version = "0.1.0"',
                'description = "A plugin for mcp-plugin-gateway."',
                'requires-python = ">=3.12"',
                'dependencies = ["mcp-plugin-gateway", "fastmcp>=4.1.4"]',
                "",
                f'[project.entry-points."{discovery.ENTRY_POINT_GROUP}"]',
                'my-plugin = "my_plugin:build_server"',
                "",
                "[tool.hatch.build.targets.wheel]",
                'packages = ["src/my_plugin"]',
            ]
        )
        + "\n",
        "scaffold_command": "mcp-plugin-gateway new-plugin my-plugin",
        "package_layout": [
            "plugins/my-plugin/pyproject.toml",
            "plugins/my-plugin/README.md",
            "plugins/my-plugin/src/my_plugin/__init__.py",
        ],
        "install_command": "python -m pip install -e .",
        "verify_command": "mcp-plugin-gateway llm --plugin my-plugin",
        "cli_example": "mcp-plugin-gateway my-plugin hello Ada",
        "mcp_command": "mcp-plugin-gateway mcp --plugin my-plugin",
    }


def render_authoring() -> str:
    """Return the markdown document describing how to author a plugin.

    The document is independent of what is installed: it describes the contract
    every plugin must satisfy, so it stays complete in an environment with no
    plugins at all.
    """
    facts = authoring_facts()
    group = facts["entry_point_group"]
    notes = facts["surface_tag_notes"]

    lines: list[str] = [
        "# Authoring a plugin",
        "",
        "A plugin is a bare `FastMCP` server. There is no base class to subclass",
        "and no registration call to make: the system discovers installed",
        "distributions, calls the callable your entry point names, and mounts the",
        "server it returns.",
        "",
        "## The contract",
        "",
        f"1. Ship a distribution that declares exactly one entry point in the group",
        f"   `{group}`.",
        f"2. The entry point value is `{facts['entry_point_value']}`, where the",
        "   attribute names a zero-argument callable.",
        f"3. The callable must return a single, bare `fastmcp.FastMCP` instance --",
        "   anything else is rejected with an error naming your plugin.",
        "4. The callable is invoked lazily, the first time a surface needs the",
        "   plugin's components. Importing your module must not read configuration",
        "   or start a server.",
        "5. Plugin names must be unique across installed distributions. A duplicate",
        "   is rejected with an error naming every distribution that claims it.",
        "",
        "## Surface tags",
        "",
        "Tools are reachable from both the CLI and MCP clients by default. Tag a",
        "tool to restrict it to one surface:",
        "",
    ]
    for tag in facts["surface_tags"]:
        lines.append(f"- `{tag}` -- {notes[tag]}")
    lines += [
        "",
        "Any other tag is a typo and fails loudly at registration.",
        "",
        "## Example",
        "",
        "`src/my_plugin/__init__.py`:",
        "",
        "```python",
        facts["example"].rstrip("\n"),
        "```",
        "",
        "`entry_points.txt`:",
        "",
        "```ini",
        facts["entry_points"].rstrip("\n"),
        "```",
        "",
        "A standalone distribution can declare its metadata and entry point in",
        "`pyproject.toml`:",
        "",
        "```toml",
        facts["pyproject_example"],
        "```",
        "",
        "## Create, install, and use a plugin",
        "",
        "From the host project root, scaffold a standalone distribution:",
        "",
        "```bash",
        facts["scaffold_command"],
        "```",
        "",
        "The generated package contains:",
        "",
    ]
    lines.extend(f"- `{path}`" for path in facts["package_layout"])
    lines += [
        "",
        "Install it into the same Python environment that runs",
        "`mcp-plugin-gateway`:",
        "",
        "```bash",
        f'cd {facts["package_layout"][0].rsplit("/", 1)[0]}',
        facts["install_command"],
        "```",
        "",
        "Verify that discovery lists the plugin and its tools:",
        "",
        "```bash",
        facts["verify_command"],
        "```",
        "",
        "Call the starter tool from the CLI (required tool arguments are",
        "positional):",
        "",
        "```bash",
        facts["cli_example"],
        "```",
        "",
        "To serve this plugin over MCP stdio:",
        "",
        "```bash",
        facts["mcp_command"],
        "```",
        "",
        "Configure your MCP client to launch that command over its stdio",
        "transport. `mcp-plugin-gateway llm` describes installed tools and",
        "`mcp-plugin-gateway authoring` prints this guide.",
        "",
    ]
    return "\n".join(lines)
