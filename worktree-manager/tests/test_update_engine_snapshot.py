"""Historical snapshots prove ownership but never supply a repair installer."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_update_engine_repair import _copy_fixture_tree, repair_case as shared_repair_case

repair_case = shared_repair_case
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real snapshot-source and native lifecycle lane; not promotion-unit evidence",
)


def _historical_snapshot(case: SimpleNamespace) -> Path:
    snapshot = case.runtime / "snapshots/1.0.0"
    _copy_fixture_tree(case.incumbent, snapshot)
    resolution = case.primitive.resolve_context(
        payload_root=case.incumbent, plugin_id="agent-worktrees",
        durable_home=case.home / ".copilot-extensions",
    )
    (snapshot / "snapshot-provenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.legacy-snapshot-source", "version": 1,
        "resolution": resolution,
    }), encoding="utf-8")
    (case.runtime / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (case.runtime / "stamped-version").write_text("1.0.0", encoding="utf-8")
    return snapshot


@pytest.mark.parametrize("state", ["intact", "corrupt-installer", "missing-snapshot"])
@pytest.mark.parametrize("ownership", ["binding", "legacy-locator"])
def test_snapshot_bound_repair_executes_only_refreshed_native_source(
    repair_case: SimpleNamespace, state: str, ownership: str,
) -> None:
    case = repair_case
    snapshot = _historical_snapshot(case)
    receipt = case.binding.publish_legacy_binding(
        case.primitive, payload_root=snapshot, plugin_id="agent-worktrees",
        legacy_root=case.runtime, environment=dict(os.environ),
    )
    manifest = json.loads(case.manifest.read_text())
    if ownership == "binding":
        manifest["commandBinding"] = receipt
    else:
        manifest.pop("commandBinding")
        manifest["source"] = {"kind": "snapshot", "path": str(snapshot)}
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    script = snapshot / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    if state == "corrupt-installer":
        script.write_text("exit 97\n", encoding="utf-8")
    elif state == "missing-snapshot":
        snapshot.rename(snapshot.with_name("retained-snapshot"))
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    if ownership == "legacy-locator" and state == "missing-snapshot":
        assert not case.acquisitions and not case.calls and not case.effect.exists()
        assert case.manifest.read_bytes() == before
        return
    assert len(case.acquisitions) == 1
    assert case.effect.is_file()
    command = case.calls[-1][0]
    assert command[command.index("--source-payload-root") + 1] == str(case.incumbent)
    assert str(script) not in command


def test_unbound_snapshot_with_invalid_provenance_refuses_before_acquisition(
    repair_case: SimpleNamespace,
) -> None:
    case = repair_case
    snapshot = _historical_snapshot(case)
    (snapshot / "snapshot-provenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.legacy-snapshot-source", "version": 1, "resolution": {},
    }), encoding="utf-8")
    manifest = json.loads(case.manifest.read_text())
    manifest.pop("commandBinding")
    manifest["source"] = {"kind": "snapshot", "path": str(snapshot)}
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert not case.acquisitions and not case.calls and not case.effect.exists()
    assert case.manifest.read_bytes() == before
