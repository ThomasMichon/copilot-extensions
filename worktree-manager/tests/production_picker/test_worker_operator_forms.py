"""Workers only exposes explicitly supported, source-owned operator controls."""

import asyncio
import json
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Input, Static, Tab

from worktree_manager.production_picker.picker_tui import pivots
from worktree_manager.production_picker.picker_tui.configuration_form import ConfigurationFormScreen
from worktree_manager.production_picker.picker_tui.field_widgets import field_label
from worktree_manager.production_picker.picker_tui.pivot_create_action import _parse_create_fields

pytestmark = pytest.mark.contract("worktree_manager.configuration_backstage")


def contribution():
    path = Path(__file__).resolve().parents[3] / "plugins" / "agent-dispatch" / "pivots" / "workers-config.json"
    return pivots.parse_manifest(json.loads(path.read_text()), name="workers", source_path=str(path))


@pytest.mark.parametrize("capability,version", [
    (None, None), ("false", "1"), ("true", None), ("true", "2"), ("true", "1"),
])
def test_new_payload_does_not_send_safe_save_syntax_to_legacy_owner(capability, version):
    row = {"id": "template:fixture", "instantiable": "true", "editable": "true"}
    if capability is not None:
        row["operator_instantiable"] = capability
    if version is not None:
        row["operator_contract_version"] = version
    actions = [action for action in contribution().actions if pivots.entry_matches(action.when, row)]
    assert [action.key for action in actions] == (
        ["details", "instantiate-save", "instantiate-activate"]
        if capability == "true" and version == "1" else ["details"]
    )
    for action in actions:
        if action.form:
            assert action.form["fields_from"] == "operator_fields"
            assert "--operator-inputs" in action.run


@pytest.mark.parametrize("change", [{"label": ""}, {"label": "two\nlines"}, {"description": 42}])
def test_configuration_captions_are_validated(change):
    with pytest.raises(pivots.ManifestError):
        _parse_create_fields([{"name": "param_label", "type": "text", **change}], where="fixture")


@pytest.mark.parametrize("tab_label", ["[bold]Queue[/bold]", "[unclosed"])
def test_native_configuration_preserves_owner_captions_and_collects_only_parameter_fields(tab_label):
    fields = list(_parse_create_fields([{
        "name": "param_label", "type": "text", "label": "Watched task label",
        "description": "Only matching queued tasks are eligible. [Source-owned]",
        "default": "fixture",
    }, {
        "name": "param_queue", "type": "text", "label": tab_label,
        "description": "Source-owned queue selector.", "default": "queue",
    }], where="fixture", dynamic_other=False))
    assert field_label(fields[0]) == "Watched task label"
    assert field_label({"name": "old_field"}) == "Old field"

    class Fixture(App):
        def on_mount(self):
            self.push_screen(ConfigurationFormScreen("Task worker pool", fields, confirm_values=True))

    async def run():
        app = Fixture()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ConfigurationFormScreen)
            captions = [str(widget.content) for widget in screen.query(Static)]
            assert any("Watched task label" in caption and "[Source-owned]" in caption for caption in captions)
            assert [tab.label.plain for tab in screen.query(Tab)] == ["Watched task label", tab_label]
            assert screen.query_one("#q-0", Input).value == "fixture"
            screen.query_one("#q-0", Input).value = "changed"
            assert screen._collect() == {"param_label": "changed", "param_queue": "queue"}
            screen._show_confirm_prompt()
            await pilot.pause()
            assert "Watched task label: changed" in str(screen.query_one("#create-confirm-prompt", Static).content)

    asyncio.run(run())
