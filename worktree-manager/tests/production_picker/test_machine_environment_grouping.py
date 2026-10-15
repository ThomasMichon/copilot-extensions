"""Physical tab grouping must not change execution-space routing identity."""

from __future__ import annotations

from rich.text import Text
import pytest

from agent_worktrees.config import MachineEntry
from machine_transport import legacy_alias_forms, normalize_machine_entries, parse_machines_yaml
from worktree_manager.production_picker.picker_tui import data_ssh, roster
from worktree_manager.production_picker.picker_tui.engine_rendering import PickerScreenRenderingMixin
from worktree_manager.production_picker.picker_tui.engine_input import PickerScreenInputMixin
from worktree_manager.production_picker.picker_tui.engine_model import PickerScreenModelMixin


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


class _NavigableTabs(PickerScreenInputMixin, PickerScreenModelMixin, _Tabs):
    def __init__(self, sources: list[data_ssh.Source]) -> None:
        super().__init__(sources)
        self.machines.insert(0, ("All", None, None, True))
        self.source_tabs.insert(0, {"label": "All", "source_id": None, "source_kind": "all"})
        self.sel = ("M", 0)
        self.wt_sel = set()
        self.wt_anchor = None
        self.activated: list[str] = []

    def machine_state(self, index: int) -> str:
        return _Tabs.machine_state(self, index)

    def _activate_current_machine_tab(self, prev_key=None, prev_was_all=False) -> None:
        self.activated.append(self._current_tab_key())

    def stops(self) -> list[tuple[str, int]]:
        return [("M", 0)]


@pytest.mark.parametrize("guest_key", ["host-a-wsl", "custom-guest"])
def test_nonadjacent_siblings_share_render_and_keyboard_order_without_routing_changes(
    guest_key: str,
) -> None:
    native = data_ssh.Source("host-a", "Win", ["ssh", "native-a"], alias="native-a")
    other = data_ssh.Source("host-b", "Linux", ["ssh", "other-b"], alias="other-b")
    guest = data_ssh.Source(
        guest_key, "WSL", ["ssh", "guest-a"], alias="guest-a", display_machine="host-a",
        resolve_argv=["resolve", "--machine", guest_key],
    )
    sources = [native, other, guest]
    before = [(source.machine, source.source_id, source.alias, source.argv) for source in sources]
    tabs = _NavigableTabs(sources)
    rendered = tabs.tab_bar(120, False).plain
    assert rendered.count("host-a") == rendered.count("host-b") == 1
    assert rendered.index("host-a") < rendered.index("WSL") < rendered.index("host-b")
    assert [tab["source_id"] for tab in tabs.source_tabs[1:]] == [
        native.source_id, guest.source_id, other.source_id,
    ]
    tabs._rotate_machine(1)
    assert tabs.cur_machine()[:2] == (guest_key, "WSL")
    assert tabs.activated[-1] == guest.source_id
    tabs._rotate_machine(1)
    assert tabs.cur_machine()[:2] == ("host-b", "Linux")
    assert tabs.activated[-1] == other.source_id
    tabs._rotate_machine(-1)
    assert tabs._current_tab_key() == guest.source_id
    assert before == [(source.machine, source.source_id, source.alias, source.argv) for source in sources]
    assert guest.resolve_argv == ["resolve", "--machine", guest_key]


MANAGER_CELLS = [
    (platform, alias)
    for platform in ("windows", "wsl")
    for base in ("Example-Host", "Generated-Host", "Legacy-Alias", "Example Workstation")
    for form in legacy_alias_forms(base, platform)
    for alias in (form, form.swapcase())
]


@pytest.mark.parametrize("platform,alias", MANAGER_CELLS)
def test_manager_local_roster_and_grouping_alias_grid(platform, alias):
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "Example-Host": {
            "hostname": "Generated-Host", "alias": "Legacy-Alias",
            "display_name": "Example Workstation", "ssh": {"environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]},
        },
    }}))
    selected = roster.local_entry(entries, alias, "Generated-Host", platform)
    expected = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    assert selected.key == expected
    assert roster.display_machine(selected) == "Example-Host"
    source = data_ssh.Source(
        selected.key, "WSL" if platform == "wsl" else "Win", None,
        alias=selected.ssh_environments[0].alias, display_machine=roster.display_machine(selected),
    )
    tab, = data_ssh.source_tabs([source])
    assert tab["machine"] == expected and tab["physical_machine"] == "Example-Host"
    assert source.alias == ("guest-profile" if platform == "wsl" else "native-profile")
