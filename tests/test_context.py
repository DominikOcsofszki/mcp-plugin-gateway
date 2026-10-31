"""Unit tests for the LLM-facing document renderers."""

from __future__ import annotations

import pytest

from mcp_plugin_gateway.context import (
    AUTHORING_EXAMPLE,
    PluginEntry,
    ToolEntry,
    authoring_entry_points,
    authoring_facts,
    render_usage,
    tool_entry,
    usage_facts,
    render_authoring,
)


def test_authoring_document_states_the_contract() -> None:
    document = render_authoring()

    assert "mcp_plugin_gateway.plugins" in document
    assert "module:attribute" in document
    assert "fastmcp.FastMCP" in document


def test_authoring_document_lists_the_surface_tags() -> None:
    document = render_authoring()

    assert "`cli`" in document
    assert "`mcp`" in document


def test_authoring_document_embeds_the_example_byte_identically() -> None:
    document = render_authoring()

    assert AUTHORING_EXAMPLE in document
    assert authoring_entry_points() in document


def test_authoring_document_explains_the_plugin_workflow() -> None:
    document = render_authoring()

    assert '[project.entry-points."mcp_plugin_gateway.plugins"]' in document
    assert 'dependencies = ["mcp-plugin-gateway", "fastmcp>=4.1.4"]' in document
    assert "mcp-plugin-gateway new-plugin my-plugin" in document
    assert "plugins/my-plugin/src/my_plugin/__init__.py" in document
    assert "python -m pip install -e ." in document
    assert "mcp-plugin-gateway llm --plugin my-plugin" in document
    assert "mcp-plugin-gateway my-plugin hello Ada" in document
    assert "mcp-plugin-gateway mcp --plugin my-plugin" in document
    assert "stdio" in document


def test_authoring_document_follows_the_group_name_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The group name is read from ``discovery`` at render time."""
    from mcp_plugin_gateway import discovery

    monkeypatch.setattr(discovery, "ENTRY_POINT_GROUP", "other.group")

    document = render_authoring()

    assert "other.group" in document
    assert "mcp_plugin_gateway.plugins" not in document


def test_authoring_document_follows_the_surface_tag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tag names are read from ``server`` at render time."""
    from mcp_plugin_gateway import server

    monkeypatch.setattr(server, "SURFACE_TAGS", frozenset({"cli", "mcp", "web"}))

    document = render_authoring()

    assert "`web`" in document


def test_authoring_facts_carry_the_same_information() -> None:
    facts = authoring_facts()

    assert facts["entry_point_group"] == "mcp_plugin_gateway.plugins"
    assert facts["surface_tags"] == ["cli", "mcp"]
    assert facts["example"] == AUTHORING_EXAMPLE
    assert facts["entry_points"] == authoring_entry_points()
    assert '[project.entry-points."mcp_plugin_gateway.plugins"]' in facts[
        "pyproject_example"
    ]
    assert facts["scaffold_command"] == "mcp-plugin-gateway new-plugin my-plugin"
    assert facts["package_layout"] == [
        "plugins/my-plugin/pyproject.toml",
        "plugins/my-plugin/README.md",
        "plugins/my-plugin/src/my_plugin/__init__.py",
    ]


def test_authoring_document_is_independent_of_the_install() -> None:
    """The contract document does not depend on any plugin being installed."""
    document = render_authoring()

    assert AUTHORING_EXAMPLE in document


# --- usage renderer -------------------------------------------------------


def _entry(name: str, **kwargs: object) -> PluginEntry:
    return PluginEntry(name=name, **kwargs)  # type: ignore[arg-type]


def test_usage_document_lists_plugins_and_tools() -> None:
    entries = [
        _entry(
            "alpha",
            distribution="alpha-plugin",
            tools=(
                ToolEntry(name="add"),
                ToolEntry(name="sub"),
            ),
        ),
        _entry("beta", distribution="beta-plugin", tools=(ToolEntry(name="mul"),)),
    ]

    document = render_usage(entries)

    assert "## alpha" in document
    assert "## beta" in document
    assert "### add" in document
    assert "### sub" in document
    assert "### mul" in document


def test_usage_document_distinguishes_required_and_defaulted_parameters() -> None:
    entry = _entry(
        "alpha",
        distribution="alpha-plugin",
        tools=(
            ToolEntry(
                name="add",
                parameters={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer", "default": 2},
                    },
                    "required": ["a"],
                },
            ),
        ),
    )

    document = render_usage([entry])

    assert "`a` (integer, required)" in document
    assert "`b` (integer, optional, default `2`)" in document


def test_usage_document_summarizes_constrained_and_nested_schemas() -> None:
    entry = _entry(
        "alpha",
        tools=(
            ToolEntry(
                name="configure",
                parameters={
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["fast", "safe"]},
                        "settings": {
                            "type": "object",
                            "properties": {
                                "retries": {"type": "integer"},
                                "labels": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["retries"],
                        },
                    },
                    "required": ["mode", "settings"],
                },
                output_schema={
                    "type": "object",
                    "properties": {"accepted": {"type": "boolean"}},
                    "required": ["accepted"],
                },
            ),
        ),
    )

    document = render_usage([entry])

    assert '`mode` (string, required); choices: ["fast", "safe"]' in document
    assert "  - `retries` (integer, required)" in document
    assert "  - `labels` (array, optional)" in document
    assert "    - Items (string, optional)" in document
    assert "- Output (object, optional)" in document
    assert "  - `accepted` (boolean, required)" in document


def test_usage_document_labels_each_tool_surface() -> None:
    entry = _entry(
        "alpha",
        distribution="alpha-plugin",
        tools=(
            ToolEntry(name="secret", tags=frozenset({"cli"})),
            ToolEntry(name="remote", tags=frozenset({"mcp"})),
            ToolEntry(name="shared"),
        ),
    )

    document = render_usage([entry])

    secret_block = document.split("### secret")[1].split("###")[0]
    remote_block = document.split("### remote")[1].split("###")[0]
    shared_block = document.split("### shared")[1]

    assert "Surfaces: cli" in secret_block
    assert "Surfaces: mcp" in remote_block
    assert "Surfaces: cli, mcp" in shared_block


def test_usage_document_reports_a_broken_plugin_without_hiding_healthy_ones() -> None:
    entries = [
        _entry("alpha", distribution="alpha-plugin", tools=(ToolEntry(name="add"),)),
        _entry("broken", error="RuntimeError: boom"),
    ]

    document = render_usage(entries)

    assert "## alpha" in document
    assert "### add" in document
    assert "## broken" in document
    assert "Unusable: RuntimeError: boom" in document


def test_usage_document_reports_an_empty_install() -> None:
    document = render_usage([])

    assert "No plugins are installed." in document


def test_usage_facts_carry_the_same_information() -> None:
    entries = [
        _entry(
            "alpha",
            distribution="alpha-plugin",
            tools=(ToolEntry(name="add", tags=frozenset({"cli"})),),
        ),
        _entry("broken", error="RuntimeError: boom"),
    ]

    facts = usage_facts(entries)

    assert facts["plugins"][0]["name"] == "alpha"
    assert facts["plugins"][0]["tools"][0]["surfaces"] == ["cli"]
    assert facts["plugins"][0]["tools"][0]["output_schema"] is None
    assert facts["plugins"][1] == {"name": "broken", "error": "RuntimeError: boom"}


def test_usage_facts_preserve_complete_input_and_output_schemas() -> None:
    input_schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "safe"]},
            "nested": {
                "type": "object",
                "properties": {"count": {"type": "integer", "minimum": 1}},
            },
        },
        "required": ["mode"],
    }
    output_schema = {
        "type": "object",
        "properties": {"accepted": {"type": "boolean"}},
        "required": ["accepted"],
    }
    facts = usage_facts(
        [
            _entry(
                "alpha",
                tools=(
                    ToolEntry(
                        name="configure",
                        parameters=input_schema,
                        output_schema=output_schema,
                    ),
                ),
            )
        ]
    )

    tool = facts["plugins"][0]["tools"][0]
    assert tool["parameters"] == input_schema
    assert tool["output_schema"] == output_schema


def test_tool_entry_reads_a_real_tool_object() -> None:
    """The renderer reads ``Tool.parameters``, not a re-derived signature."""
    from fastmcp import FastMCP

    from mcp_plugin_gateway.cli import _run_async

    server = FastMCP("alpha")

    @server.tool
    def add(a: int, b: int = 2) -> int:
        """Add two integers."""
        return a + b

    tools = {tool.name: tool for tool in _run_async(server.list_tools())}

    entry = tool_entry(tools["add"])
    document = render_usage([_entry("alpha", tools=(entry,))])

    assert entry.parameters["required"] == ["a"]
    assert "`a` (integer, required)" in document
    assert "`b` (integer, optional, default `2`)" in document
    assert entry.output_schema == tools["add"].output_schema
