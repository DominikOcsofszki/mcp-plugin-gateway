"""Tests for the container artifacts: Dockerfile and docker-compose.yml.

These assert the properties the specs require of the container build without
needing a Docker daemon: the build runs the settings doctor, the secret is
mounted as a build secret rather than baked into a layer, and the compose file
serves one plugin per service with the secret mounted at ``/run/secrets`` under
the plugin-prefixed name. ``docker compose config`` validation is exercised when
the Docker CLI is available.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _compose() -> str:
    return COMPOSE_FILE.read_text(encoding="utf-8")


# --- 6.4 build-time settings check ---------------------------------------


def test_dockerfile_runs_the_settings_doctor() -> None:
    """The build runs the same check ``doctor`` runs, in container mode."""
    text = _dockerfile()

    assert "python -m mcp_plugin_gateway.settings" in text
    assert "MCP_PLUGIN_GATEWAY_MODE=container" in text


def test_dockerfile_mounts_the_secret_as_a_build_secret() -> None:
    """The secret is a BuildKit mount, so it never lands in an image layer."""
    text = _dockerfile()

    assert "--mount=type=secret" in text
    assert "id=SECURE_PLUGIN_TOKEN" in text
    assert "target=/run/secrets/SECURE_PLUGIN_TOKEN" in text
    # A COPY/ENV of the secret would bake it into the image.
    assert "COPY secrets" not in text
    assert "ENV SECURE_PLUGIN_TOKEN" not in text


def test_dockerfile_does_not_require_the_secret_before_the_check() -> None:
    """``required=true`` would abort before the doctor could name the variable."""
    text = _dockerfile()

    mount_line = next(
        line for line in text.splitlines() if "--mount=type=secret" in line
    )
    assert "required=true" not in mount_line


def test_dockerfile_serves_one_plugin_per_container() -> None:
    text = _dockerfile()

    assert 'ENTRYPOINT ["mcp-plugin-gateway", "mcp"]' in text
    assert '"--plugin"' in text


# --- 6.8 compose: one service per plugin ---------------------------------


def test_compose_declares_one_service_per_plugin() -> None:
    text = _compose()

    assert "example:" in text
    assert "secure-plugin:" in text
    # Two ``command:`` entries, each selecting one plugin.
    assert text.count('command: ["--plugin"') == 2


def test_compose_mounts_secrets_at_run_secrets() -> None:
    text = _compose()

    assert "secrets:" in text
    assert "SECURE_PLUGIN_TOKEN:" in text
    # The secret name carries the plugin prefix, so the settings class reads it.
    assert "secure_plugin_token" not in text


def test_compose_sets_container_mode() -> None:
    text = _compose()

    assert text.count("MCP_PLUGIN_GATEWAY_MODE: container") == 2


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI is not installed"
)
def test_compose_config_validates() -> None:
    """``docker compose config`` parses the file without a daemon."""
    result = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
