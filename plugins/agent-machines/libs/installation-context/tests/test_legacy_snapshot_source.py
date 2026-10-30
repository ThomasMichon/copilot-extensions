"""Frozen snapshot source evidence never replaces live publication policy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_installation_mode_governance import ALL_RUNNERS, _environment, _load_module, _write_json
from test_runtime_deployment_adapters import _run_adapter


@pytest.mark.parametrize("runner", ALL_RUNNERS)
@pytest.mark.parametrize(("receipt_version", "case"), [
    (version, case) for version in (1, 2) for case in (
        "standalone", "directory", "managed", "fingerprint", "source", "plugin", "version",
        "selected", "stamped", "record", "version-array",
    )
] + [(2, case) for case in (
    "snapshot-id", "payload-version", "id-array", "payload-version-array", "missing-selection",
)])
def test_snapshot_source_revalidates_identity_and_live_policy(
    tmp_path: Path, runner: str, case: str, receipt_version: int,
) -> None:
    module = _load_module(native_environment=True)
    publisher = tmp_path / "publisher"
    publisher.mkdir()
    durable = tmp_path / "durable"
    captured = module.resolve_context(
        payload_root=publisher, plugin_id="agent-example", durable_home=durable,
        source_descriptor=(
            {"source": "directory", "path": str(publisher)}
            if case == "directory" else {"source": "github", "repo": "Example/Plugins"}
        ),
        marketplace_key="example", environment={},
    )
    root = tmp_path / "runtime"
    snapshot = root / "snapshots" / ("1.2.3-20261021T000000-42" if receipt_version == 2 else "1.2.3")
    _write_json(snapshot / "plugin.json", {"name": "agent-example", "version": "1.2.3"})
    (root / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (root / "stamped-version").write_text("1.2.3", encoding="utf-8")
    receipt = {
        "schema": "copilot-extensions.legacy-snapshot-source", "version": receipt_version,
        "resolution": captured,
    }
    if receipt_version == 2:
        receipt.update(snapshotId=snapshot.name, payloadVersion="1.2.3")
    path = snapshot / "snapshot-provenance.json"
    _write_json(path, receipt)
    publisher.rmdir()
    current = _environment()
    arguments = {
        "payload_root": snapshot, "plugin_id": "agent-example", "legacy_root": root,
        "durable_home": durable, "policy_path": tmp_path / "missing-mode-policy.json",
        "os_profile": Path(current["homeRealPath"]), "platform": current["platform"],
        "wsl_distro": current["wslDistro"], "environment": {},
    }
    initial = module.resolve_runtime_deployment(**arguments)
    _write_json(root / "runtime-deployment.json", {
        "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
        "owner": module._deployment_owner(initial, arguments), "managed": case == "managed",
    })
    if case == "fingerprint":
        captured["sourceFingerprint"] = "sha256:" + "0" * 64
    elif case == "source":
        captured["source"]["canonical"] = "github:another/plugins"
    elif case == "plugin":
        captured["pluginId"] = "agent-other"
    elif case == "version":
        receipt["version"] = True
    elif case == "version-array":
        receipt["version"] = [receipt_version]
    elif case == "selected":
        (root / "payload-dir").write_text(str(root / "snapshots/other"), encoding="utf-8")
    elif case == "stamped":
        (root / "stamped-version").write_text("9.8.7", encoding="utf-8")
    elif case == "record":
        captured["source"]["record"] = "wrong"
    elif case == "snapshot-id":
        receipt["snapshotId"] = "other-snapshot"
    elif case == "payload-version":
        receipt["payloadVersion"] = "9.8.7"
    elif case == "id-array":
        receipt["snapshotId"] = [snapshot.name]
    elif case == "payload-version-array":
        receipt["payloadVersion"] = ["1.2.3"]
    elif case == "missing-selection":
        receipt.pop("snapshotId")
    _write_json(path, receipt)
    before = {str(entry): entry.read_bytes() for entry in root.rglob("*") if entry.is_file()}
    result = _run_adapter(runner, [
        "probe-plugin-publication", "--payload-root", str(snapshot),
        "--plugin-id", "agent-example", "--legacy-root", str(root),
        "--durable-home", str(durable), "--policy-path", str(arguments["policy_path"]),
    ])
    after = {str(entry): entry.read_bytes() for entry in root.rglob("*") if entry.is_file()}
    assert after == before
    if case not in {"standalone", "directory", "managed"} and result.returncode == 1:
        assert not result.stdout
        assert "legacy snapshot" in result.stderr.lower()
        assert "Traceback" not in result.stderr
        return
    allowed = case in {"standalone", "directory"}
    assert result.returncode == (0 if allowed else 3), result.stdout + result.stderr
    decision = json.loads(result.stdout)["deployment"]
    assert decision["allowPluginPublication"] is allowed
    if case in {"standalone", "directory", "managed"}:
        assert decision["state"] == "valid"
        assert decision["managed"] is (case == "managed")
    else:
        assert decision["state"] == "invalid"
        assert decision["diagnostic"]
