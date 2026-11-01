# mcp-plugin-gateway

A plugin system where **each plugin is a bare [FastMCP](https://github.com/jlowin/fastmcp)
server**. The system discovers installed plugins, mounts them on an aggregate MCP
server, and generates a matching command-line interface from the same tool
schemas. A plugin author writes ordinary FastMCP code and declares one entry
point; nothing else is required.

The distribution and command are named `mcp-plugin-gateway`. The Python import
package is `mcp_plugin_gateway`, and plugins register in the
`mcp_plugin_gateway.plugins` entry-point group.

## The plugin contract

A plugin is a Python distribution that declares exactly one entry point in the
`mcp_plugin_gateway.plugins` group. The entry point's value resolves to a
**zero-argument callable that returns a single `FastMCP` instance**.

```toml
# pyproject.toml of the plugin distribution
[project]
name = "my-plugin"
version = "0.1.0"
dependencies = ["mcp-plugin-gateway", "fastmcp>=4.1.4"]

[project.entry-points."mcp_plugin_gateway.plugins"]
my-plugin = "my_plugin:build_server"
```

```python
# src/my_plugin/__init__.py
from fastmcp import FastMCP


def build_server() -> FastMCP:
    server = FastMCP("my-plugin")

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    return server
```

Install the distribution (`pip install -e .`) and the plugin is discovered. The
callable is invoked lazily: importing the plugin's module and constructing its
server happen when a surface first needs that plugin's components, not at
discovery time. A plugin that needs configuration should therefore read it
inside `build_server()` or inside its own lifespan, not at import time.

### Create a plugin

From the root of your project, scaffold a standalone plugin distribution:

```bash
mcp-plugin-gateway new-plugin my-plugin
```

The command creates `mcp-gateway/my-plugin/` with its metadata, README, and starter
FastMCP server:

```text
mcp-gateway/
└── my-plugin/
    ├── pyproject.toml
    ├── README.md
    └── src/
        └── my_plugin/
            └── __init__.py
```

The distribution name is the plugin's entry-point name; hyphens become
underscores in the Python package name. From the generated project directory,
install it into the **same Python environment that runs the
`mcp-plugin-gateway` console script**:

```bash
cd mcp-gateway/my-plugin
python -m pip install -e .
```

Choose another project-relative destination with `--directory`:

```bash
mcp-plugin-gateway new-plugin my-plugin --directory plugins/my-plugin
```

Verify that the plugin and its starter `hello` tool are discovered, then call
the tool. Its required `name` argument is a positional CLI argument:

```bash
mcp-plugin-gateway llm
mcp-plugin-gateway my-plugin hello Ada
```

To serve plugins for an MCP client, start the stdio server and configure the
client to launch this command with its stdio transport:

```bash
mcp-plugin-gateway mcp
```

### Add MCP to an existing Python project

If the current project has a PEP 621 `pyproject.toml` and exactly one
top-level Python package under `src/` (or the project root), initialize it in
place:

```bash
mpg init
```

The command previews the changes to `pyproject.toml` and the new
`<package>/mcp_server.py`, then asks for confirmation. Declining makes no
changes. For a non-interactive run, review the printed plan and explicitly
approve it with:

```bash
mpg init --yes
```

This registers the existing distribution as a plugin and adds a starter
`hello` tool. It does not create a nested distribution or generate
project-specific tools. An agent can adapt the starter server to expose the
project's functionality. Unsupported or ambiguous layouts are rejected
without changing files.

### Naming rules

* The **entry-point name** is the plugin name. It is used as the namespace on the
  aggregate server (`my-plugin_add`) and as the command group in the CLI.
* Names must be unique across installed distributions. Two distributions
  declaring the same name is an error that names both distributions; uninstall or
  rename one.
* The name should be lowercase and hyphenated, matching the distribution name.
  FastMCP normalizes tool names, so avoid characters that cannot appear in a tool
  name.
* The names of the built-in commands are reserved: `mcp`, `serve`, `llm`,
  `authoring`, `new-plugin`, and `config`. A plugin that claims one of them is
  reported as a conflict and is not registered as a command group, so it cannot
  shadow the built-in command. Rename the plugin.

### Contract rules

* The callable must accept no arguments.
* It must return a `FastMCP` instance. Returning anything else — or raising — is
  reported as a contract error naming the offending plugin and its distribution.
* The returned server should be **bare**: no transport is started and no
  authentication is configured. The system owns transport and mounting.
* An empty `mcp_plugin_gateway.plugins` group is valid and yields no plugins.
  Entry points in other groups are ignored.

### Surface tags

Tools are exposed on two surfaces: MCP clients and the generated CLI. Tags decide
which surface sees a tool.

| Tags on the tool | MCP clients | CLI |
| --- | --- | --- |
| *(none)* | visible | visible |
| `{"mcp"}` | visible | hidden |
| `{"cli"}` | hidden | visible |

```python
@server.tool
def shared(x: int) -> str:
    """Available on both surfaces."""
    return str(x)


@server.tool(tags={"mcp"})
def remote_only() -> str:
    """MCP clients only."""
    return "mcp"


@server.tool(tags={"cli"})
def local_only() -> str:
    """CLI only."""
    return "cli"
```

With the plugin above mounted as `my-plugin`, an MCP client lists `my-plugin_shared`
and `my-plugin_remote_only`, while the CLI offers `my-plugin shared` and
`my-plugin local-only`. Any other tag is rejected at registration with an error
naming the plugin and the tool, so a typo cannot silently change visibility.

`mcp-plugin-gateway authoring` prints this contract as a generated document, so an
agent reading it gets the current rules rather than a copy that can drift.

## Configuration

Configuration is per plugin and lazy. Each plugin gets its own settings class
with an environment prefix derived from its name, so two plugins can use the same
variable name without colliding. See the plugin's own documentation for the
variables it reads.

### Prefix convention

The prefix is the plugin name in upper case followed by an underscore, with `-`
replaced by `_`. Plugin `alpha` reads `ALPHA_*`; plugin `my-plugin` reads
`MY_PLUGIN_*`. A variable without a plugin's prefix is never read by that plugin.

```dotenv
# ~/.config/mcp_plugin_gateway/.env
ALPHA_API_KEY=alpha-key
BETA_API_KEY=beta-key
```

### Local development

Local runs read a single user-level file, `~/.config/mcp_plugin_gateway/.env`. The
path is resolved through your home directory, so the file is genuinely read. A
`.env` shipped inside a plugin's own directory is ignored.

```bash
mkdir -p ~/.config/mcp_plugin_gateway
printf 'ALPHA_API_KEY=alpha-key\n' > ~/.config/mcp_plugin_gateway/.env
```

### Docker secrets

Set `MCP_PLUGIN_GATEWAY_MODE=container` to read settings from `/run/secrets`
instead of the user config file. In this mode the `.env` file is not read at all,
so secrets are authoritative. A missing `/run/secrets` directory only warns; the
remaining sources still apply.

Secret file names must carry the plugin prefix in upper case. A file named
`ALPHA_TOKEN` supplies plugin `alpha`'s `token` setting; a file named
`alpha_token` is ignored. Docker secret names are conventionally lower case, so
declare the prefixed name explicitly:

```yaml
services:
  alpha:
    environment:
      MCP_PLUGIN_GATEWAY_MODE: container
    secrets:
      - source: alpha_token
        target: ALPHA_TOKEN
secrets:
  alpha_token:
    file: ./secrets/alpha_token.txt
```

### Precedence

Highest wins:

1. Process environment
2. `.env` file (local mode only)
3. Secrets directory (container mode only)
4. Declared defaults

Local and container modes never configure both file and secrets sources at once,
because pydantic-settings silently prefers the file when both are present.

### Build-time check

The container build verifies configuration so a missing variable fails the build
rather than the first tool call. The check constructs every plugin's server
without executing any tool:

```bash
MCP_PLUGIN_GATEWAY_MODE=container python -m mcp_plugin_gateway.settings
```

It prints one line per plugin and exits non-zero if any plugin fails to build.

### Container image and Compose

The repository ships a `Dockerfile` and a `docker-compose.yml`. The image runs the
build-time check above as a build step, so a missing secret fails `docker build`
instead of the first tool call. The secret is mounted as a BuildKit secret, so it
never lands in an image layer:

```bash
docker build \
  --secret id=SECURE_PLUGIN_TOKEN,src=./secrets/SECURE_PLUGIN_TOKEN \
  -t mcp-plugin-gateway:local .
```

Building without that secret fails on purpose and names the missing variable:

```bash
docker build .   # exits non-zero: "token ... Field required"
```

`docker-compose.yml` serves each plugin as its own MCP service, so a plugin whose
settings are broken cannot take down the others. Every service runs the same image
with a different `--plugin` argument and reads its secret from `/run/secrets`:

```bash
docker compose config   # validate
docker compose up -d    # start one container per plugin
```

The secret file name must be the plugin-prefixed variable name in upper case
(`SECURE_PLUGIN_TOKEN` for plugin `secure-plugin`); a lowercase name is ignored.
See `examples/README.md` for the two example plugins and how to call them.

## Usage

```bash
mcp-plugin-gateway --help            # list discovered plugins
mcp-plugin-gateway my-plugin --help  # list that plugin's CLI-visible tools
mcp-plugin-gateway my-plugin add 1   # call a tool; defaulted params are options
mcp-plugin-gateway my-plugin add 1 --b 2
```

Commands are generated from each tool's input schema, so no CLI code is written
by hand. The mapping is:

| Schema | CLI |
| --- | --- |
| required parameter | positional argument |
| parameter with a default | named option (`--limit 10`) |
| enum | option validated against the choices |
| array | repeatable flag (`--items a --items b`) |
| boolean | paired flags (`--loud/--no-loud`) |
| nested object | single JSON option (`--o '{"name": "x"}'`) |

Underscores in parameter names become dashes: `max_items` is `--max-items`.

Every plugin's commands sit beneath a plugin-named group, even when the plugin
has a single tool. `mcp-plugin-gateway alpha ping` stays valid when `alpha` gains a
second tool.

A plugin whose settings are invalid does not break the CLI. It appears as a stub
group whose invocation reports the error, so `--help` still lists healthy
plugins.

### Output format

Results print as human-readable text by default, matching what an MCP client
sees. A tool that returns a string prints it verbatim, so markdown and tables
reach the terminal unescaped; a scalar prints its bare value; a structured
result prints as indented JSON.

Pass `--json` for the machine-readable envelope, either before the plugin name
or after the tool name:

```bash
mcp-plugin-gateway my-plugin greet Ada          # Hello, Ada!
mcp-plugin-gateway my-plugin greet Ada --json   # {"result": "Hello, Ada!"}
mcp-plugin-gateway --json my-plugin greet Ada   # same
```

The flag is consumed by the CLI and never forwarded to the tool.

### Shell completion

```bash
mcp-plugin-gateway --install-completion bash   # install for the current shell
mcp-plugin-gateway --show-completion bash      # print the script instead
```

Completion is dynamic: plugin names come from installed entry points and tool
names from the selected plugin's CLI-visible tools. A plugin's `mcp`-tagged tools
are not offered, because the CLI cannot call them.

### Serving over MCP

```bash
mcp-plugin-gateway serve             # serve all plugins over stdio
```

## Context for LLMs

Two commands print documents meant to be read by an agent rather than by a
person. Both are generated at read time from live introspection, so neither can
describe an install that no longer exists.

```bash
mcp-plugin-gateway llm               # the installed plugins and their tools
mcp-plugin-gateway authoring         # the contract a plugin must satisfy
```

`llm` describes the *install*: every installed plugin, its tools, each tool's
parameters, and the surface each tool is reachable from. It is the authoritative
answer to "what can I call here?", and it is generated from the same
introspection the CLI and MCP surfaces are built from, so it cannot drift from
them.

The Markdown view keeps parameter summaries concise while showing enum choices,
nested object fields, array item types, and declared result shapes. The JSON
view is lossless: each tool's `parameters` field contains its complete input
schema, and `output_schema` contains its complete output schema or `null` when
none is declared.

```text
# Installed plugins

1 plugin(s) installed.

## my-plugin

- Distribution: `my-plugin`

### add

Add two integers.

- Surfaces: cli, mcp
- Parameters:
  - `a` (integer, required)
  - `b` (integer, required)
```

Narrow the document to one plugin with `--plugin`. An unknown name exits
non-zero and prints no document, so a caller never has to tell "absent" from
"broken":

```bash
mcp-plugin-gateway llm --plugin my-plugin
```

A plugin that cannot be constructed is listed with its failure reason instead of
being dropped, and the healthy plugins are still described. One broken plugin
never hides the rest.

`authoring` describes the *contract*: the entry-point group, the required
callable shape, the surface tags, and a worked example. It is independent of
what is installed, so it stays complete in an environment with no plugins at
all. The example it shows is the same source the test suite installs and runs,
so it cannot drift from what actually works.

Both commands print markdown by default and a JSON document on request, with
`--json` accepted before or after the command name:

```bash
mcp-plugin-gateway llm --json         # {"plugins": [...]}
mcp-plugin-gateway authoring --json   # {"entry_point_group": "...", ...}
```

## Standalone plugin distributions

A plugin distribution can be the *only* thing installed in an environment. It
depends on `mcp-plugin-gateway`, declares its own `mcp_plugin_gateway.plugins` entry
point, and ships the `mcp-plugin-gateway` console script as a transitive
dependency. Users install one distribution and get both surfaces for its tools —
no host project, no extra wiring.

```toml
# pyproject.toml of the standalone plugin distribution
[project]
name = "my-plugin"
version = "0.1.0"
dependencies = ["mcp-plugin-gateway", "fastmcp"]

[project.entry-points."mcp_plugin_gateway.plugins"]
my-plugin = "my_plugin:build_server"
```

```bash
pip install my-plugin
mcp-plugin-gateway --help        # lists the my-plugin group
mcp-plugin-gateway add 1 2       # plugin name omitted: only one plugin installed
mcp-plugin-gateway mcp           # serves my-plugin's tools without a prefix
```

### Unqualified versus namespaced tool names

The plugin name is optional when exactly one plugin is installed. The surfaces
resolve the lone plugin and drop its namespace:

| Installed plugins | CLI | MCP tool names |
| --- | --- | --- |
| one | `mcp-plugin-gateway add 1 2` | `add`, `ping` |
| two or more | `mcp-plugin-gateway my-plugin add 1 2` | `my-plugin_add`, `other_ping` |

With two or more plugins installed the name is required. An unnamed invocation
exits non-zero and lists the installed plugin names instead of guessing:

```text
Ambiguous plugin selection: 2 plugins are installed (my-plugin, other-plugin).
Name one explicitly, for example 'mcp-plugin-gateway my-plugin ...'.
```

The explicit form always works, in both environments, so an invocation written
for a multi-plugin environment keeps working when it is the only plugin:

```bash
mcp-plugin-gateway my-plugin add 1 2   # same result as the unnamed form
mcp-plugin-gateway mcp --plugin my-plugin   # serve only this plugin, unqualified
```

`mcp-plugin-gateway mcp` serves the aggregate server — namespaced tool names — only
when two or more plugins are installed. Naming a plugin with `--plugin` always
serves that plugin unqualified.

Entry points are read from the active interpreter's installed metadata. A plugin
installed into a *different* virtual environment is invisible, so install the
plugin into the environment that runs the console script.

## Development

```bash
uv pip install -e . --python .venv/bin/python
uv pip install --group dev --python .venv/bin/python
.venv/bin/python -m pytest
```

The example plugins under `examples/` are separate distributions. Install them to
see the surfaces in action; see `examples/README.md`.
