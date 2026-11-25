"""Two-volume legacy preservation with explicitly fixture-owned liveness."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, cast

import pytest
import yaml

from agent_worktrees import checkout_relocation_state as state, procs
import test_checkout_relocation as checkout_fixture
import test_legacy_checkout_relocation as legacy_fixture

checkout = checkout_fixture.checkout
legacy = legacy_fixture.legacy


_CHILD_OBSERVATION = """
from types import SimpleNamespace
from agent_worktrees import procs, sessions, tracking
ledger = cfg.tracking_dir("sample") / (sys.argv[1] + ".yaml")
def fixture_probe(record, **kwargs):
    if record.yaml_path != ledger or record.worktree_id != sys.argv[1]:
        raise AssertionError("observation escaped the owned fixture")
    return SimpleNamespace(probes_ok=True, active=False, mux_live=False)
def fixture_blocker(path):
    current = tracking.load_record(ledger)
    allowed = {Path(current.worktree_path), Path(sys.argv[2])}
    journal = move.state.read(ledger)
    if journal:
        source = Path(journal["source"])
        allowed.update((source, Path(journal["destination"]), source.with_name(
            source.name + ".retiring-" + Path(journal["stage"]).name.rsplit("-", 1)[1])))
    if Path(path) not in allowed:
        raise AssertionError("process observation escaped the owned fixture")
    return None
sessions.verify_worktree_active = fixture_probe
procs.checkout_retirement_blocker = fixture_blocker
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows native cross-volume qualification")
@pytest.mark.timeout(300)
def test_native_cross_volume_legacy_component_preserves_original_operation(
    legacy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    import checkout_cross_volume_fixture as fixture_owner

    fixture = legacy
    before = fixture.record.yaml_path.read_bytes()
    monkeypatch.setenv("COPILOT_HOME", str(fixture.copilot))

    def scoped_blocker(path: str) -> str | None:
        allowed = {fixture.source, fixture.destination}
        journal = state.read(fixture.record.yaml_path)
        if journal:
            source = Path(journal["source"])
            allowed.update((source, Path(journal["destination"]), source.with_name(
                source.name + ".retiring-" + Path(journal["stage"]).name.rsplit("-", 1)[1],
            )))
        if Path(path) not in allowed:
            raise AssertionError("process observation escaped the owned fixture")
        return None

    monkeypatch.setattr(procs, "checkout_retirement_blocker", scoped_blocker)
    anchor = 'cfg.set_active_project("sample")\n'
    assert fixture_owner._CRASH.count(anchor) == 1
    monkeypatch.setattr(
        fixture_owner, "_CRASH", fixture_owner._CRASH.replace(anchor, anchor + _CHILD_OBSERVATION),
    )
    qualify = cast(Callable[[SimpleNamespace, pytest.FixtureRequest], None], fixture_owner.qualify)
    qualify(fixture, request)
    assert "creation_nonce" not in yaml.safe_load(before)
    assert "creation_nonce" not in yaml.safe_load(fixture.record.yaml_path.read_bytes())
    report = state.read(fixture.record.yaml_path)
    assert report is not None and report["phase"] == "complete"
    assert report["schema"] == state.LEGACY_SCHEMA
