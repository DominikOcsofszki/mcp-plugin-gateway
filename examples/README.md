# Example plugin

`example_plugin` exercises all three surface-tag cases:

| Tool | Tags | MCP | CLI |
| --- | --- | --- | --- |
| `greet` | *(none)* | visible | visible |
| `convert` | *(none)* | visible | visible |
| `local_status` | `cli` | hidden | visible |
| `remote_status` | `mcp` | visible | hidden |

## Install and try it

```bash
cd examples/example_plugin
uv pip install -e . --python ../../.venv/bin/python
```

Then, from the repository root:

```bash
.venv/bin/mcp-plugin-gateway --help            # lists "example"
.venv/bin/mcp-plugin-gateway example --help    # greet, convert, local_status
.venv/bin/mcp-plugin-gateway example greet Ada
.venv/bin/mcp-plugin-gateway example convert 100 --unit celsius
.venv/bin/mcp-plugin-gateway example local_status
```

`remote_status` is absent from the CLI listing because it is `mcp`-tagged. An MCP
client connected to `mcp-plugin-gateway mcp` sees `greet`, `convert`, and
`remote_status`, but not `local_status`.

## Secure plugin (container example)

`secure_plugin` reads configuration, so it demonstrates the container contract:
its settings class is instantiated inside `make_plugin()` (never at import), and a
missing `SECURE_PLUGIN_TOKEN` fails construction — which is what makes the
build-time doctor fail the image build.

```bash
cd examples/secure_plugin
uv pip install -e . --python ../../.venv/bin/python
```

Without the secret, the plugin appears as a stub group whose `error` command
reports the validation failure:

```bash
.venv/bin/mcp-plugin-gateway secure-plugin error
```

Provide the secret locally in `~/.config/mcp_plugin_gateway/.env`:

```dotenv
SECURE_PLUGIN_TOKEN=dev-secret-token
```

Or in a container, mount it at `/run/secrets/SECURE_PLUGIN_TOKEN` (see the
repository's `Dockerfile` and `docker-compose.yml`). The file name must be the
plugin-prefixed variable name in upper case; `secure_plugin_token` is ignored.
