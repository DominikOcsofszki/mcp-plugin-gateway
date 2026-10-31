"""Lazy plugin provider: constructs a plugin's server on first access.

The aggregate server registers one :class:`LazyPluginProvider` per plugin. The
provider holds only the plugin's loader until a surface first asks for that
plugin's components; at that moment it constructs the plugin's ``FastMCP`` and
enters the plugin's own lifespan, so plugin startup work runs and teardown runs
on shutdown.

"Lazy" means lazy until the first ``list_tools()`` (or any other component
access), not until the first tool call. Entering a plugin's lifespan requires
constructing the plugin, and entering it later — from a tool call — would set
context variables in one task and reset them in another, which raises
``ValueError: Token ... was created in a different Context``. So construction and
lifespan entry happen together, at connect time, and the built plugin is reused
for every later call in the same process.

All component types are delegated: tools, prompts, resources, and resource
templates. A tools-only provider would silently drop a plugin's prompts and
resources.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable, Sequence
from typing import TYPE_CHECKING, Any

from fastmcp.server.providers import Provider
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.prompts.base import Prompt
    from fastmcp.resources.base import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.base import Tool
    from fastmcp.utilities.versions import VersionSpec


class LazyPluginProvider(Provider):
    """Provider that builds a plugin's server on first access and delegates to it."""

    def __init__(self, name: str, loader: Callable[[], "FastMCP"]) -> None:
        super().__init__()
        self.name = name
        self._loader = loader
        self._server: "FastMCP | None" = None
        self._inner: FastMCPProvider | None = None
        self._entered = False

    # -- construction -----------------------------------------------------

    @property
    def server(self) -> "FastMCP":
        """Return the plugin's server, constructing it on first access."""
        return self._ensure().server

    @property
    def constructed(self) -> bool:
        """Whether the plugin's server has been constructed."""
        return self._server is not None

    def _ensure(self) -> FastMCPProvider:
        """Construct the plugin and wrap it in a delegating provider."""
        if self._inner is None:
            server = self._loader()
            self._server = server
            self._inner = FastMCPProvider(server)
        return self._inner

    def __repr__(self) -> str:
        state = "constructed" if self.constructed else "lazy"
        return f"LazyPluginProvider(name={self.name!r}, {state})"

    # -- delegation -------------------------------------------------------

    def required_extensions(self) -> Sequence[Any]:
        """Expose the plugin's bundled extensions once it is constructed."""
        if self._inner is None:
            return ()
        return self._inner.required_extensions()

    @contextlib.contextmanager
    def _extension_runtime(self, available: frozenset[str], *, root: "FastMCP | None") -> Any:
        if self._inner is None:
            yield
            return
        with self._inner._extension_runtime(available, root=root):
            yield

    async def _list_tools(self) -> Sequence["Tool"]:
        return list(await self._ensure().list_tools())

    async def _get_tool(
        self, name: str, version: "VersionSpec | None" = None
    ) -> "Tool | None":
        return await self._ensure().get_tool(name, version)

    async def _list_prompts(self) -> Sequence["Prompt"]:
        return list(await self._ensure().list_prompts())

    async def _get_prompt(
        self, name: str, version: "VersionSpec | None" = None
    ) -> "Prompt | None":
        return await self._ensure().get_prompt(name, version)

    async def _list_resources(self) -> Sequence["Resource"]:
        return list(await self._ensure().list_resources())

    async def _get_resource(
        self, uri: str, version: "VersionSpec | None" = None
    ) -> "Resource | None":
        return await self._ensure().get_resource(uri, version)

    async def _list_resource_templates(self) -> Sequence["ResourceTemplate"]:
        return list(await self._ensure().list_resource_templates())

    async def _get_resource_template(
        self, uri: str, version: "VersionSpec | None" = None
    ) -> "ResourceTemplate | None":
        return await self._ensure().get_resource_template(uri, version)

    # -- lifespan ---------------------------------------------------------

    @contextlib.asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        """Construct the plugin and run its lifespan for the server's lifetime."""
        from fastmcp.server.mixins.lifespan import _lifespan_root_active

        server = self._ensure().server
        token = _lifespan_root_active.set(True)
        active = True
        try:
            async with server._lifespan_manager():
                _lifespan_root_active.reset(token)
                active = False
                self._entered = True
                yield
        finally:
            if active:
                _lifespan_root_active.reset(token)
            self._entered = False
