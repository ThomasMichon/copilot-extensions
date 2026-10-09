"""Every shipped instruction projection fits the projection manager's budgets.

A template over ``MAX_TEMPLATE_BYTES`` (or a rendered projection over
``MAX_PROJECTION_BYTES``) makes every consuming repository's projection sync and
validation fail with "file is not a bounded regular file", so a plugin must
never ship one. This walks the real plugin payloads in this repository through
the same loader the sync uses.
"""

from __future__ import annotations

import json
import sys
import warnings
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


def test_repository_enabled_stack_audits_the_aggregate_budget(tmp_path: Path) -> None:
    """Audit the real stack this repository enables for its own self-sync
    (``.github/copilot/settings.json``'s ``enabledPlugins``) against this
    repository's own effective aggregate budget (``_load_aggregate_budget``,
    honoring ``.github/copilot/instruction-projections.config.json`` if
    present) -- the exact aggregate check `sync_repository` itself runs
    here, without gating reprojection on aggregate debt. Reads the real
    committed settings rather than the full plugin catalog, since most
    plugins this repo ships are not self-enabled.
    """
    settings = json.loads(
        (REPO / ".github" / "copilot" / "settings.json").read_text(encoding="utf-8")
    )
    enabled_names = {
        origin.split("@", 1)[0]
        for origin, enabled in settings["enabledPlugins"].items()
        if enabled
    }
    enabled_plugins = [plugin for plugin in _PLUGINS if plugin.name in enabled_names]
    assert enabled_plugins, "expected at least one enabled plugin to declare projections"

    result = projections.Result(operation="test")
    total = 0
    for plugin in enabled_plugins:
        source = SimpleNamespace(
            origin=f"copilot-extensions/{plugin.name}",
            payload_root=plugin,
            skills_root=plugin / "skills",
            controlled=False,
            source="",
            version="",
        )
        specs, _unknown = projections._load_specs(tmp_path, [source], result)
        for spec in specs:
            total += projections.render_projection(spec).byte_count

    budget_result = projections.Result(operation="test")
    budget = projections._load_aggregate_budget(REPO, budget_result)
    assert budget_result.blocking == 0, budget_result.findings

    assert result.blocking == 0, result.findings
    if total > budget:
        warnings.warn(
            f"enabled stack totals {total} bytes; the effective aggregate "
            f"budget is {budget} bytes (over by {total - budget} bytes)",
            stacklevel=1,
        )
