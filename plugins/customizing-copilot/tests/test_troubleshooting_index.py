"""Guard test: every declared troubleshooting category has an ambient pointer.

Parallel to
``plugins/copilot-extensions-harness/tests/test_session_context_declarations.py``'s
``test_dynamic_pointer_projections_have_exact_session_writers`` -- this scans
each plugin's *static* instruction-projection templates (not skills, not a
consumer repo's synced copies) and asserts every category a plugin's
``troubleshooting-index.json`` claims is actually backed by a matching marker
string somewhere in that ambient content. A category that is claimed but
unindexed is a "dead end" in the ``ambient-guidance-navigability`` audit's own
vocabulary: content reachable only by already knowing the right skill to
invoke.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reviewing-customizations"
    / "scripts"
)
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import troubleshooting_index as tix  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
PLUGINS = REPO / "plugins"


def _write_projected_plugin(
    root: Path,
    name: str,
    *,
    template_body: str,
    categories: list[dict],
) -> Path:
    """Build a synthetic plugin with a static projection + a
    troubleshooting-index.json, mirroring the real on-disk shape."""
    plugin = root / name
    template = plugin / "instructions" / "fallback.instructions.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(template_body, encoding="utf-8")
    (plugin / "instruction-projections.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.instruction-projections",
                "version": 1,
                "projections": [
                    {
                        "id": "fallback",
                        "template": "instructions/fallback.instructions.md",
                        "destination": f".github/instructions/{name}/fallback.instructions.md",
                        "customizationKind": "instructions",
                        "applyTo": "**",
                        "legacyMarkers": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (plugin / tix.DECLARATION_FILE).write_text(
        json.dumps(
            {
                "schema": tix.DECLARATION_SCHEMA,
                "version": tix.DECLARATION_VERSION,
                "categories": categories,
            }
        ),
        encoding="utf-8",
    )
    return plugin


def test_uncovered_category_is_reported(tmp_path: Path) -> None:
    plugin = _write_projected_plugin(
        tmp_path,
        "synthetic-plugin",
        template_body="# Fallback\n\nNothing relevant here.\n",
        categories=[
            {
                "id": "widget-jam",
                "summary": "The widget jammed.",
                "pointer": "`widget-tool unjam`",
                "markers": ["widget-jam", "widget-tool unjam"],
            }
        ],
    )

    uncovered = tix.uncovered_categories(plugin)

    assert [c.id for c in uncovered] == ["widget-jam"]


def test_covered_category_is_not_reported(tmp_path: Path) -> None:
    plugin = _write_projected_plugin(
        tmp_path,
        "synthetic-plugin",
        template_body=(
            "# Fallback\n\nIf a widget-jam occurs, run `widget-tool unjam`.\n"
        ),
        categories=[
            {
                "id": "widget-jam",
                "summary": "The widget jammed.",
                "pointer": "`widget-tool unjam`",
                "markers": ["widget-jam", "widget-tool unjam"],
            }
        ],
    )

    assert tix.uncovered_categories(plugin) == []


def test_plugin_without_declaration_reports_nothing(tmp_path: Path) -> None:
    plugin = tmp_path / "plain-plugin"
    plugin.mkdir()
    assert tix.uncovered_categories(plugin) == []


def test_declared_category_with_no_static_projections_is_uncovered(
    tmp_path: Path,
) -> None:
    # A plugin can declare troubleshooting-index.json without shipping any
    # instruction-projections.json at all -- that must fail closed (every
    # category uncovered), not silently pass because there's nothing to scan.
    plugin = tmp_path / "unprojected-plugin"
    plugin.mkdir()
    (plugin / tix.DECLARATION_FILE).write_text(
        json.dumps(
            {
                "schema": tix.DECLARATION_SCHEMA,
                "version": tix.DECLARATION_VERSION,
                "categories": [
                    {
                        "id": "widget-jam",
                        "summary": "The widget jammed.",
                        "pointer": "`widget-tool unjam`",
                        "markers": ["widget-jam"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    uncovered = tix.uncovered_categories(plugin)

    assert [c.id for c in uncovered] == ["widget-jam"]


@pytest.mark.parametrize(
    "declaration",
    [
        {"schema": "wrong-schema", "version": 1, "categories": [{}]},
        {"schema": tix.DECLARATION_SCHEMA, "version": 2, "categories": [{}]},
        {"schema": tix.DECLARATION_SCHEMA, "version": 1, "categories": []},
        {
            "schema": tix.DECLARATION_SCHEMA,
            "version": 1,
            "categories": [{"id": "a", "summary": "x", "pointer": "y", "markers": []}],
        },
        {
            "schema": tix.DECLARATION_SCHEMA,
            "version": 1,
            "categories": [
                {"id": "a", "summary": "x", "pointer": "y", "markers": ["m"]},
                {"id": "a", "summary": "x2", "pointer": "y2", "markers": ["m2"]},
            ],
        },
    ],
)
def test_malformed_declaration_fails_closed(tmp_path: Path, declaration: dict) -> None:
    path = tmp_path / tix.DECLARATION_FILE
    path.write_text(json.dumps(declaration), encoding="utf-8")

    with pytest.raises(tix.TroubleshootingIndexError):
        tix.load_declaration(path)


def test_every_real_plugin_covers_its_declared_categories() -> None:
    """The actual repository-wide guard: any plugin that opts into the
    troubleshooting-index registry must have zero uncovered categories."""
    for plugin in tix.iter_plugin_roots(PLUGINS):
        uncovered = tix.uncovered_categories(plugin)
        assert uncovered == [], (
            f"{plugin.name} declares troubleshooting categories with no "
            f"ambient pointer: {[c.id for c in uncovered]}"
        )
