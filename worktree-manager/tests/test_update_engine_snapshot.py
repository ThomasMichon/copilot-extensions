"""Historical snapshots prove ownership but never supply a repair installer."""

import json
import os
import shutil

import pytest
from test_update_engine_repair import repair_case as shared_repair_case

repair_case = shared_repair_case
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real snapshot-source and native lifecycle lane; not promotion-unit evidence",
)


@pytest.mark.parametrize("state", ["intact", "corrupt-installer", "missing-snapshot"])
def test_snapshot_bound_repair_executes_only_refreshed_native_source(repair_case, state):
    case = repair_case
    snapshot = case.runtime / "snapshots/1.0.0"
    shutil.copytree(case.incumbent, snapshot)
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
    receipt = case.binding.publish_legacy_binding(
        case.primitive, payload_root=snapshot, plugin_id="agent-worktrees",
        legacy_root=case.runtime, environment=dict(os.environ),
    )
    manifest = json.loads(case.manifest.read_text())
    manifest["commandBinding"] = receipt
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    script = snapshot / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    if state == "corrupt-installer":
        script.write_text("exit 97\n", encoding="utf-8")
    elif state == "missing-snapshot":
        snapshot.rename(snapshot.with_name("retained-snapshot"))
    assert case.update.repair_engine(case.source) != 0
    assert len(case.acquisitions) == 1
    assert case.effect.is_file()
    command = case.calls[-1][0]
    assert command[command.index("--source-payload-root") + 1] == str(case.incumbent)
    assert str(script) not in command
