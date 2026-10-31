"""Per-plugin configuration: lazy ``BaseSettings`` with an ``<PLUGIN>_`` prefix.

Two modes share one settings class:

* **local** (default) reads ``~/.config/mcp_plugin_gateway/.env``;
* **container** reads ``/run/secrets`` and omits ``env_file`` so secrets are
  authoritative.

The mode is selected explicitly by ``MCP_PLUGIN_GATEWAY_MODE`` rather than by
configuring both sources, because pydantic-settings silently prefers ``env_file``
over ``secrets_dir`` when both are set.

Nothing here runs at import time. ``settings_class_for`` only builds a class;
``get_settings`` is what reads the environment and validates.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources.providers.secrets import SecretsSettingsSource

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

CONFIG_DIR = Path.home() / ".config" / "mcp_plugin_gateway"
"""User configuration directory; never written as a literal ``~`` path."""

CONFIG_FILE = CONFIG_DIR / ".env"
"""Local-mode configuration file."""

SECRETS_DIR = Path("/run/secrets")
"""Container-mode secrets mount."""

MODE_ENV_VAR = "MCP_PLUGIN_GATEWAY_MODE"
"""Selects ``local`` (default) or ``container`` configuration."""

LOCAL_MODE = "local"
CONTAINER_MODE = "container"


def env_prefix_for(plugin: str) -> str:
    """Return the environment prefix for ``plugin``: upper case, ``-`` → ``_``."""
    return plugin.upper().replace("-", "_") + "_"


def current_mode() -> str:
    """Return the active configuration mode."""
    return os.environ.get(MODE_ENV_VAR, LOCAL_MODE).strip().lower() or LOCAL_MODE


class ExactCaseSecretsSource(SecretsSettingsSource):
    """Secrets source that matches file names case-sensitively.

    pydantic-settings' default lookup ignores case when ``case_sensitive`` is
    false, so a file named ``alpha_token`` would satisfy ``ALPHA_TOKEN``. Docker
    secret names are conventionally lower case, and silently accepting them would
    make the documented prefix convention meaningless. This source looks up the
    upper-cased variable name exactly, so only ``ALPHA_TOKEN`` is read.
    """

    def get_field_value(
        self, field: "FieldInfo", field_name: str
    ) -> tuple[Any, str, bool]:
        for field_key, env_name, value_is_complex in self._extract_field_info(
            field, field_name
        ):
            # ``find_case_path`` compares directory entry names as strings, so this
            # stays exact-case even on a case-insensitive filesystem where
            # ``Path("ALPHA_TOKEN").is_file()`` would match ``alpha_token``.
            for secrets_path in reversed(self.secrets_paths):
                path = self.find_case_path(secrets_path, env_name.upper(), True)
                if path is not None:
                    return (
                        path.read_text(encoding="utf-8").strip(),
                        field_key,
                        value_is_complex,
                    )
        return None, field_name, False


def _customise_sources(
    cls: type[BaseSettings],
    settings_cls: type[BaseSettings],
    init_settings: Any,
    env_settings: Any,
    dotenv_settings: Any,
    file_secret_settings: Any,
) -> tuple[Any, ...]:
    """Order sources: init > process env > ``.env`` > secrets > defaults."""
    return (
        init_settings,
        env_settings,
        dotenv_settings,
        ExactCaseSecretsSource(
            settings_cls, env_prefix=settings_cls.model_config.get("env_prefix")
        ),
    )


@lru_cache(maxsize=None)
def settings_class_for(
    plugin: str, base: type[BaseSettings] | None = None
) -> type[BaseSettings]:
    """Return a ``BaseSettings`` subclass whose ``env_prefix`` is ``<PLUGIN>_``.

    ``base`` supplies the plugin's fields; when omitted an empty settings class is
    returned. The class is cached so repeated lookups reuse one type.
    """
    parent = base or BaseSettings
    prefix = env_prefix_for(plugin)

    if current_mode() == CONTAINER_MODE:
        config = SettingsConfigDict(
            env_prefix=prefix,
            secrets_dir=str(SECRETS_DIR),
            extra="ignore",
        )
    else:
        config = SettingsConfigDict(
            env_prefix=prefix,
            env_file=CONFIG_FILE,
            env_file_encoding="utf-8",
            extra="ignore",
        )

    return type(
        f"{plugin.title().replace('-', '')}Settings",
        (parent,),
        {
            "model_config": config,
            "settings_customise_sources": classmethod(_customise_sources),
        },
    )


_settings_cache: dict[str, BaseSettings] = {}


def get_settings(
    plugin: str, base: type[BaseSettings] | None = None
) -> BaseSettings:
    """Return the cached settings instance for ``plugin``, instantiating lazily."""
    cached = _settings_cache.get(plugin)
    if cached is not None:
        return cached
    instance = settings_class_for(plugin, base)()
    _settings_cache[plugin] = instance
    return instance


def clear_settings_cache() -> None:
    """Drop cached settings instances (used by tests and the doctor command)."""
    _settings_cache.clear()
    cache_clear = getattr(settings_class_for, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()


def doctor() -> dict[str, str]:
    """Construct every plugin and report failures without executing any tool.

    Returns a mapping of plugin name to ``"ok"`` or the error text. A plugin whose
    settings or server cannot be built is reported rather than raised, so one
    broken plugin does not hide the rest. This is the check the container build
    runs, so a missing variable fails the build instead of the first tool call.
    """
    from mcp_plugin_gateway.registry import build_registry

    report: dict[str, str] = {}
    for spec in build_registry():
        try:
            spec.loader()
        except Exception as exc:  # noqa: BLE001 - report, never raise
            report[spec.name] = f"{type(exc).__name__}: {exc}"
        else:
            report[spec.name] = "ok"
    return report


def _main(argv: list[str] | None = None) -> int:
    """Run the build-time configuration check; exit non-zero on any failure."""
    import sys

    report = doctor()
    failures = {name: status for name, status in report.items() if status != "ok"}
    for name in sorted(report):
        print(f"{name}: {report[name]}")
    if not report:
        print("no plugins installed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
