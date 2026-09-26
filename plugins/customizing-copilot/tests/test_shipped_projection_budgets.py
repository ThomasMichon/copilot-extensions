"""Every shipped instruction projection fits the projection manager's budgets.

A template over ``MAX_TEMPLATE_BYTES`` (or a rendered projection over
``MAX_PROJECTION_BYTES``) makes every consuming repository's projection sync and
validation fail with "file is not a bounded regular file", so a plugin must
never ship one. This walks the real plugin payloads in this repository through
the same loader the sync uses.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reviewing-customizations"
    / "scripts"
)
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import instruction_projections as projections

REPO = Path(__file__).resolve().parents[3]
_PLUGINS = sorted(
    path.parent
    for path in (REPO / "plugins").glob("*/instruction-projections.json")
)


def test_repository_ships_projection_declarations() -> None:
    assert _PLUGINS, "expected at least one plugin to declare projections"


@pytest.mark.parametrize("plugin", _PLUGINS, ids=lambda p: p.name)
def test_shipped_projections_fit_the_budgets(plugin: Path, tmp_path: Path) -> None:
    source = SimpleNamespace(
        origin=f"copilot-extensions/{plugin.name}",
        payload_root=plugin,
        skills_root=plugin / "skills",
        controlled=False,
        source="",
        version="",
    )
    result = projections.Result(operation="test")
    specs, _unknown = projections._load_specs(tmp_path, [source], result)

    blocking = [f for f in result.findings if f.severity == projections.BLOCKING]
    assert not blocking, [f"{f.check}: {f.path}: {f.message}" for f in blocking]
    assert specs, f"{plugin.name} declares projections but none loaded"
    for spec in specs:
        assert spec.template_bytes <= projections.MAX_TEMPLATE_BYTES, spec.template
        rendered = projections.render_projection(spec)
        assert rendered.byte_count <= projections.MAX_PROJECTION_BYTES, (
            f"{spec.template} renders to {rendered.byte_count} bytes"
        )
