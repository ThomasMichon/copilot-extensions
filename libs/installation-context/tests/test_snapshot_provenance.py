"""Cross-runner tests for immutable snapshot provenance."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

LIB = Path(__file__).resolve().parents[1]
PYTHON_SCRIPT = LIB / "installation_context.py"
POSIX_SCRIPT = LIB / "installation-context.sh"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
FIXTURES = LIB / "fixtures" / "source-identities.json"
Runner = tuple[str, tuple[str, ...], str]

RUNNERS: tuple[Runner, ...] = (
    ("python", (sys.executable, str(PYTHON_SCRIPT)), "long"),
    ("posix", (str(POSIX_SCRIPT),), "long"),
    *(
        (
            (
                "powershell",
                (
                    str(POWERSHELL),
                    "-NoProfile",
                    "-File",
                    str(LIB / "installation-context.ps1"),
                ),
                "powershell",
            ),
        )
        if POWERSHELL is not None
        else ()
    ),
)


def _load_python_module() -> Any:
    spec = importlib.util.spec_from_file_location("snapshot_installation_context", PYTHON_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _vectors() -> list[dict[str, object]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["vectors"]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _receipt_layout(
    tmp_path: Path,
    *,
    vector_index: int = 0,
    plugin_id: str = "agent-example",
) -> dict[str, Path | str]:
    vector = _vectors()[vector_index]
    normalized = vector["normalized"]
    assert isinstance(normalized, dict)
    marketplace_id = str(vector["marketplaceId"])
    durable = tmp_path / "durable"
    cell = durable / "marketplaces" / marketplace_id
    plugin_root = cell / "plugins" / plugin_id
    payload = tmp_path / f"payload-{vector_index}-{plugin_id}"
    payload.mkdir(parents=True)
    (payload / "content.txt").write_text("original\n", encoding="utf-8")
    plugin_root.mkdir(parents=True)
    namespace = cell / "namespace.json"
    install = plugin_root / "install.json"
    if not namespace.exists():
        _write_json(
            namespace,
            {
                "schema": "copilot-extensions.marketplace-namespace",
                "version": 1,
                "marketplaceId": marketplace_id,
                "source": {
                    "kind": normalized["kind"],
                    "canonical": normalized["canonical"],
                    "ref": normalized["ref"],
                    "fingerprint": f"sha256:{vector['sha256']}",
                },
                "locators": [],
                "generation": 1,
                "state": "active",
            },
        )
    _write_json(
        install,
        {
            "schema": "copilot-extensions.plugin-installation",
            "version": 1,
            "marketplaceId": marketplace_id,
            "pluginId": plugin_id,
            "pluginRoot": str(plugin_root.resolve()),
            "namespaceReceipt": str(namespace.resolve()),
            "payload": {
                "root": str(payload.resolve()),
                "version": "1.0.0",
                "origin": "explicit",
            },
            "roots": {
                "versions": "versions",
                "snapshots": "snapshots",
                "state": "state",
                "run": "run",
                "logs": "logs",
                "cache": "cache",
                "launchers": "launchers",
            },
            "generation": 2,
            "state": "active",
        },
    )
    snapshot_root = plugin_root / "snapshots" / "1.0.0"
    snapshot_root.mkdir(parents=True)
    (snapshot_root / "payload-content.txt").write_text(
        "materialized snapshot\n",
        encoding="utf-8",
    )
    return {
        "marketplace_id": marketplace_id,
        "plugin_id": plugin_id,
        "durable": durable,
        "cell": cell,
        "plugin_root": plugin_root,
        "snapshots": plugin_root / "snapshots",
        "snapshot_root": snapshot_root,
        "payload": payload,
        "namespace": namespace,
        "install": install,
    }


def _flag(style: str, name: str) -> str:
    if style == "long":
        return f"--{name}"
    return "-" + "".join(part.capitalize() for part in name.split("-"))


def _command(
    runner: Runner,
    action: str,
    layout: dict[str, Path | str],
    *,
    snapshot_id: str = "1.0.0",
    expected_namespace_generation: int | str = 1,
    expected_install_generation: int | str = 2,
) -> list[str]:
    _, prefix, style = runner
    command = [
        *prefix,
        action,
        _flag(style, "context"),
        str(layout["install"]),
        _flag(style, "durable-home"),
        str(layout["durable"]),
        _flag(style, "expected-marketplace-id"),
        str(layout["marketplace_id"]),
        _flag(style, "expected-plugin-id"),
        str(layout["plugin_id"]),
        _flag(style, "snapshot-id"),
        snapshot_id,
    ]
    if action == "snapshot-stamp":
        command.extend(
            [
                _flag(style, "expected-namespace-generation"),
                str(expected_namespace_generation),
                _flag(style, "expected-install-generation"),
                str(expected_install_generation),
            ]
        )
    return command


def _run(
    runner: Runner,
    action: str,
    layout: dict[str, Path | str],
    *,
    snapshot_id: str = "1.0.0",
    expected_namespace_generation: int | str = 1,
    expected_install_generation: int | str = 2,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    result = subprocess.run(
        _command(
            runner,
            action,
            layout,
            snapshot_id=snapshot_id,
            expected_namespace_generation=expected_namespace_generation,
            expected_install_generation=expected_install_generation,
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(
            f"{runner[0]} failed ({result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _run_context_validate(
    runner: Runner,
    layout: dict[str, Path | str],
) -> subprocess.CompletedProcess[str]:
    _, prefix, style = runner
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    return subprocess.run(
        [
            *prefix,
            "validate",
            _flag(style, "context"),
            str(layout["install"]),
            _flag(style, "durable-home"),
            str(layout["durable"]),
            _flag(style, "expected-marketplace-id"),
            str(layout["marketplace_id"]),
            _flag(style, "expected-plugin-id"),
            str(layout["plugin_id"]),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def _stamp_with_python(
    layout: dict[str, Path | str],
    *,
    snapshot_id: str = "1.0.0",
) -> dict[str, object]:
    return json.loads(_run(RUNNERS[0], "snapshot-stamp", layout, snapshot_id=snapshot_id).stdout)


def _provenance_path(
    layout: dict[str, Path | str],
    snapshot_id: str = "1.0.0",
) -> Path:
    return Path(layout["snapshots"]) / snapshot_id / "snapshot-provenance.json"


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    snapshot: dict[str, tuple[str, bytes]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", b"")
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        else:
            snapshot[relative] = ("other", b"")
    return snapshot


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_stamp_and_validate_are_idempotent_and_cell_local(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    first = json.loads(_run(runner, "snapshot-stamp", layout).stdout)
    provenance = _provenance_path(layout)
    assert first["action"] == "snapshot-stamp"
    assert first["status"] == "ready"
    assert first["reason"] == "snapshot-provenance-published"
    assert first["snapshotChanged"] is True
    assert first["operative"] is False
    assert Path(first["provenance"]) == provenance.resolve()
    assert provenance.parent.parent == Path(layout["snapshots"])
    assert provenance.read_bytes().endswith(b"\n")
    assert not provenance.read_bytes().startswith(b"\xef\xbb\xbf")

    second = json.loads(_run(runner, "snapshot-stamp", layout).stdout)
    assert second["reason"] == "snapshot-provenance-current"
    assert second["snapshotChanged"] is False
    validated = json.loads(_run(runner, "snapshot-validate", layout).stdout)
    assert validated["action"] == "snapshot-validate"
    assert validated["sourceFingerprint"].startswith("sha256:")
    assert validated["namespaceGeneration"] == 1
    assert validated["installGeneration"] == 2
    assert validated["payload"]["originReceipt"] is None


def test_importable_python_snapshot_api_matches_cli(tmp_path: Path) -> None:
    layout = _receipt_layout(tmp_path)
    module = _load_python_module()
    stamped = module.stamp_snapshot_provenance(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        expected_namespace_generation=1,
        expected_install_generation=2,
        snapshot_id="1.0.0",
        durable_home=layout["durable"],
        environment={},
    )
    assert stamped["snapshotChanged"] is True
    validated = module.validate_snapshot_provenance(
        context=layout["install"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"],
        snapshot_id="1.0.0",
        durable_home=layout["durable"],
        environment={},
    )
    assert validated["provenance"] == stamped["provenance"]


@pytest.mark.parametrize(
    "argument",
    ("expected_namespace_generation", "expected_install_generation"),
)
def test_importable_python_snapshot_api_rejects_generation_overflow(
    argument: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    module = _load_python_module()
    generations = {
        "expected_namespace_generation": 1,
        "expected_install_generation": 2,
    }
    generations[argument] = 9223372036854775808
    with pytest.raises(
        module.InstallationContextError,
        match="portable signed 64-bit maximum",
    ):
        module.stamp_snapshot_provenance(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            snapshot_id="1.0.0",
            durable_home=layout["durable"],
            environment={},
            **generations,
        )
    assert not _provenance_path(layout).exists()


def test_python_reparse_detection_supports_pre_312_pathlib(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_python_module()
    path = tmp_path / "junction"
    path.mkdir()
    monkeypatch.setattr(module.Path, "is_symlink", lambda _self: False)
    if hasattr(module.Path, "is_junction"):
        monkeypatch.setattr(module.Path, "is_junction", lambda _self: False)
    monkeypatch.setattr(
        module.os,
        "lstat",
        lambda _path: SimpleNamespace(
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
        ),
    )
    assert module._is_link_or_junction(path) is True


def test_python_snapshot_leaf_name_defends_when_link_detection_degrades(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    module = _load_python_module()
    requested_root = Path(layout["snapshot_root"])
    other_root = Path(layout["snapshots"]) / "other"
    shutil.move(requested_root, other_root)
    requested_root.symlink_to(other_root, target_is_directory=True)
    monkeypatch.setattr(module, "_is_link_or_junction", lambda _path: False)
    with pytest.raises(
        module.InstallationContextError,
        match="requested snapshot id",
    ):
        module.stamp_snapshot_provenance(
            context=layout["install"],
            expected_marketplace_id=layout["marketplace_id"],
            expected_plugin_id=layout["plugin_id"],
            expected_namespace_generation=1,
            expected_install_generation=2,
            snapshot_id="1.0.0",
            durable_home=layout["durable"],
            environment={},
        )


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_original_payload_replacement_does_not_make_stage_path_identity(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    payload_file = Path(layout["payload"]) / "content.txt"
    payload_file.write_text("replacement\n", encoding="utf-8")
    validated = json.loads(_run(runner, "snapshot-validate", layout).stdout)
    assert validated["status"] == "ready"
    assert validated["payload"]["root"] == str(Path(layout["payload"]).resolve())


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        (("source", "fingerprint"), "sha256:" + "0" * 64, "fingerprint"),
        (("marketplaceId",), "other--0123456789abcdef", "Expected marketplace"),
        (("pluginId",), "agent-other", "Expected plugin"),
        (("payload", "root"), "/other/payload", "payload"),
        (("payload", "version"), "2.0.0", "payload"),
        (("payload", "origin"), "installed", "payload"),
        (("payload", "originReceipt"), "/other/origin.json", "payload"),
        (("namespaceReceipt", "path"), "/other/namespace.json", "namespace receipt"),
        (("namespaceReceipt", "generation"), 2, "namespace generation"),
        (("installReceipt", "path"), "/other/install.json", "install receipt"),
        (("installReceipt", "generation"), 3, "install generation"),
        (("snapshot", "id"), "other", "snapshot directory"),
        (("snapshot", "root"), "/other/snapshot", "snapshot.root"),
    ],
)
def test_snapshot_identity_mismatches_fail_closed(
    runner: Runner,
    path: tuple[str, ...],
    replacement: object,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance_path = _provenance_path(layout)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    target = provenance
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    _write_json(provenance_path, provenance)
    before = _tree_snapshot(Path(layout["durable"]))
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert message.lower() in result.stderr.lower()
    assert _tree_snapshot(Path(layout["durable"])) == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("source", None, "source is missing"),
        ("snapshot", "not-an-object", "snapshot identity is missing"),
        ("namespaceReceipt", [], "receipt references are missing"),
        ("payload", [], "payload identity is missing"),
    ),
)
def test_snapshot_container_fields_require_json_objects(
    runner: Runner,
    field: str,
    replacement: object,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance_path = _provenance_path(layout)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance[field] = replacement
    _write_json(provenance_path, provenance)
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert message in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"{", "invalid json"),
        (
            b'{"schema":"copilot-extensions.snapshot-provenance",'
            b'"schema":"copilot-extensions.snapshot-provenance","version":1}',
            "duplicate",
        ),
        (
            b"\xef\xbb\xbf"
            b'{"schema":"copilot-extensions.snapshot-provenance","version":1}',
            "invalid",
        ),
        (
            b'{"schema":"copilot-extensions.snapshot-provenance","version":"1"}',
            "version",
        ),
        (
            b'{"schema":"copilot-extensions.snapshot-provenance","version":2}',
            "version",
        ),
    ],
)
def test_malformed_snapshot_sidecars_are_rejected_without_replacement(
    runner: Runner,
    content: bytes,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance = _provenance_path(layout)
    provenance.write_bytes(content)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert message.lower() in result.stderr.lower()
    assert provenance.read_bytes() == content


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "snapshot_id",
    (
        "../other",
        "..\\other",
        "nested/child",
        "nested\\child",
        "/absolute",
        "1.0.0\n",
        "1.0.0\r",
    ),
)
def test_snapshot_path_attacks_are_rejected_without_mutation(
    runner: Runner,
    snapshot_id: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    before = _tree_snapshot(Path(layout["durable"]))
    result = _run(
        runner,
        "snapshot-stamp",
        layout,
        snapshot_id=snapshot_id,
        check=False,
    )
    assert result.returncode != 0
    assert "snapshot id" in result.stderr.lower()
    assert _tree_snapshot(Path(layout["durable"])) == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("materialization", ("missing", "empty"))
def test_snapshot_stamp_requires_preexisting_materialized_content(
    runner: Runner,
    materialization: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot_root = Path(layout["snapshot_root"])
    (snapshot_root / "payload-content.txt").unlink()
    if materialization == "missing":
        snapshot_root.rmdir()
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "materialized" in result.stderr.lower()
    assert not _provenance_path(layout).exists()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_validation_rejects_sidecar_only_snapshot(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    (Path(layout["snapshot_root"]) / "payload-content.txt").unlink()
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert "materialized" in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("receipt_name", ("namespace", "install"))
def test_stale_receipt_generation_rejects_republication_without_overwrite(
    runner: Runner,
    receipt_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance = _provenance_path(layout)
    original = provenance.read_bytes()
    receipt_path = Path(layout[receipt_name])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["generation"] += 1
    _write_json(receipt_path, receipt)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "generation changed" in result.stderr.lower()
    assert provenance.read_bytes() == original


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("receipt_name", ("namespace", "install"))
@pytest.mark.parametrize("state", ("inactive", "orphaned"))
def test_inactive_or_orphaned_receipts_reject_snapshot_validation(
    runner: Runner,
    receipt_name: str,
    state: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    receipt_path = Path(layout[receipt_name])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["state"] = state
    _write_json(receipt_path, receipt)
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert "active namespace and install receipts" in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    "created_at",
    ("not-a-time", "2026-02-30T00:00:00Z", "2026-01-01T00:00:00+00:00"),
)
def test_snapshot_created_at_must_be_exact_valid_utc_timestamp(
    runner: Runner,
    created_at: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance_path = _provenance_path(layout)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["createdAt"] = created_at
    _write_json(provenance_path, provenance)
    result = _run(runner, "snapshot-validate", layout, check=False)
    assert result.returncode != 0
    assert "createdat" in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("target_kind", ("cell", "plugin"))
def test_copied_cross_cell_or_cross_plugin_sidecar_is_rejected(
    runner: Runner,
    target_kind: str,
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, vector_index=0)
    _stamp_with_python(source_layout)
    if target_kind == "cell":
        target_layout = _receipt_layout(tmp_path, vector_index=1)
    else:
        target_layout = _receipt_layout(tmp_path, vector_index=0, plugin_id="agent-other")
    target = _provenance_path(target_layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_provenance_path(source_layout), target)
    result = _run(runner, "snapshot-validate", target_layout, check=False)
    assert result.returncode != 0
    assert (
        "expected marketplace" in result.stderr.lower()
        or "expected plugin" in result.stderr.lower()
    )


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_rewritten_foreign_sidecar_still_fails_receipt_anchoring(
    runner: Runner,
    tmp_path: Path,
) -> None:
    source_layout = _receipt_layout(tmp_path, vector_index=0)
    target_layout = _receipt_layout(tmp_path, vector_index=1)
    _stamp_with_python(source_layout)
    provenance = json.loads(
        _provenance_path(source_layout).read_text(encoding="utf-8")
    )
    target_namespace = json.loads(
        Path(target_layout["namespace"]).read_text(encoding="utf-8")
    )
    provenance["marketplaceId"] = target_layout["marketplace_id"]
    provenance["pluginId"] = target_layout["plugin_id"]
    provenance["source"] = target_namespace["source"]
    provenance["snapshot"] = {
        "id": "1.0.0",
        "root": str(Path(target_layout["snapshot_root"]).resolve()),
    }
    _write_json(_provenance_path(target_layout), provenance)
    result = _run(runner, "snapshot-validate", target_layout, check=False)
    assert result.returncode != 0
    assert "namespace receipt" in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_plugin_root_symlink_cannot_escape_the_cell(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    plugin_root = Path(layout["plugin_root"])
    outside = tmp_path / "outside-plugin-root"
    shutil.move(plugin_root, outside)
    try:
        plugin_root.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    before = _tree_snapshot(outside)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "plugin root" in result.stderr.lower()
    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("component", "label"),
    (
        ("marketplaces", "marketplaces root"),
        ("cell", "marketplace cell root"),
        ("plugins", "cell plugins root"),
        ("plugin_root", "plugin root"),
    ),
)
def test_context_validation_rejects_linked_physical_ownership_chain(
    runner: Runner,
    component: str,
    label: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    paths = {
        "marketplaces": Path(layout["durable"]) / "marketplaces",
        "cell": Path(layout["cell"]),
        "plugins": Path(layout["cell"]) / "plugins",
        "plugin_root": Path(layout["plugin_root"]),
    }
    linked_path = paths[component]
    outside = tmp_path / f"outside-{component}"
    shutil.move(linked_path, outside)
    try:
        linked_path.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    before = _tree_snapshot(outside)
    result = _run_context_validate(runner, layout)
    assert result.returncode != 0
    assert label in result.stderr.lower()
    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize("receipt_name", ("namespace", "install"))
def test_context_validation_rejects_linked_receipt_files(
    runner: Runner,
    receipt_name: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    receipt = Path(layout[receipt_name])
    outside = tmp_path / f"outside-{receipt_name}.json"
    shutil.move(receipt, outside)
    try:
        receipt.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file symlinks are unavailable: {error}")
    original = outside.read_bytes()
    result = _run_context_validate(runner, layout)
    assert result.returncode != 0
    assert f"{receipt_name}.json may not" in result.stderr.lower()
    assert outside.read_bytes() == original


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_root_symlink_cannot_escape_the_plugin(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    snapshot_root = Path(layout["snapshot_root"])
    outside = tmp_path / "outside-snapshot"
    shutil.move(snapshot_root, outside)
    try:
        snapshot_root.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    before = _tree_snapshot(outside)
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "snapshot root" in result.stderr.lower()
    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_snapshot_sidecar_symlink_is_rejected_without_touching_target(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    provenance = _provenance_path(layout)
    outside = tmp_path / "outside-provenance.json"
    shutil.move(provenance, outside)
    try:
        provenance.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file symlinks are unavailable: {error}")
    original = outside.read_bytes()
    result = _run(runner, "snapshot-stamp", layout, check=False)
    assert result.returncode != 0
    assert "snapshot provenance" in result.stderr.lower()
    assert outside.read_bytes() == original


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize(
    ("argument", "value", "message"),
    (
        ("expected_namespace_generation", "1.5", "non-negative integer"),
        ("expected_install_generation", "2.5", "non-negative integer"),
        (
            "expected_namespace_generation",
            "9223372036854775808",
            "portable signed 64-bit maximum",
        ),
    ),
)
def test_powershell_rejects_non_decimal_int64_generation_arguments(
    argument: str,
    value: str,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    generations: dict[str, int | str] = {
        "expected_namespace_generation": 1,
        "expected_install_generation": 2,
    }
    generations[argument] = value
    result = _run(
        next(runner for runner in RUNNERS if runner[0] == "powershell"),
        "snapshot-stamp",
        layout,
        check=False,
        **generations,
    )
    assert result.returncode != 0
    assert message in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
@pytest.mark.parametrize(
    ("value", "message"),
    (
        ("+1", "generation"),
        (" 1", "generation"),
        ("1_0", "generation"),
        ("\u0661", "generation"),
        ("9223372036854775808", "portable signed 64-bit maximum"),
        ("10000000000000000000", "portable signed 64-bit maximum"),
    ),
)
def test_generation_arguments_reject_non_ascii_decimal_or_overflow(
    runner: Runner,
    value: str,
    message: str,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    result = _run(
        runner,
        "snapshot-stamp",
        layout,
        expected_namespace_generation=value,
        check=False,
    )
    assert result.returncode != 0
    assert message in result.stderr.lower()


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_generation_arguments_normalize_leading_zeroes(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    result = json.loads(
        _run(
            runner,
            "snapshot-stamp",
            layout,
            expected_namespace_generation="01",
            expected_install_generation="002",
        ).stdout
    )
    assert result["reason"] == "snapshot-provenance-published"


@pytest.mark.parametrize("runner", RUNNERS, ids=lambda runner: runner[0])
def test_concurrent_snapshot_publication_has_one_atomic_winner_and_retry(
    runner: Runner,
    tmp_path: Path,
) -> None:
    layout = _receipt_layout(tmp_path)
    environment = os.environ.copy()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    processes = [
        subprocess.Popen(
            _command(runner, "snapshot-stamp", layout),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        for _ in range(2)
    ]
    results = [
        (*process.communicate(timeout=30), process.returncode)
        for process in processes
    ]
    payloads: list[dict[str, object]] = []
    for stdout, stderr, returncode in results:
        if returncode == 0:
            payloads.append(json.loads(stdout))
            continue
        assert "remained busy" in stderr.lower(), results
        payloads.append(json.loads(_run(runner, "snapshot-stamp", layout).stdout))
    assert sum(payload["snapshotChanged"] is True for payload in payloads) == 1
    assert sum(payload["snapshotChanged"] is False for payload in payloads) == 1
    provenance = _provenance_path(layout)
    assert json.loads(provenance.read_text(encoding="utf-8"))["snapshot"]["id"] == "1.0.0"
