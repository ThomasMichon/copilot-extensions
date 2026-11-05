from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import runtime_candidate, runtime_dev_cli


@pytest.mark.parametrize("fault", ["none", "changed", "editable", "omitted", "malformed"])
def test_original_origin_receipt_is_independent_of_current_graph(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(runtime_candidate.sys, "prefix", str(tmp_path))
    record = {
        "schema": "installer-engine.frozen-input-origins", "version": 1,
        "packages": {"original-only": "file:///gone/original"},
    }
    if fault == "malformed":
        record["version"] = True
    if fault == "omitted":
        record["packages"]["missing"] = "file:///gone/missing"
    (tmp_path / ".runtime-input-origins.json").write_text(json.dumps(record), encoding="utf-8")
    distribution = SimpleNamespace(
        metadata={"Name": "original-only"},
        locate_file=lambda _path: tmp_path,
        read_text=lambda _path: json.dumps({
            "url": "file:///different" if fault == "changed" else "file:///gone/original",
            "dir_info": {"editable": fault == "editable"},
        }),
    )
    monkeypatch.setattr(runtime_candidate, "distributions", lambda: [distribution])
    if fault == "none":
        runtime_candidate.verify_installed_origins()
    else:
        with pytest.raises(RuntimeError):
            runtime_candidate.verify_installed_origins()


def test_original_legacy_build_without_receipt_does_not_invent_origins(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_candidate.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(
        runtime_candidate, "distributions",
        lambda: pytest.fail("Legacy build must not be compared to today's dependency graph"),
    )
    runtime_candidate.verify_installed_origins()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("fault", [
    "none", "version", "origin", "missing-origin", "extra", "receipt", "validator", "health", "wheel",
])
def test_closed_wheel_receipt_qualifies_retained_control(tmp_path, monkeypatch, fault, nested):
    if fault in ("health", "wheel") and not nested:
        pytest.skip("Only an application retains its wheels")
    environment = tmp_path / ".venv" if nested else tmp_path
    environment.mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_candidate.sys, "prefix", str(environment))
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    bootstrap = tmp_path / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in (
        *runtime_candidate._LIFECYCLE_FILES, "versioned_runtime.py",
        "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py", "versioned_runtime_publication.py",
    ):
        shutil.copyfile(scripts / name, bootstrap / name)
    if fault == "validator":
        (bootstrap / "installer_wheel_snapshot.py").unlink()
    if fault == "health":
        (bootstrap / "installer_engine_health.py").unlink()

    content = b"fixture application wheel"
    digest = hashlib.sha256(content).hexdigest()
    filename = "example_app-1.0-py3-none-any.whl"
    binding = {"fixture": "selected"}
    lock_id = "sha256:" + hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if nested:
        wheel = tmp_path / "wheels" / filename
        wheel.parent.mkdir()
        wheel.write_bytes(b"changed" if fault == "wheel" else content)
        url = wheel.as_uri()
    else:
        url = "file:///gone/example.whl#sha256=" + digest
    wheels = {
        "construction": "closed-wheels", "lock_id": lock_id, "binding": binding,
        "distributions": {"example-app": {
            "version": "1.0", "filename": filename,
            "sha256": "sha256:" + digest,
        }},
    }
    if fault == "receipt":
        wheels["lock_id"] = "sha256:" + "b" * 64
    (tmp_path / ".runtime-input-origins.json").write_text(json.dumps({
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "packages": {"example-app": url}, "wheel_inputs": wheels,
    }), encoding="utf-8")
    origin = {
        "url": url,
        "archive_info": {"hashes": {"sha256": "b" * 64 if fault == "origin" else digest}},
    }
    distribution = SimpleNamespace(
        metadata={"Name": "Example_App"}, version="2.0" if fault == "version" else "1.0",
        locate_file=lambda _path: tmp_path,
        read_text=lambda _path: None if fault == "missing-origin" else json.dumps(origin),
    )
    installed = [distribution]
    if fault == "extra":
        installed.append(SimpleNamespace(metadata={"Name": "Unexpected"}, read_text=lambda _path: None))
    monkeypatch.setattr(runtime_candidate, "distributions", lambda **_kwargs: installed)
    if fault == "none":
        runtime_candidate.verify_installed_origins()
    else:
        with pytest.raises((RuntimeError, ValueError)):
            runtime_candidate.verify_installed_origins()


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path metadata")
def test_closed_wheel_origin_health_reads_long_windows_metadata(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    suffix = Path(r"versions\1.0.0+\Lib\site-packages\example_app-1.0.dist-info\direct_url.json")
    padding = 262 - len(str(root / suffix))
    assert 1 <= padding <= 200
    slot = root / "versions" / ("1.0.0+" + "a" * padding)
    site = slot / "Lib" / "site-packages"
    metadata = site / "example_app-1.0.dist-info"
    native = runtime_candidate._native
    native(metadata).mkdir(parents=True)
    native(metadata / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: example-app\nVersion: 1.0\n\n", encoding="utf-8",
    )
    digest = "a" * 64
    url = "file:///gone/example.whl#sha256=" + digest
    origin = metadata / "direct_url.json"
    assert len(str(origin)) >= 260
    native(origin).write_text(json.dumps({
        "url": url, "archive_info": {"hashes": {"sha256": digest}},
    }), encoding="utf-8")
    binding = {"fixture": "selected"}
    receipt = {
        "construction": "closed-wheels",
        "lock_id": "sha256:" + hashlib.sha256(json.dumps(
            binding, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
        "binding": binding,
        "distributions": {"example-app": {
            "version": "1.0", "filename": "example_app-1.0-py3-none-any.whl",
            "sha256": "sha256:" + digest,
        }},
    }
    native(slot / ".runtime-input-origins.json").write_text(json.dumps({
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "packages": {"example-app": url}, "wheel_inputs": receipt,
    }), encoding="utf-8")
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    bootstrap = slot / ".runtime-bootstrap"
    native(bootstrap).mkdir()
    for name in ("installer_wheel_snapshot.py", "installer_wheel_inputs.py"):
        shutil.copyfile(scripts / name, native(bootstrap / name))
    base = Path(sys.base_prefix).resolve()
    stdlib = [
        path for path in sys.path
        if path and Path(path).resolve().is_relative_to(base) and "site-packages" not in path.lower()
    ]
    with monkeypatch.context() as patch:
        patch.setattr(sys, "prefix", str(slot))
        patch.setattr(sys, "path", [str(site), *stdlib])
        runtime_candidate.verify_installed_origins()


@pytest.mark.parametrize("source_wheel", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_origin_verifier_normalizes_wheel_names_but_preserves_directory_names(
    tmp_path, monkeypatch, source_wheel, changed,
):
    monkeypatch.setattr(runtime_candidate.sys, "prefix", str(tmp_path))
    digest = "a" * 64
    name = "example-app" if source_wheel else "Example_App"
    url = "file:///gone/example.whl#sha256=" + digest if source_wheel else "file:///gone/example"
    record = {
        "schema": "installer-engine.frozen-input-origins", "version": 1,
        "packages": {name: url},
    }
    if source_wheel:
        record["source_wheels"] = {name: {"version": "1.0", "sha256": "sha256:" + digest}}
    (tmp_path / ".runtime-input-origins.json").write_text(json.dumps(record), encoding="utf-8")
    origin = {
        "url": "file:///changed" if changed else url,
        **({"archive_info": {"hashes": {"sha256": digest}}} if source_wheel else {"dir_info": {}}),
    }
    distribution = SimpleNamespace(
        metadata={"Name": "Example_App"}, version="1.0",
        locate_file=lambda _path: tmp_path,
        read_text=lambda _path: json.dumps(origin),
    )
    monkeypatch.setattr(runtime_candidate, "distributions", lambda: [distribution])
    if changed:
        with pytest.raises(RuntimeError):
            runtime_candidate.verify_installed_origins()
    else:
        runtime_candidate.verify_installed_origins()


@pytest.mark.parametrize(("archive", "accepted"), [
    ({}, True),
    ({"hashes": {"sha256": "b" * 64}}, False),
    ({"hash": "sha256=" + "b" * 64, "hashes": {"sha256": "a" * 64}}, False),
    ({"hashes": {"sha256": "a" * 64}, "extra": True}, False),
    (None, False),
])
def test_retained_source_wheel_archive_metadata_matches_construction(
    tmp_path, monkeypatch, archive, accepted,
):
    digest = "a" * 64
    url = "file:///gone/example.whl#sha256=" + digest
    monkeypatch.setattr(runtime_candidate.sys, "prefix", str(tmp_path))
    (tmp_path / ".runtime-input-origins.json").write_text(json.dumps({
        "schema": "installer-engine.frozen-input-origins", "version": 1,
        "packages": {"example-app": url},
        "source_wheels": {"example-app": {"version": "1.0", "sha256": "sha256:" + digest}},
    }), encoding="utf-8")
    distribution = SimpleNamespace(
        metadata={"Name": "example-app"}, version="1.0",
        locate_file=lambda _path: tmp_path,
        read_text=lambda _path: json.dumps({"url": url, "archive_info": archive}),
    )
    monkeypatch.setattr(runtime_candidate, "distributions", lambda: [distribution])
    if accepted:
        runtime_candidate.verify_installed_origins()
    else:
        with pytest.raises(RuntimeError, match="non-editable"):
            runtime_candidate.verify_installed_origins()


def test_deployed_release_uses_whole_operation_without_owner_checkout(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    root = tmp_path / "runtime"
    slot = root / "versions" / "dev"
    slot.mkdir(parents=True)
    missing_owner = tmp_path / "missing-worktree"
    monkeypatch.setattr(runtime_dev_cli.sys, "prefix", str(slot))
    monkeypatch.setattr(runtime_dev_cli.cfg, "install_dir", lambda: root)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    arguments = argparse.Namespace(owner=missing_owner, json=True)
    lifecycle = SimpleNamespace(
        SCRIPTS=slot / ".runtime-bootstrap",
        os=__import__("os"),
        Governance=lambda *_args: SimpleNamespace(signature=lambda: {"generation": 1}),
        runtime=SimpleNamespace(DevClaimConflict=RuntimeError),
    )
    monkeypatch.setattr(runtime_dev_cli, "_lifecycle", lambda: lifecycle)
    calls = []

    def owned(requested, plugin, **kwargs):
        assert requested == root
        assert plugin == lifecycle.SCRIPTS
        assert kwargs["owner"] == missing_owner
        assert kwargs["expected_governance"] == {"generation": 1}
        assert kwargs["mode"] == "dev-release"
        assert "uv" not in kwargs and "base_python" not in kwargs
        calls.append("whole-operation")
        return {"runtime_version": "original", "runtime_generation": "a" * 32}

    monkeypatch.setitem(sys.modules, "runtime_install_modes", SimpleNamespace(
        run_owned=owned,
        _resolve_owner=lambda _payload, owner, **_kwargs: owner.resolve(),
    ))
    with runtime_dev_cli.output.capture_json_output() as result:
        assert runtime_dev_cli.cmd_dev_release(arguments) == 0
    assert calls == ["whole-operation"]
    assert json.loads(result.getvalue())["runtime_version"] == "original"
    assert not missing_owner.exists()


def test_unavailable_deployed_lifecycle_reports_error_without_secondary_failure(monkeypatch):
    monkeypatch.setattr(
        runtime_dev_cli, "_lifecycle",
        lambda: (_ for _ in ()).throw(RuntimeError("missing installed lifecycle")),
    )
    with runtime_dev_cli.output.capture_json_output() as result:
        assert runtime_dev_cli.cmd_dev_release(argparse.Namespace(json=True)) == 1
    assert json.loads(result.getvalue())["error"] == "missing installed lifecycle"


def test_deployed_status_reads_claim_strictly_and_does_not_release(monkeypatch, tmp_path):
    from types import SimpleNamespace

    calls = []
    claim = {"owner": str(tmp_path / "owner"), "previous_version": "original"}

    def read(root, *, strict):
        assert root == tmp_path and strict
        calls.append("read")
        return claim

    monkeypatch.setattr(runtime_dev_cli.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_dev_cli, "_lifecycle", lambda: SimpleNamespace(
        runtime=SimpleNamespace(
            read_dev_claim=read, current_version=lambda _root: "dev",
        ),
    ))
    with runtime_dev_cli.output.capture_json_output() as result:
        assert runtime_dev_cli.cmd_dev_status(argparse.Namespace(json=True)) == 0
    assert calls == ["read"]
    assert json.loads(result.getvalue())["claim"] == claim
