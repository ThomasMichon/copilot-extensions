"""Actor-free command ownership across provider-verified root aliases."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2] / "libs/payload-invocation/command_binding.py"
)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_promotion_command_root_alias", SOURCE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ownership(
    api: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    root = tmp_path / "runtime"
    (root / "component").mkdir(parents=True)
    alias = root / "component" / ".."
    provenance = {
        "pluginId": "agent-worktrees",
        "marketplaceId": "example",
        "sourceFingerprint": "sha256:" + "a" * 64,
        "source": {"kind": "github", "canonical": "github:example/plugins", "ref": ""},
        "payloadRoot": str(tmp_path / "payload"),
    }
    mode = {
        "status": "ready",
        "reason": "legacy",
        "desiredMode": "legacy",
        "actualMode": "legacy",
        "marketplaceId": "example",
        "runtimeRoot": str(alias),
        "legacy": {"tombstone": None},
        "environment": {"platform": "fixture", "homeRealPath": str(tmp_path)},
    }
    receipt = api._legacy_receipt(provenance, {**mode, "runtimeRoot": str(root)})
    owner = {key: value for key, value in receipt.items() if key != "payloadRoot"}
    manifest = {
        "schema_version": 3,
        "service": "agent-worktrees",
        "commandBinding": receipt,
        "commandLifecycle": {"version": 1, "state": "complete", "owner": owner},
    }
    path = root / "deploy-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    primitive = SimpleNamespace(
        InstallationContextError=ValueError,
        resolve_payload_source=lambda **_kwargs: provenance,
        resolve_installation_mode=lambda **_kwargs: mode,
        paths_equal=lambda left, right: (
            {str(left), str(right)} <= {str(root), str(alias)}
        ),
        read_json=lambda selected: json.loads(selected.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(
        api,
        "_load_lifecycle",
        lambda: SimpleNamespace(
            incumbent_manifest=lambda *_args: json.loads(
                path.read_text(encoding="utf-8")
            ),
        ),
    )
    return SimpleNamespace(
        primitive=primitive,
        provenance=provenance,
        receipt=receipt,
        owner=owner,
        path=path,
        arguments={
            "payload_root": tmp_path / "payload",
            "plugin_id": "agent-worktrees",
            "legacy_root": alias,
        },
        root=root,
        manifest=manifest,
    )


@pytest.mark.parametrize("retained", ["binding", "reservation"])
def test_alias_keeps_original_publication_identity(
    api: ModuleType,
    ownership: SimpleNamespace,
    retained: str,
) -> None:
    if retained == "reservation":
        ownership.manifest.pop("commandBinding")
        ownership.manifest["commandLifecycle"]["state"] = "pending"
        ownership.path.write_text(json.dumps(ownership.manifest), encoding="utf-8")
    before = ownership.path.read_bytes()
    assert (
        api.publish_legacy_binding(
            ownership.primitive,
            **ownership.arguments,
        )
        == ownership.receipt
    )
    result = api.resolve_command_binding(ownership.primitive, **ownership.arguments)
    assert result["status"] == ("selected" if retained == "binding" else "blocked")
    if retained == "binding":
        assert (
            result["installation"]["installationId"]
            == ownership.receipt["installationId"]
        )
        assert result["installation"]["runtimeRoot"] == str(ownership.root)
    else:
        assert result["reason"] == "legacy-lifecycle-incomplete"
    assert ownership.path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        "other-root",
        "relative-root",
        "source",
        "installation",
        "provider-source",
    ],
)
def test_alias_does_not_authorize_changed_ownership(
    api: ModuleType,
    ownership: SimpleNamespace,
    change: str,
) -> None:
    receipt = ownership.manifest["commandBinding"]
    if change == "other-root":
        receipt["runtimeRoot"] = str(ownership.root.with_name("foreign"))
    elif change == "relative-root":
        receipt["runtimeRoot"] = "relative"
    elif change == "source":
        receipt["sourceFingerprint"] = "sha256:" + "b" * 64
    elif change == "installation":
        receipt["installationId"] = "sha256:" + "0" * 64
    else:
        ownership.provenance["sourceFingerprint"] = "sha256:" + "b" * 64
    ownership.path.write_text(json.dumps(ownership.manifest), encoding="utf-8")
    before = ownership.path.read_bytes()
    assert (
        api.resolve_command_binding(
            ownership.primitive,
            **ownership.arguments,
        )["status"]
        == "blocked"
    )
    with pytest.raises(api.CommandOwnershipConflict):
        api.publish_legacy_binding(ownership.primitive, **ownership.arguments)
    assert ownership.path.read_bytes() == before
