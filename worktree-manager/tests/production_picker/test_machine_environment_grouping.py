"""Physical tab grouping must not change execution-space routing identity."""

from __future__ import annotations

from rich.text import Text

from agent_worktrees.config import MachineEntry
from worktree_manager.production_picker.picker_tui import data_ssh, roster
from worktree_manager.production_picker.picker_tui.engine_rendering import PickerScreenRenderingMixin


class _Tabs(PickerScreenRenderingMixin):
    def __init__(self, sources: list[data_ssh.Source]) -> None:
        self.machines = data_ssh.machines(sources)
        self.source_tabs = data_ssh.source_tabs(sources)
        self.machine_idx = 1

    def _in_configuration(self) -> bool:
        return False

    def machine_state(self, index: int) -> str:
        return "failed"

    def _hl(self, style: str, selected: bool, focused: bool) -> str:
        return style


def test_native_and_guest_share_one_rendered_host_but_keep_distinct_targets() -> None:
    sources = [
        data_ssh.Source("example-host", "Win", None, alias="native-ssh"),
        data_ssh.Source("example-host-wsl", "WSL", None, alias="guest-ssh"),
    ]
    tabs = _Tabs(sources)
    rendered: Text = tabs.tab_bar(120, False)
    assert rendered.plain.count("example-host") == 1
    assert "example-host-wsl" not in rendered.plain
    assert "Win" in rendered.plain and "WSL" in rendered.plain
    assert tabs.source_tabs[0]["machine"] == "example-host"
    assert tabs.source_tabs[1]["machine"] == "example-host-wsl"
    assert sources[0].source_id != sources[1].source_id
    assert (sources[0].alias, sources[1].alias) == ("native-ssh", "guest-ssh")


def test_grouping_respects_explicit_physical_association() -> None:
    entry = MachineEntry(
        key="guest-label", display_name="Guest", physical_host="physical-host",
        execution_platform="wsl",
    )
    assert roster.display_machine(entry) == "physical-host"
    inferred = MachineEntry(key="Example-Host-WSL", display_name="Guest")
    assert roster.display_machine(inferred) == "Example-Host"
