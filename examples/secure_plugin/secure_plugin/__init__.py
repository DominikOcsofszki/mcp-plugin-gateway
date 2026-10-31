"""Example plugin whose settings require a secret.

Unlike ``example_plugin``, this plugin reads configuration, so it demonstrates
the container contract end to end: the settings class is instantiated inside
``make_plugin`` (never at import), a missing ``SECURE_PLUGIN_TOKEN`` fails
construction, and the build-time doctor therefore fails the image build when the
secret is absent.

The secret file must be named ``SECURE_PLUGIN_TOKEN`` — the plugin name in upper
case with ``-`` replaced by ``_``, followed by an underscore — because the
settings class applies ``env_prefix="SECURE_PLUGIN_"``.
"""

from __future__ import annotations

from fastmcp import FastMCP
from pydantic_settings import BaseSettings

from mcp_plugin_gateway.settings import get_settings

PLUGIN_NAME = "secure-plugin"


class SecureSettings(BaseSettings):
    """Settings for the secure example plugin."""

    token: str


def make_plugin() -> FastMCP:
    """Return the secure plugin's server, reading settings lazily."""
    # Instantiated here, not at import: importing this module must not read
    # configuration. A missing token raises ``ValidationError`` now, which is
    # what makes the build-time doctor fail.
    settings = get_settings(PLUGIN_NAME, SecureSettings)

    server = FastMCP(PLUGIN_NAME)

    @server.tool
    def whoami() -> str:
        """Report the configured token's length without revealing it."""
        return f"token length: {len(settings.token)}"

    @server.tool(tags={"mcp"})
    def remote_whoami() -> str:
        """Report the configured token's length over MCP only."""
        return f"remote token length: {len(settings.token)}"

    return server
