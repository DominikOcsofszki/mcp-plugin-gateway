"""Schema-driven CLI generation from MCP tool input schemas.

Commands are derived from each tool's JSON schema rather than its Python
signature, so the CLI accepts exactly what MCP accepts. Required parameters
become positional arguments, defaulted parameters become named options, enums
become choices, arrays become repeatable flags, and booleans become paired
on/off flags. Nested objects are accepted as a single JSON value.
"""

from __future__ import annotations

import inspect
import json
import sys
from typing import TYPE_CHECKING, Any

import typer
from click.parser import _split_opt
from typer.core import TyperGroup

from mcp_plugin_gateway.discovery import PluginError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from fastmcp import FastMCP

    from mcp_plugin_gateway.registry import PluginRegistry

JSON = "json"


# --- schema → signature ---------------------------------------------------


def _resolve_ref(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` into the document's ``$defs`` block."""
    seen: set[str] = set()
    while "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            break
        if ref in seen:
            break
        seen.add(ref)
        target: Any = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                target = None
                break
            target = target[part]
        if not isinstance(target, dict):
            break
        merged = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
        schema = merged
    return schema


def _is_object(schema: dict[str, Any]) -> bool:
    return schema.get("type") == "object" or "properties" in schema


def _python_type(schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """Return the annotation used for a schema node."""
    schema = _resolve_ref(schema, root)
    if "enum" in schema:
        return str
    if _is_object(schema):
        return str  # accepted as a JSON string, parsed before the call
    kind = schema.get("type")
    if kind == "integer":
        return int
    if kind == "number":
        return float
    if kind == "boolean":
        return bool
    if kind == "array":
        return list[str]
    return str


def _option_decl(name: str, schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """Build the ``typer.Option``/``typer.Argument`` default for one parameter."""
    schema = _resolve_ref(schema, root)
    flag = f"--{name.replace('_', '-')}"
    help_text = schema.get("description")
    default = schema.get("default")

    if "enum" in schema:
        choices = [str(v) for v in schema["enum"]]
        return typer.Option(
            default if default is not None else ...,
            flag,
            help=help_text,
            case_sensitive=True,
            autocompletion=lambda incomplete: [
                c for c in choices if c.startswith(incomplete)
            ],
        )
    if _is_object(schema):
        return typer.Option(
            json.dumps(default) if default is not None else ...,
            flag,
            help=help_text or "JSON object",
        )
    if schema.get("type") == "boolean":
        return typer.Option(
            bool(default) if default is not None else False,
            f"{flag}/--no-{name.replace('_', '-')}",
            help=help_text,
        )
    if schema.get("type") == "array":
        # Typer derives repeatability from the ``list[...]`` annotation, so the
        # option needs no ``multiple`` flag (removed in Typer 0.27).
        return typer.Option(
            default if default is not None else ...,
            flag,
            help=help_text,
        )
    return typer.Option(
        default if default is not None else ...,
        flag,
        help=help_text,
    )


def signature_from_schema(schema: dict[str, Any]) -> inspect.Signature:
    """Synthesize an ``inspect.Signature`` from a tool's input schema.

    Required parameters become positional arguments; parameters carrying a
    ``default`` become named options. Nested objects are annotated as ``str`` and
    parsed as JSON at call time.
    """
    root = schema
    properties: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or ())

    params: list[inspect.Parameter] = []
    for name, prop in properties.items():
        prop = _resolve_ref(prop, root)
        annotation = _python_type(prop, root)
        if name in required:
            default: Any = typer.Argument(..., help=prop.get("description"))
        else:
            default = _option_decl(name, prop, root)
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=annotation,
                default=default,
            )
        )
    return inspect.Signature(params)


CTX_PARAM = "_cli_ctx"
JSON_PARAM = "_cli_json"


def _command_signature(schema: dict[str, Any]) -> inspect.Signature:
    """Return the tool's schema signature plus the CLI's own context and flag.

    The context parameter is injected first so Typer binds it positionally; the
    ``--json`` option is appended last so it never shifts a tool's positional
    arguments. Typer binds the context by annotation, so its name is free to be
    private. A tool parameter literally named ``json`` would collide with the
    flag, so that one parameter is renamed to ``--json-output``.
    """
    params: list[inspect.Parameter] = [
        inspect.Parameter(
            CTX_PARAM, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context
        )
    ]
    for param in signature_from_schema(schema).parameters.values():
        if param.name == "json":
            param = param.replace(name="json_output")
        params.append(param)
    params.append(
        inspect.Parameter(
            JSON_PARAM,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=bool,
            default=typer.Option(
                False, "--json", help="Print the raw JSON result instead of text."
            ),
        )
    )
    return inspect.Signature(params)


def _coerce_arguments(
    schema: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """Parse JSON-valued object parameters and unwrap repeatable flags."""
    root = schema
    properties: dict[str, Any] = schema.get("properties") or {}
    payload: dict[str, Any] = {}
    for name, value in arguments.items():
        prop = _resolve_ref(properties.get(name, {}), root)
        if _is_object(prop) and isinstance(value, str):
            payload[name] = json.loads(value)
        elif prop.get("type") == "array" and isinstance(value, tuple):
            payload[name] = list(value)
        else:
            payload[name] = value
    return payload


# --- execution ------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run ``coro`` inline, or in a worker thread when a loop is already active."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def run_tool(server: "FastMCP", tool: str, arguments: dict[str, Any]) -> Any:
    """Execute ``tool`` through an in-memory client and return its result."""
    from fastmcp import Client

    async def call() -> Any:
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments)
            return result

    return _run_async(call())


def _text_blocks(result: Any) -> "str | None":
    """Return the result's text content blocks joined, or ``None`` when empty."""
    content = getattr(result, "content", None)
    if not content:
        return None
    texts = [t for t in (getattr(block, "text", None) for block in content) if t is not None]
    return "\n".join(texts) if texts else None


def _render(result: Any, *, json_output: bool = False) -> str:
    """Render a tool result for standard output.

    Text mode is the default and mirrors what an MCP client sees: a tool that
    returns a string prints that string verbatim, so markdown and tables reach
    the terminal unescaped. Structured returns are pretty-printed as JSON.

    ``json_output`` restores the machine-readable envelope — the whole
    ``structured_content`` document, indented — for scripts and ``jq``.
    """
    structured = getattr(result, "structured_content", None)
    text = _text_blocks(result)

    if json_output:
        if structured is not None:
            return json.dumps(structured, indent=2, default=str)
        if text is not None:
            return text
        return str(result)

    # FastMCP wraps a non-dict return as ``{"result": value}``. Unwrap it so a
    # scalar tool prints its bare value rather than a JSON envelope. A tool that
    # genuinely returns ``{"result": ...}`` produces a text block equal to the
    # whole dict, which fails the ``text == value`` check and stays structured.
    if isinstance(structured, dict) and set(structured) == {"result"}:
        value = structured["result"]
        rendered = value if isinstance(value, str) else json.dumps(value, default=str)
        if text == rendered:
            return rendered
        return json.dumps(structured, indent=2, default=str)
    if structured is not None:
        return json.dumps(structured, indent=2, default=str)
    if text is not None:
        return text
    return str(result)


# --- command tree ---------------------------------------------------------


def _wants_json(ctx: "typer.Context | None", leaf_flag: bool) -> bool:
    """Return whether JSON output was requested at the leaf or the root."""
    if leaf_flag:
        return True
    obj = getattr(ctx, "obj", None)
    return bool(isinstance(obj, dict) and obj.get("json"))


def _make_command(
    server: "FastMCP", tool_name: str, schema: dict[str, Any], doc: str | None
) -> "Callable[..., None]":
    """Return a callable whose signature drives Typer for one tool.

    The synthesized signature gains a leading context parameter so the command
    can read the root ``--json`` flag, and a trailing ``--json`` option so the
    flag also works after the tool name. A tool parameter named ``json`` renders
    as ``--json-output`` to keep the flag unambiguous.
    """

    def command(**kwargs: Any) -> None:
        ctx = kwargs.pop(CTX_PARAM, None)
        leaf_flag = bool(kwargs.pop(JSON_PARAM, False))
        # Undo the ``json`` → ``json_output`` rename so the tool receives the
        # parameter name its schema declares.
        if "json" in (schema.get("properties") or {}):
            kwargs["json"] = kwargs.pop("json_output", None)
        payload = _coerce_arguments(schema, kwargs)
        result = run_tool(server, tool_name, payload)
        typer.echo(_render(result, json_output=_wants_json(ctx, leaf_flag)))

    command.__name__ = tool_name.replace("-", "_")
    command.__doc__ = doc or f"Call {tool_name}."
    command.__signature__ = _command_signature(schema)  # type: ignore[attr-defined]
    return command


def build_plugin_group(name: str, server: "FastMCP", tools: "Iterable[Any]") -> typer.Typer:
    """Return a Typer sub-app exposing one command per visible tool."""
    group = typer.Typer(name=name, help=f"Commands for plugin {name}.", no_args_is_help=True)
    for tool in tools:
        group.command(
            name=tool.name,
            help=tool.description,
        )(_make_command(server, tool.name, tool.parameters, tool.description))
    return group


def stub_group(name: str, error: BaseException) -> typer.Typer:
    """Return a command group that reports a plugin's construction failure."""
    group = typer.Typer(name=name, help=f"Plugin {name} failed to load.", no_args_is_help=True)
    message = f"{type(error).__name__}: {error}"

    def _report() -> None:
        typer.echo(f"Plugin {name} is unavailable: {message}", err=True)
        raise typer.Exit(code=1)

    group.command(name="error", help="Report why this plugin is unavailable.")(_report)
    return group


def _plugin_names() -> list[str]:
    """Return installed plugin names for shell completion."""
    from mcp_plugin_gateway.registry import build_registry

    try:
        return build_registry().names()
    except Exception:  # noqa: BLE001 - completion must never fail
        return []


def _tool_names(plugin: str) -> list[str]:
    """Return a plugin's CLI-visible tool names for shell completion."""
    from mcp_plugin_gateway.registry import build_registry
    from mcp_plugin_gateway.server import build_plugin_server, iter_cli_tools

    try:
        registry = build_registry()
        server = build_plugin_server(registry, plugin, hide_cli_tools=False)
        return [tool.name for tool in _run_async(iter_cli_tools(server))]
    except Exception:  # noqa: BLE001 - completion must never fail
        return []


def _complete_plugin(incomplete: str) -> list[str]:
    return [n for n in _plugin_names() if n.startswith(incomplete)]


class PluginSelectorGroup(TyperGroup):
    """Root group that lets a lone plugin's tools run without its name.

    ``mcp-plugin-gateway greet Ada`` is rewritten to ``mcp-plugin-gateway alpha greet
    Ada`` when ``alpha`` is the only installed plugin. Known commands (``mcp``,
    ``serve``, plugin names) and options are never rewritten, so explicit
    invocations and completion flags keep working. With two or more plugins the
    registry's resolver raises an ambiguity error instead.
    """

    registry: "PluginRegistry | None" = None

    def resolve_command(
        self, ctx: "typer.Context", args: "list[str]"
    ) -> "tuple[str | None, Any, list[str]]":
        args = self._select_plugin(ctx, args)
        return super().resolve_command(ctx, args)

    def _select_plugin(self, ctx: "typer.Context", args: "list[str]") -> "list[str]":
        """Prepend the resolved plugin name when the first token is a tool."""
        registry = self.registry
        if registry is None or not args or ctx.resilient_parsing:
            return args
        first = args[0]
        if first in self.commands or _split_opt(first)[0]:
            return args
        try:
            spec = registry.resolve()
        except PluginError as exc:
            ctx.fail(str(exc))
        return [spec.name, *args]


def _complete_tool(ctx: "typer.Context", incomplete: str) -> list[str]:
    plugin = ctx.params.get("plugin")
    if not plugin:
        return []
    return [n for n in _tool_names(plugin) if n.startswith(incomplete)]


def build_cli(registry: "PluginRegistry") -> typer.Typer:
    """Return a Typer app with one command group per plugin.

    A plugin whose server cannot be constructed registers as a stub group, so one
    broken plugin never hides the healthy ones from ``--help``.

    The app also carries the built-in ``llm`` and ``authoring`` commands, which
    describe the install and the plugin contract to an agent. Their content is
    collected here, in the same pass that constructs the plugins, so the
    documents and the command tree cannot disagree.
    """
    from mcp_plugin_gateway.context import PluginEntry, tool_entry
    from mcp_plugin_gateway.server import build_plugin_server, is_cli_visible, iter_all_tools

    # ``Typer(cls=...)`` takes a class, so bind the registry on a subclass
    # rather than on the shared ``PluginSelectorGroup``.
    group_cls = type(
        "_RegistryPluginSelectorGroup", (PluginSelectorGroup,), {"registry": registry}
    )

    app = typer.Typer(
        name="mcp_plugin_gateway",
        help="Run plugin tools from the command line.",
        no_args_is_help=True,
        add_completion=True,
        cls=group_cls,
    )

    @app.callback()
    def _root(
        ctx: typer.Context,
        json_output: bool = typer.Option(
            False, "--json", help="Print raw JSON results instead of text."
        ),
    ) -> None:
        """Run plugin tools from the command line."""
        ctx.obj = {"json": json_output}

    def _serve(
        plugin: "str | None" = typer.Option(
            None, "--plugin", help="Serve only this plugin instead of all of them."
        ),
        skip: "list[str]" = typer.Option(
            [], "--skip-plugin", help="Exclude a plugin from the aggregate server."
        ),
    ) -> None:
        """Serve plugins over stdio for MCP clients."""
        from mcp_plugin_gateway.server import (
            build_default_server,
            build_plugin_server,
            serve_stdio,
        )

        if plugin is not None:
            server = build_plugin_server(registry, plugin)
        else:
            server = build_default_server(registry, skip=skip)
        serve_stdio(server)

    app.command("mcp", help="Serve plugins over stdio for MCP clients.")(_serve)
    app.command("serve", help="Alias for 'mcp'.")(_serve)

    # The usage entries are built in the same pass that constructs each plugin's
    # command group, so a plugin's factory runs once per process and the usage
    # document and ``--help`` cannot disagree about which plugins are healthy.
    usage_entries: list[PluginEntry] = []
    reserved = _register_context_commands(app, registry, usage_entries)

    for spec in registry:
        try:
            server = build_plugin_server(registry, spec.name, hide_cli_tools=False)
            tools = _run_async(iter_all_tools(server))
        except Exception as exc:  # noqa: BLE001 - isolate per plugin
            app.add_typer(stub_group(spec.name, exc), name=spec.name)
            usage_entries.append(
                PluginEntry(
                    name=spec.name,
                    distribution=spec.distribution,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        entries = tuple(tool_entry(tool) for tool in tools)
        if spec.name in reserved:
            # A plugin that shadows a built-in command is reported rather than
            # registered: silently skipping it would look like a discovery bug.
            typer.echo(
                f"Plugin {spec.name!r} collides with a built-in command name and was "
                f"not registered as a command group. Rename the plugin.",
                err=True,
            )
            usage_entries.append(
                PluginEntry(
                    name=spec.name,
                    distribution=spec.distribution,
                    tools=entries,
                    conflict=f"collides with the built-in command {spec.name!r}",
                )
            )
            continue

        app.add_typer(
            build_plugin_group(
                spec.name, server, [t for t in tools if is_cli_visible(t)]
            ),
            name=spec.name,
        )
        usage_entries.append(
            PluginEntry(
                name=spec.name,
                distribution=spec.distribution,
                tools=entries,
            )
        )
    return app


RESERVED_COMMAND_NAMES = frozenset({"config"})
"""Built-in command names reserved before their command exists.

The rest of the reserved set is derived from the commands the CLI registers.
``config`` is listed here because a separate change adds a ``config`` command
group, and a reserved name that only one of the two changes knows about is a
collision waiting to happen.
"""


def _register_context_commands(
    app: typer.Typer,
    registry: "PluginRegistry",
    usage_entries: "list[PluginEntry]",
) -> "frozenset[str]":
    """Register the built-in context and plugin-authoring commands.

    Returns the reserved command names: the built-in commands this app
    registers, plus the names reserved ahead of their command existing. The set
    is derived rather than hand-kept, so a future built-in command reserves its
    own name automatically.
    """
    from mcp_plugin_gateway.context import (
        authoring_facts,
        render_authoring,
        render_usage,
        usage_facts,
    )

    def _llm(
        ctx: typer.Context,
        plugin: "str | None" = typer.Option(
            None, "--plugin", help="Describe only this plugin instead of all of them."
        ),
        json_output: bool = typer.Option(
            False, "--json", help="Print the document as JSON instead of markdown."
        ),
    ) -> None:
        """Print the installed plugins and their tools."""
        entries = usage_entries
        if plugin is not None:
            # Resolve the selector before rendering: an unknown name is an
            # error with no partial output, so a caller never has to tell
            # "absent" from "broken".
            if plugin not in registry:
                known = ", ".join(registry.names()) or "<none>"
                typer.echo(
                    f"Unknown plugin {plugin!r}. Installed plugins: {known}", err=True
                )
                raise typer.Exit(code=1)
            entries = [entry for entry in entries if entry.name == plugin]

        if _wants_json(ctx, json_output):
            typer.echo(json.dumps(usage_facts(entries), indent=2, default=str))
        else:
            typer.echo(render_usage(entries))

    def _authoring(
        ctx: typer.Context,
        json_output: bool = typer.Option(
            False, "--json", help="Print the document as JSON instead of markdown."
        ),
    ) -> None:
        """Print the contract a plugin must satisfy."""
        if _wants_json(ctx, json_output):
            typer.echo(json.dumps(authoring_facts(), indent=2, default=str))
        else:
            typer.echo(render_authoring())

    def _new_plugin(
        distribution_name: str = typer.Argument(
            ..., help="Distribution name for the new standalone plugin project."
        ),
    ) -> None:
        """Create a standalone plugin project under ./plugins/."""
        from mcp_plugin_gateway.scaffold import scaffold_plugin

        try:
            project = scaffold_plugin(distribution_name)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        except OSError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Created plugin project at {project}")

    app.command("llm", help="Print the installed plugins and their tools.")(_llm)
    app.command("authoring", help="Print the contract a plugin must satisfy.")(_authoring)
    app.command("new-plugin", help="Scaffold a standalone plugin project.")(_new_plugin)

    reserved = {command.name for command in app.registered_commands}
    return frozenset(reserved | RESERVED_COMMAND_NAMES)


def main(argv: "list[str] | None" = None) -> None:
    """Console-script entry point."""
    from mcp_plugin_gateway.registry import build_registry

    app = build_cli(build_registry())
    app(args=argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    main()
