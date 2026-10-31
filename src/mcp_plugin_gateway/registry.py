"""Plugin registry: duplicate detection, contract validation, and lookup.

The registry holds :class:`~mcp_plugin_gateway.discovery.PluginSpec` entries. It
never constructs a plugin server; construction happens when a surface first asks
for that plugin's components.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING

from mcp_plugin_gateway.discovery import (
    ENTRY_POINT_GROUP,
    DuplicatePluginError,
    PluginError,
    PluginSpec,
    discover_plugins,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP


class AmbiguousPluginError(PluginError):
    """A plugin name is required because several plugins are installed."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = tuple(names)
        listed = ", ".join(self.names)
        example = self.names[0]
        super().__init__(
            f"Ambiguous plugin selection: {len(self.names)} plugins are installed "
            f"({listed}). Name one explicitly, for example "
            f"'mcp-plugin-gateway {example} ...'."
        )


class NoPluginsError(PluginError):
    """No plugin is installed in the active environment."""

    def __init__(self, environment: str | None = None) -> None:
        self.environment = environment or sys.prefix
        super().__init__(
            f"No plugins are installed in this environment ({self.environment}). "
            f"Install a distribution declaring an entry point in the "
            f"{ENTRY_POINT_GROUP!r} group."
        )


class PluginRegistry:
    """Holds discovered plugins and rejects ambiguous or invalid entries."""

    def __init__(self, specs: Iterable[PluginSpec] = ()) -> None:
        self._specs: dict[str, PluginSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: PluginSpec) -> None:
        """Register a plugin, rejecting duplicate names."""
        existing = self._specs.get(spec.name)
        if existing is not None:
            raise DuplicatePluginError(
                spec.name, (existing.distribution, spec.distribution)
            )
        self._specs[spec.name] = spec

    def get(self, name: str) -> PluginSpec:
        """Return the plugin registered under ``name``."""
        try:
            return self._specs[name]
        except KeyError:
            known = ", ".join(self.names()) or "<none>"
            raise KeyError(f"Unknown plugin {name!r}. Installed plugins: {known}") from None

    def names(self) -> list[str]:
        """Return registered plugin names in sorted order."""
        return sorted(self._specs)

    def build(self, name: str) -> "FastMCP":
        """Construct and validate the plugin's ``FastMCP`` server."""
        return self.get(name).loader()

    def resolve(self, name: str | None = None) -> PluginSpec:
        """Return the plugin to use for ``name``, defaulting to a lone plugin.

        An explicit ``name`` always resolves to that plugin. When ``name`` is
        omitted, exactly one installed plugin resolves to itself; two or more
        raise :class:`AmbiguousPluginError` listing every installed plugin name;
        none raises :class:`NoPluginsError`.
        """
        if name is not None:
            return self.get(name)
        names = self.names()
        if len(names) == 1:
            return self._specs[names[0]]
        if not names:
            raise NoPluginsError()
        raise AmbiguousPluginError(names)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __iter__(self) -> Iterator[PluginSpec]:
        for name in self.names():
            yield self._specs[name]

    def __len__(self) -> int:
        return len(self._specs)


def build_registry() -> PluginRegistry:
    """Discover installed plugins and return a populated registry."""
    return PluginRegistry(discover_plugins().values())
