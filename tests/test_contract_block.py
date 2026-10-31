"""The ``pyproject.toml`` contract block must agree with the generated contract.

The block is a human-facing copy of the contract; ``mcp-plugin-gateway
authoring`` is the authoritative, generated one. This test keeps the two from
drifting apart on the facts that can change: the entry-point group name and the
permitted surface tags.
"""

from __future__ import annotations

import re
from pathlib import Path

from mcp_plugin_gateway.context import authoring_facts

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _contract_block() -> str:
    """Return the comment block that documents the plugin entry points."""
    text = PYPROJECT.read_text()
    start = text.index("# Plugin entry points")
    return text[start:]


def test_contract_block_states_the_entry_point_group() -> None:
    facts = authoring_facts()

    assert facts["entry_point_group"] in _contract_block()


def test_contract_block_states_every_surface_tag() -> None:
    facts = authoring_facts()

    block = _contract_block()
    for tag in facts["surface_tags"]:
        assert f"``{tag}``" in block, f"contract block omits the {tag!r} surface tag"


def test_contract_block_points_at_the_generated_contract() -> None:
    assert "mcp-plugin-gateway authoring" in _contract_block()


def test_contract_block_does_not_invent_a_group_name() -> None:
    """The block must not name a group other than the real one."""
    facts = authoring_facts()
    block = _contract_block()

    groups = set(re.findall(r"mcp_plugin_gateway\.[a-z_]+", block))

    assert groups == {facts["entry_point_group"]}
