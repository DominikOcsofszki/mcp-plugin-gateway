"""Tests for schema-driven CLI generation and execution."""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from pathlib import Path
from typing import Literal

import pytest
import typer
from pydantic import BaseModel

from mcp_plugin_gateway.cli import (
    _coerce_arguments,
    _render,
    _run_async,
    build_cli,
    build_plugin_group,
    run_tool,
    signature_from_schema,
    stub_group,
)
from mcp_plugin_gateway.discovery import PluginSpec
from mcp_plugin_gateway.registry import PluginRegistry
from mcp_plugin_gateway.server import iter_cli_tools


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


class Inner(BaseModel):
    a: str
    b: int = 2


class Outer(BaseModel):
    name: str
    inner: Inner
    tags: list[str] = []


def _schema(**properties: object) -> dict[str, object]:
    return {"type": "object", "properties": properties}


# --- 5.1 schema-driven generation ----------------------------------------


def test_enum_parameter_renders_as_choice() -> None:
    schema = _schema(color={"type": "string", "enum": ["red", "blue"]})

    sig = signature_from_schema(schema)
    param = sig.parameters["color"]

    assert param.annotation is str
    assert "red" in param.default.autocompletion("r")


def test_array_parameter_renders_as_repeatable_flag() -> None:
    schema = _schema(items={"type": "array", "items": {"type": "string"}})

    sig = signature_from_schema(schema)
    param = sig.parameters["items"]

    assert param.annotation == list[str]
    assert param.default.param_decls == ("--items",)


def test_boolean_parameter_renders_paired_flags() -> None:
    schema = _schema(flag={"type": "boolean", "default": False})

    sig = signature_from_schema(schema)
    param = sig.parameters["flag"]

    assert param.annotation is bool
    assert param.default.param_decls == ("--flag/--no-flag",)


# --- 5.2 parameter mapping -----------------------------------------------


def test_required_parameter_is_positional() -> None:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    param = signature_from_schema(schema).parameters["query"]

    assert isinstance(param.default, typer.models.ArgumentInfo)
    assert param.default.default is ...


def test_defaulted_parameter_is_named_option() -> None:
    schema = _schema(limit={"type": "integer", "default": 10})

    param = signature_from_schema(schema).parameters["limit"]

    assert isinstance(param.default, typer.models.OptionInfo)
    assert param.default.default == 10
    assert param.default.param_decls == ("--limit",)


def test_underscore_name_becomes_dashed_flag() -> None:
    schema = _schema(max_items={"type": "integer", "default": 5})

    param = signature_from_schema(schema).parameters["max_items"]

    assert param.default.param_decls == ("--max-items",)


def test_annotation_mapping_covers_scalar_types() -> None:
    schema = _schema(
        i={"type": "integer"},
        n={"type": "number"},
        s={"type": "string"},
        b={"type": "boolean"},
    )

    params = signature_from_schema(schema).parameters

    assert params["i"].annotation is int
    assert params["n"].annotation is float
    assert params["s"].annotation is str
    assert params["b"].annotation is bool


# --- 5.3 nested objects --------------------------------------------------


def test_nested_object_renders_as_json_option() -> None:
    schema = _schema(
        o={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
    )

    param = signature_from_schema(schema).parameters["o"]

    assert param.annotation is str
    assert "JSON" in (param.default.help or "")


def test_ref_into_defs_is_resolved() -> None:
    schema = {
        "type": "object",
        "properties": {"o": {"$ref": "#/$defs/Outer"}},
        "required": ["o"],
        "$defs": {
            "Outer": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        },
    }

    param = signature_from_schema(schema).parameters["o"]

    assert param.annotation is str


def test_nested_object_round_trips_through_cli() -> None:
    """A JSON-valued option reaches the tool as the same object as a direct call."""
    from fastmcp import FastMCP

    server = FastMCP("nested")

    @server.tool
    def echo_outer(o: Outer) -> str:
        """Echo the nested object."""
        return json.dumps(o.model_dump(), sort_keys=True)

    async def schema_of() -> dict:
        for tool in await server.list_tools():
            if tool.name == "echo_outer":
                return tool.parameters
        raise AssertionError("tool missing")

    schema = _run_async(schema_of())
    payload = _coerce_arguments(schema, {"o": '{"name": "x", "inner": {"a": "y"}}'})

    assert payload["o"] == {"name": "x", "inner": {"a": "y"}}

    via_cli = run_tool(server, "echo_outer", payload)
    direct = _run_async(server.call_tool("echo_outer", payload))

    assert _render(via_cli) == _render(direct)


# --- 5.4 synchronous bridge ----------------------------------------------


def test_bridge_runs_inline_without_loop() -> None:
    async def coro() -> str:
        return "inline"

    assert _run_async(coro()) == "inline"


def test_bridge_runs_in_worker_thread_with_active_loop() -> None:
    async def outer() -> str:
        async def coro() -> str:
            return "threaded"

        return _run_async(coro())

    assert asyncio.run(outer()) == "threaded"


# --- 5.5 execution through the client ------------------------------------


def test_cli_result_equals_direct_result() -> None:
    from fastmcp import FastMCP

    server = FastMCP("exec")

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    via_cli = run_tool(server, "add", {"a": 2, "b": 3})
    direct = _run_async(server.call_tool("add", {"a": 2, "b": 3}))

    assert _render(via_cli) == _render(direct)
    assert "5" in _render(via_cli)


# --- 5.6 always-grouped tree --------------------------------------------


def _plugin_with(tools: int) -> "FastMCP":
    from fastmcp import FastMCP

    server = FastMCP("alpha")

    @server.tool
    def ping() -> str:
        """Ping."""
        return "pong"

    if tools > 1:

        @server.tool
        def pong() -> str:
            """Pong."""
            return "ping"

    return server


def _invocation_path(registry: PluginRegistry) -> list[str]:
    app = build_cli(registry)
    return sorted(
        f"{group.name} {cmd.name}"
        for group in app.registered_groups
        for cmd in group.typer_instance.registered_commands
    )


def test_single_tool_plugin_keeps_group_name() -> None:
    registry = PluginRegistry([PluginSpec("alpha", lambda: _plugin_with(1), "demo")])

    assert _invocation_path(registry) == ["alpha ping"]


def test_adding_a_tool_keeps_existing_path() -> None:
    one = _invocation_path(
        PluginRegistry([PluginSpec("alpha", lambda: _plugin_with(1), "demo")])
    )
    two = _invocation_path(
        PluginRegistry([PluginSpec("alpha", lambda: _plugin_with(2), "demo")])
    )

    assert "alpha ping" in one
    assert "alpha ping" in two


# --- 5.8 construction failure tolerance ---------------------------------


def _broken() -> "FastMCP":
    raise ValueError("ALPHA_TOKEN is required")


def test_broken_plugin_becomes_stub_group() -> None:
    registry = PluginRegistry(
        [
            PluginSpec("alpha", lambda: _plugin_with(1), "demo"),
            PluginSpec("broken", _broken, "demo"),
        ]
    )

    app = build_cli(registry)
    names = sorted(group.name for group in app.registered_groups)

    assert names == ["alpha", "broken"]


def test_stub_group_reports_error(capsys: pytest.CaptureFixture[str]) -> None:
    parent = typer.Typer()
    parent.add_typer(
        stub_group("broken", ValueError("ALPHA_TOKEN is required")), name="broken"
    )

    with pytest.raises((typer.Exit, SystemExit)) as excinfo:
        parent(args=["broken", "error"])

    code = getattr(excinfo.value, "exit_code", None)
    if code is None:
        code = getattr(excinfo.value, "code", 1)
    assert code == 1
    assert "ALPHA_TOKEN is required" in capsys.readouterr().err


def test_help_lists_healthy_plugins_when_one_is_broken() -> None:
    registry = PluginRegistry(
        [
            PluginSpec("alpha", lambda: _plugin_with(1), "demo"),
            PluginSpec("broken", _broken, "demo"),
        ]
    )

    app = build_cli(registry)
    names = sorted(group.name for group in app.registered_groups)

    assert "alpha" in names
    assert "broken" in names


# --- 5.7 dynamic completion ----------------------------------------------


def test_plugin_name_completion_uses_installed_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_plugin_gateway import cli as cli_module

    monkeypatch.setattr(cli_module, "_plugin_names", lambda: ["alpha", "beta"])

    assert cli_module._complete_plugin("a") == ["alpha"]
    assert cli_module._complete_plugin("") == ["alpha", "beta"]


def test_tool_name_completion_uses_visible_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_plugin_gateway import cli as cli_module

    monkeypatch.setattr(cli_module, "_tool_names", lambda plugin: ["add", "paint"])

    class _Ctx:
        params = {"plugin": "alpha"}

    assert cli_module._complete_tool(_Ctx(), "a") == ["add"]
    assert cli_module._complete_tool(_Ctx(), "") == ["add", "paint"]


def test_tool_completion_without_plugin_returns_nothing() -> None:
    from mcp_plugin_gateway import cli as cli_module

    class _Ctx:
        params: dict = {}

    assert cli_module._complete_tool(_Ctx(), "") == []


def test_completion_never_raises_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_plugin_names`` swallows discovery errors so completion stays usable."""
    from mcp_plugin_gateway import cli as cli_module
    from mcp_plugin_gateway import registry as registry_module

    def boom() -> None:
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(registry_module, "build_registry", boom)

    assert cli_module._plugin_names() == []


# --- 5.10 output formatting ----------------------------------------------


def _result_for(return_value: object, annotation: type = str) -> object:
    """Call a tool returning ``return_value`` and return its raw result.

    The annotation matters: FastMCP wraps a return whose annotation is not a
    concrete type as ``{"result": value}``, so tests pass the type the tool
    would really declare. Each annotation gets its own typed tool because
    pydantic resolves annotations statically.
    """
    from fastmcp import FastMCP

    server = FastMCP("render")

    if annotation is dict:

        @server.tool
        def probe_dict() -> dict:
            """Return a fixed mapping."""
            return return_value  # type: ignore[return-value]

        name = "probe_dict"
    elif annotation is int:

        @server.tool
        def probe_int() -> int:
            """Return a fixed integer."""
            return return_value  # type: ignore[return-value]

        name = "probe_int"
    else:

        @server.tool
        def probe_str() -> str:
            """Return a fixed string."""
            return return_value  # type: ignore[return-value]

        name = "probe_str"

    return _run_async(server.call_tool(name, {}))


def test_string_result_prints_verbatim_by_default() -> None:
    """A string tool prints its text, not a JSON envelope."""
    assert _render(_result_for("Hello, Ada!")) == "Hello, Ada!"


def test_markdown_result_keeps_its_formatting() -> None:
    """Markdown reaches stdout unescaped, so tables and headings survive."""
    markdown = "## Status\n\n| plugin | state |\n|---|---|\n| example | ok |"

    assert _render(_result_for(markdown)) == markdown


def test_structured_result_prints_as_json_by_default() -> None:
    """A dict tool prints pretty JSON without the ``result`` wrapper."""
    rendered = _render(_result_for({"a": 1, "b": [2, 3]}, dict))

    assert json.loads(rendered) == {"a": 1, "b": [2, 3]}
    assert "result" not in json.loads(rendered)


def test_scalar_result_prints_bare_value_by_default() -> None:
    """A non-string scalar prints its value, not ``{"result": 7}``."""
    assert _render(_result_for(7, int)) == "7"


def test_json_flag_restores_the_envelope() -> None:
    """``--json`` prints the whole ``structured_content`` document."""
    rendered = _render(_result_for("Hello, Ada!"), json_output=True)

    assert json.loads(rendered) == {"result": "Hello, Ada!"}


def test_json_flag_keeps_structured_results_unchanged() -> None:
    """A dict tool renders identically in both modes."""
    result = _result_for({"a": 1}, dict)

    assert _render(result) == _render(result, json_output=True)


def test_tool_returning_result_key_is_not_unwrapped() -> None:
    """A genuine ``{"result": ...}`` return stays structured, not bare text."""
    rendered = _render(_result_for({"result": "genuine"}, dict))

    assert json.loads(rendered) == {"result": "genuine"}


def _run_cli(registry: PluginRegistry, argv: list[str]) -> str:
    """Invoke the generated CLI and return its stdout."""
    app = build_cli(registry)
    try:
        app(args=argv)
    except SystemExit:
        pass
    return ""


def _capture_cli(
    registry: PluginRegistry, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> str:
    _run_cli(registry, argv)
    return capsys.readouterr().out


def _greet_registry() -> PluginRegistry:
    from fastmcp import FastMCP

    server = FastMCP("alpha")

    @server.tool
    def greet(name: str) -> str:
        """Greet someone."""
        return f"Hello, {name}!"

    return PluginRegistry([PluginSpec("alpha", lambda: server, "demo")])


def test_cli_prints_text_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    out = _capture_cli(_greet_registry(), ["alpha", "greet", "Ada"], capsys)

    assert "Hello, Ada!" in out
    assert '"result"' not in out


def test_cli_leaf_json_flag_prints_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = _capture_cli(_greet_registry(), ["alpha", "greet", "Ada", "--json"], capsys)

    assert json.loads(out) == {"result": "Hello, Ada!"}


def test_cli_root_json_flag_prints_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` before the plugin name works as well as after the tool name."""
    out = _capture_cli(_greet_registry(), ["--json", "alpha", "greet", "Ada"], capsys)

    assert json.loads(out) == {"result": "Hello, Ada!"}


def test_cli_json_flag_does_not_leak_into_tool_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The flag is consumed by the CLI and never forwarded to the tool."""
    out = _capture_cli(_greet_registry(), ["alpha", "greet", "Ada", "--json"], capsys)

    assert "Hello, Ada!" in json.loads(out)["result"]


def test_cli_tool_parameter_named_json_still_works(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tool parameter named ``json`` binds to ``--json-output``, not the flag."""
    from fastmcp import FastMCP

    server = FastMCP("alpha")

    @server.tool
    def echo_json(json: str) -> str:
        """Echo a parameter named json."""
        return f"got {json}"

    registry = PluginRegistry([PluginSpec("alpha", lambda: server, "demo")])

    out = _capture_cli(registry, ["alpha", "echo_json", "hello"], capsys)
    assert "got hello" in out

    out = _capture_cli(registry, ["alpha", "echo_json", "hello", "--json"], capsys)
    assert json.loads(out) == {"result": "got hello"}


# --- 2.1 optional plugin selector ----------------------------------------


def test_tool_runs_without_plugin_name_for_lone_plugin(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A standalone distribution omits the plugin name: ``greet Ada`` works."""
    out = _capture_cli(_greet_registry(), ["greet", "Ada"], capsys)

    assert "Hello, Ada!" in out


def _run_cli_exit_code(registry: PluginRegistry, argv: list[str]) -> int:
    """Invoke the generated CLI and return its exit code."""
    app = build_cli(registry)
    try:
        app(args=argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _two_plugin_registry(calls: list[str]) -> PluginRegistry:
    """Return a registry with ``alpha`` and ``beta``, recording tool calls."""
    from fastmcp import FastMCP

    def plugin(name: str) -> "FastMCP":
        server = FastMCP(name)

        @server.tool
        def greet(who: str) -> str:
            """Greet someone."""
            calls.append(name)
            return f"{name} says hello to {who}"

        return server

    return PluginRegistry(
        [
            PluginSpec("alpha", lambda: plugin("alpha"), "demo"),
            PluginSpec("beta", lambda: plugin("beta"), "demo"),
        ]
    )


# --- 2.2 ambiguity error path --------------------------------------------


def test_unnamed_invocation_is_rejected_with_multiple_plugins(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two installed plugins make the plugin name mandatory."""
    calls: list[str] = []
    registry = _two_plugin_registry(calls)

    code = _run_cli_exit_code(registry, ["greet", "Ada"])
    captured = capsys.readouterr()

    assert code != 0
    assert "alpha" in captured.err
    assert "beta" in captured.err
    assert calls == []


# --- 2.3 explicit plugin name still works --------------------------------


def test_named_and_unnamed_invocations_match_for_lone_plugin(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``greet Ada`` and ``alpha greet Ada`` produce the same result and code."""
    registry = _greet_registry()

    named_code = _run_cli_exit_code(registry, ["alpha", "greet", "Ada"])
    named_out = capsys.readouterr().out
    unnamed_code = _run_cli_exit_code(registry, ["greet", "Ada"])
    unnamed_out = capsys.readouterr().out

    assert named_code == unnamed_code == 0
    assert named_out == unnamed_out
    assert "Hello, Ada!" in named_out


def test_explicit_plugin_name_resolves_in_multi_plugin_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Naming the plugin keeps working when several are installed."""
    calls: list[str] = []
    registry = _two_plugin_registry(calls)

    code = _run_cli_exit_code(registry, ["beta", "greet", "Ada"])
    out = capsys.readouterr().out

    assert code == 0
    assert "beta says hello to Ada" in out
    assert calls == ["beta"]


# --- 5.1 llm and authoring commands --------------------------------------


def _usage_registry(calls: list[str]) -> PluginRegistry:
    """Return a registry whose plugin records every construction."""
    from fastmcp import FastMCP

    def build() -> "FastMCP":
        calls.append("construct")
        server = FastMCP("alpha")

        @server.tool
        def ping(x: int = 1) -> str:
            """Ping the server."""
            return f"pong {x}"

        @server.tool(tags={"cli"})
        def secret() -> str:
            """A CLI-only tool."""
            return "secret"

        @server.tool(tags={"mcp"})
        def remote() -> str:
            """An MCP-only tool."""
            return "remote"

        return server

    return PluginRegistry([PluginSpec("alpha", build, "demo")])


def test_llm_constructs_each_plugin_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One ``llm`` invocation runs each plugin's factory exactly once."""
    calls: list[str] = []
    registry = _usage_registry(calls)

    _capture_cli(registry, ["llm"], capsys)

    assert calls == ["construct"]


def test_llm_lists_every_tool_with_its_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The document names every tool and says which surface reaches it."""
    registry = _usage_registry([])

    out = _capture_cli(registry, ["llm"], capsys)

    assert "## alpha" in out
    assert "### ping" in out
    assert "### secret" in out
    assert "### remote" in out
    assert "- Surfaces: cli, mcp" in out
    assert "- Surfaces: cli\n" in out
    assert "- Surfaces: mcp\n" in out


def test_llm_shows_required_and_defaulted_parameters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A defaulted parameter is marked optional and shows its default."""
    from fastmcp import FastMCP

    server = FastMCP("alpha")

    @server.tool
    def greet(name: str, greeting: str = "Hello") -> str:
        """Greet someone."""
        return f"{greeting}, {name}!"

    registry = PluginRegistry([PluginSpec("alpha", lambda: server, "demo")])

    out = _capture_cli(registry, ["llm"], capsys)

    assert "- `name` (string, required)" in out
    assert "- `greeting` (string, optional, default `Hello`)" in out


def test_llm_and_the_command_group_list_the_same_tools(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The document and ``--help`` cannot disagree about which tools exist."""
    registry = _usage_registry([])

    out = _capture_cli(registry, ["llm"], capsys)
    app = build_cli(registry)
    group = next(g for g in app.registered_groups if g.name == "alpha")
    command_names = {cmd.name for cmd in group.typer_instance.registered_commands}

    assert command_names == {"ping", "secret"}
    assert "### remote" in out


def test_llm_reports_a_broken_plugin_and_keeps_healthy_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One broken plugin never hides the healthy ones from the document."""
    registry = PluginRegistry(
        [
            PluginSpec("alpha", lambda: _plugin_with(1), "demo"),
            PluginSpec("broken", _broken, "demo"),
        ]
    )

    out = _capture_cli(registry, ["llm"], capsys)

    assert "## alpha" in out
    assert "### ping" in out
    assert "## broken" in out
    assert "Unusable" in out
    assert "ALPHA_TOKEN is required" in out


def test_llm_scoped_to_a_broken_plugin_reports_the_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scoping to a broken plugin reports the reason instead of crashing."""
    registry = PluginRegistry(
        [
            PluginSpec("alpha", lambda: _plugin_with(1), "demo"),
            PluginSpec("broken", _broken, "demo"),
        ]
    )

    code = _run_cli_exit_code(registry, ["llm", "--plugin", "broken"])
    out = capsys.readouterr().out

    assert code == 0
    assert "## broken" in out
    assert "ALPHA_TOKEN is required" in out
    assert "## alpha" not in out


def test_llm_scoped_to_one_plugin_describes_only_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--plugin`` narrows the document to the named plugin."""
    calls: list[str] = []
    registry = _two_plugin_registry(calls)

    out = _capture_cli(registry, ["llm", "--plugin", "beta"], capsys)

    assert "## beta" in out
    assert "## alpha" not in out


def test_llm_rejects_an_unknown_plugin_without_partial_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown selector fails loudly and prints no document."""
    registry = _usage_registry([])

    code = _run_cli_exit_code(registry, ["llm", "--plugin", "nope"])
    captured = capsys.readouterr()

    assert code != 0
    assert "nope" in captured.err
    assert captured.out == ""


def test_llm_prints_json_on_request(capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` prints a JSON document carrying the same information."""
    registry = _usage_registry([])

    out = _capture_cli(registry, ["llm", "--json"], capsys)
    payload = json.loads(out)

    plugin = payload["plugins"][0]
    assert plugin["name"] == "alpha"
    assert {tool["name"] for tool in plugin["tools"]} == {"ping", "secret", "remote"}
    assert plugin["tools"][0]["surfaces"] == ["cli", "mcp"]


def test_llm_json_includes_complete_output_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The JSON context carries FastMCP's output schema without re-deriving it."""
    from fastmcp import FastMCP

    server = FastMCP("alpha")

    @server.tool
    def report() -> dict[str, int]:
        """Return a report."""
        return {"value": 1}

    registry = PluginRegistry([PluginSpec("alpha", lambda: server, "demo")])

    out = _capture_cli(registry, ["llm", "--json"], capsys)
    payload = json.loads(out)
    tool = payload["plugins"][0]["tools"][0]

    assert tool["name"] == "report"
    assert tool["output_schema"] == {
        "additionalProperties": {"type": "integer"},
        "type": "object",
    }


def test_llm_root_json_flag_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    """The root ``--json`` flag reaches the ``llm`` command too."""
    registry = _usage_registry([])

    out = _capture_cli(registry, ["--json", "llm"], capsys)

    assert json.loads(out)["plugins"][0]["name"] == "alpha"


def test_authoring_prints_the_contract(capsys: pytest.CaptureFixture[str]) -> None:
    """The authoring document states the group, the callable shape, and the tags."""
    registry = PluginRegistry([])

    out = _capture_cli(registry, ["authoring"], capsys)

    assert "mcp_plugin_gateway.plugins" in out
    assert "module:attribute" in out
    assert "- `cli` --" in out
    assert "- `mcp` --" in out


def test_authoring_prints_json_on_request(capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` prints the contract as structured data."""
    registry = PluginRegistry([])

    out = _capture_cli(registry, ["authoring", "--json"], capsys)
    payload = json.loads(out)

    assert payload["entry_point_group"] == "mcp_plugin_gateway.plugins"
    assert payload["surface_tags"] == ["cli", "mcp"]
    assert "def build_server()" in payload["example"]
    assert '[project.entry-points."mcp_plugin_gateway.plugins"]' in payload[
        "pyproject_example"
    ]
    assert payload["scaffold_command"] == "mpg new-plugin my-plugin"
    assert payload["scaffold_destination"] == "mcp-gateway/my-plugin/"
    assert payload["init_command"] == "mpg init"
    assert payload["init_approved_command"] == "mpg init --yes"
    assert payload["cli_example"] == "mcp-plugin-gateway my-plugin hello Ada"
    assert payload["mcp_command"] == "mcp-plugin-gateway mcp --plugin my-plugin"


def test_new_plugin_creates_the_standalone_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    out = _capture_cli(PluginRegistry([]), ["new-plugin", "my-plugin"], capsys)

    project = tmp_path / "mcp-gateway" / "my-plugin"
    assert "Created plugin project" in out
    assert (project / "pyproject.toml").is_file()
    assert (project / "README.md").is_file()
    assert (project / "src" / "my_plugin" / "__init__.py").is_file()


def test_new_plugin_accepts_a_custom_destination_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    out = _capture_cli(
        PluginRegistry([]),
        ["new-plugin", "my-plugin", "--directory", "custom/my-plugin"],
        capsys,
    )

    project = tmp_path / "custom" / "my-plugin"
    assert str(project) in out
    assert (project / "pyproject.toml").is_file()


def test_new_plugin_reports_invalid_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    code = _run_cli_exit_code(PluginRegistry([]), ["new-plugin", "../escape"])

    assert code == 2
    assert "Invalid distribution name" in capsys.readouterr().err
    assert not (tmp_path / "mcp-gateway").exists()


def test_new_plugin_refuses_to_overwrite_an_existing_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "mcp-gateway" / "my-plugin"
    target.mkdir(parents=True)
    marker = target / "keep.txt"
    marker.write_text("keep")

    code = _run_cli_exit_code(PluginRegistry([]), ["new-plugin", "my-plugin"])

    assert code == 1
    assert "already exists" in capsys.readouterr().err
    assert marker.read_text() == "keep"


def test_new_plugin_is_reserved_and_the_builtin_still_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fastmcp import FastMCP

    monkeypatch.chdir(tmp_path)
    server = FastMCP("new-plugin")

    @server.tool
    def ping() -> str:
        """Ping."""
        return "pong"

    registry = PluginRegistry([PluginSpec("new-plugin", lambda: server, "demo")])

    app = build_cli(registry)
    group_names = {group.name for group in app.registered_groups}
    assert "new-plugin" not in group_names
    assert "new-plugin" in capsys.readouterr().err

    out = _capture_cli(registry, ["new-plugin", "fresh-plugin"], capsys)
    assert "Created plugin project" in out
    assert (tmp_path / "mcp-gateway" / "fresh-plugin" / "pyproject.toml").is_file()


def _write_project_to_initialize(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample-project"\nversion = "1.0"\n',
        encoding="utf-8",
    )
    package = root / "src" / "sample_project"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")


def test_init_previews_and_applies_with_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_project_to_initialize(tmp_path)
    monkeypatch.chdir(tmp_path)

    out = _capture_cli(PluginRegistry([]), ["init", "--yes"], capsys)

    assert "Planned MCP integration for sample-project" in out
    assert "Initialized MCP plugin" in out
    assert (tmp_path / "src" / "sample_project" / "mcp_server.py").is_file()
    assert "sample_project.mcp_server:build_server" in (
        tmp_path / "pyproject.toml"
    ).read_text(encoding="utf-8")


def test_init_decline_leaves_project_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_project_to_initialize(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class InteractiveInput:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("mcp_plugin_gateway.cli.sys.stdin", InteractiveInput())
    monkeypatch.setattr("typer.confirm", lambda *_args, **_kwargs: False)

    out = _capture_cli(PluginRegistry([]), ["init"], capsys)

    assert "Cancelled" in out
    assert pyproject.read_text(encoding="utf-8") == original
    assert not (tmp_path / "src" / "sample_project" / "mcp_server.py").exists()


def test_init_noninteractive_requires_yes_and_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_project_to_initialize(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class NonInteractiveInput:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("mcp_plugin_gateway.cli.sys.stdin", NonInteractiveInput())

    code = _run_cli_exit_code(PluginRegistry([]), ["init"])

    assert code == 2
    assert "--yes" in capsys.readouterr().err
    assert pyproject.read_text(encoding="utf-8") == original
    assert not (tmp_path / "src" / "sample_project" / "mcp_server.py").exists()


def test_reserved_name_collision_is_reported_and_the_builtin_still_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A plugin named like a built-in is reported, and the built-in still works."""
    from fastmcp import FastMCP

    server = FastMCP("llm")

    @server.tool
    def ping() -> str:
        """Ping."""
        return "pong"

    registry = PluginRegistry([PluginSpec("llm", lambda: server, "demo")])

    app = build_cli(registry)
    group_names = {group.name for group in app.registered_groups}
    captured = capsys.readouterr()

    assert "llm" not in group_names
    assert "llm" in captured.err

    out = _capture_cli(registry, ["llm"], capsys)
    assert "# Installed plugins" in out
    assert "Conflict" in out
