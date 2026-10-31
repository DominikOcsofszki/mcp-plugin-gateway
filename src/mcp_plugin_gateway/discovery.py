"""Plugin discovery through the ``mcp_plugin_gateway.plugins`` entry-point group.

Discovery is the only way plugins enter the system. Each installed distribution
may declare one entry point in the group whose value resolves to a zero-argument
callable returning a single, bare :class:`fastmcp.FastMCP` instance.

Nothing here imports a plugin module or constructs a plugin server: discovery
reads installed-distribution metadata only. The loader returned in each
:class:`PluginSpec` performs the import and the call when a surface first needs
that plugin's components.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP

ENTRY_POINT_GROUP = "mcp_plugin_gateway.plugins"
"""The single entry-point group scanned for plugins."""


class PluginError(Exception):
    """Base class for plugin discovery and contract failures."""


class DuplicatePluginError(PluginError):
    """Two installed distributions claim the same plugin name."""

    def __init__(self, name: str, distributions: tuple[str, ...]) -> None:
        self.name = name
        self.distributions = distributions
        listed = ", ".join(repr(d) for d in distributions)
        super().__init__(
            f"Duplicate plugin name {name!r} declared by multiple distributions: "
            f"{listed}. Uninstall or rename one of them."
        )


class PluginContractError(PluginError):
    """A plugin's entry point did not produce a usable ``FastMCP`` server."""

    def __init__(self, name: str, reason: str, *, distribution: str | None = None) -> None:
        self.name = name
        self.reason = reason
        self.distribution = distribution
        where = f" (from distribution {distribution!r})" if distribution else ""
        super().__init__(f"Plugin {name!r}{where} is unusable: {reason}")


@dataclass(frozen=True)
class PluginSpec:
    """A discovered plugin: its name, lazy loader, and declaring distribution."""

    name: str
    loader: Callable[[], "FastMCP"]
    distribution: str

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PluginSpec(name={self.name!r}, distribution={self.distribution!r})"


def _distribution_name(entry_point: Any) -> str:
    """Return the declaring distribution's name, or a placeholder."""
    dist = getattr(entry_point, "dist", None)
    name = getattr(dist, "name", None)
    if isinstance(name, str) and name:
        return name
    return "<unknown distribution>"


def _make_loader(entry_point: Any, name: str, distribution: str) -> Callable[[], "FastMCP"]:
    """Return a callable that imports and invokes the entry point's target."""

    def loader() -> "FastMCP":
        from fastmcp import FastMCP

        try:
            target = entry_point.load()
        except Exception as exc:  # noqa: BLE001 - reported against the plugin
            raise PluginContractError(
                name,
                f"entry point {entry_point.value!r} could not be imported: {exc}",
                distribution=distribution,
            ) from exc

        if not callable(target):
            raise PluginContractError(
                name,
                f"entry point {entry_point.value!r} resolved to "
                f"{type(target).__name__}, which is not callable",
                distribution=distribution,
            )

        try:
            server = target()
        except Exception as exc:  # noqa: BLE001 - reported against the plugin
            raise PluginContractError(
                name,
                f"callable {entry_point.value!r} raised {type(exc).__name__}: {exc}",
                distribution=distribution,
            ) from exc

        if not isinstance(server, FastMCP):
            raise PluginContractError(
                name,
                f"callable {entry_point.value!r} returned "
                f"{type(server).__name__}, expected a fastmcp.FastMCP instance",
                distribution=distribution,
            )
        return server

    return loader


def _iter_entry_points() -> Any:
    """Return the raw entry points declared in the plugin group."""
    return metadata.entry_points().select(group=ENTRY_POINT_GROUP)


@dataclass(frozen=True)
class DiscoveryResult:
    """The installed plugin list together with its count.

    ``count`` is derived from the same entry-point read that produced
    ``plugins``, so surfaces can branch on the installed plugin count without a
    second scan. ``count`` is ``len(plugins)``: duplicate names are rejected
    during discovery, so every entry point contributes one plugin.
    """

    plugins: dict[str, PluginSpec]

    @property
    def count(self) -> int:
        """Return the number of installed plugins."""
        return len(self.plugins)

    @property
    def names(self) -> list[str]:
        """Return installed plugin names in sorted order."""
        return sorted(self.plugins)

    def __len__(self) -> int:
        return self.count


def discover_plugins_with_count() -> DiscoveryResult:
    """Return discovered plugins and the installed plugin count.

    Reads installed-distribution metadata once: no plugin module is imported and
    no plugin server is constructed. An empty group yields a result with zero
    plugins. Duplicate names raise :class:`DuplicatePluginError` naming every
    declaring distribution.
    """
    return DiscoveryResult(discover_plugins())


def discover_plugins() -> dict[str, PluginSpec]:
    """Return discovered plugins keyed by plugin name.

    Reads installed-distribution metadata only: no plugin module is imported and
    no plugin server is constructed. An empty group yields an empty mapping.
    Duplicate names raise :class:`DuplicatePluginError` naming every declaring
    distribution.
    """
    specs: dict[str, PluginSpec] = {}
    owners: dict[str, list[str]] = {}

    for entry_point in _iter_entry_points():
        name = entry_point.name
        distribution = _distribution_name(entry_point)
        owners.setdefault(name, []).append(distribution)
        if name in specs:
            continue
        specs[name] = PluginSpec(
            name=name,
            loader=_make_loader(entry_point, name, distribution),
            distribution=distribution,
        )

    duplicates = {name: dists for name, dists in owners.items() if len(dists) > 1}
    if duplicates:
        name = sorted(duplicates)[0]
        raise DuplicatePluginError(name, tuple(duplicates[name]))

    return specs


def load_plugin(spec: PluginSpec) -> "FastMCP":
    """Invoke a plugin's loader and validate the returned object."""
    return spec.loader()
