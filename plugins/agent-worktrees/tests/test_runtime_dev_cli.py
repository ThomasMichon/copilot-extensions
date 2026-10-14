from __future__ import annotations

import argparse
import json
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


def test_deployed_release_uses_whole_operation_without_owner_checkout(tmp_path, monkeypatch):
    import sys

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
