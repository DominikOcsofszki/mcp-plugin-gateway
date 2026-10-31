"""Shared fixtures: fake entry points and plugin builders."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from importlib.metadata import EntryPoints
from typing import Any

import pytest
from fastmcp import FastMCP

from mcp_plugin_gateway.discovery import ENTRY_POINT_GROUP


@dataclass
class FakeDist:
    """Minimal stand-in for an installed distribution."""

    name: str


@dataclass
class FakeEntryPoint:
    """Minimal stand-in for ``importlib.metadata.EntryPoint``."""

    name: str
    value: str
    target: Any
    dist: FakeDist | None = None
    group: str = ENTRY_POINT_GROUP

    def load(self) -> Any:
        if isinstance(self.target, BaseException):
            raise self.target
        return self.target

    def matches(self, **params: Any) -> bool:
        """Mirror ``importlib.metadata.EntryPoint.matches``."""
        return all(getattr(self, key) == value for key, value in params.items())


def make_entry_point(
    name: str,
    target: Any,
    *,
    distribution: str | None = None,
    group: str = ENTRY_POINT_GROUP,
    value: str | None = None,
) -> FakeEntryPoint:
    """Build a fake entry point whose ``load()`` returns ``target``."""
    return FakeEntryPoint(
        name=name,
        value=value or f"fake_plugins:{name}",
        target=target,
        dist=FakeDist(distribution) if distribution else None,
        group=group,
    )


def make_entry_points(*entry_points: FakeEntryPoint) -> EntryPoints:
    """Wrap fake entry points in a real ``EntryPoints`` container."""
    return EntryPoints(entry_points)


@dataclass
class PluginHarness:
    """A plugin builder that records construction and lifespan events."""

    events: list[str] = field(default_factory=list)
    name: str = "alpha"

    def build(self) -> FastMCP:
        self.events.append("construct")
        server = FastMCP(self.name, lifespan=self._lifespan)

        @server.tool
        def ping(x: int = 1) -> str:
            return f"pong {x}"

        @server.tool(tags={"cli"})
        def secret() -> str:
            return "secret"

        @server.tool(tags={"mcp"})
        def remote() -> str:
            return "remote"

        @server.prompt
        def greet(name: str) -> str:
            return f"hi {name}"

        @server.resource("data://alpha/value")
        def value() -> str:
            return "V"

        return server

    @contextlib.asynccontextmanager
    async def _lifespan(self, server: FastMCP) -> AsyncGenerator[dict[str, Any], None]:
        self.events.append("startup")
        try:
            yield {"ready": True}
        finally:
            self.events.append("teardown")


@pytest.fixture
def harness() -> PluginHarness:
    """Return a fresh plugin harness."""
    return PluginHarness()


@pytest.fixture
def loader(harness: PluginHarness) -> Callable[[], FastMCP]:
    """Return the harness's plugin builder."""
    return harness.build
