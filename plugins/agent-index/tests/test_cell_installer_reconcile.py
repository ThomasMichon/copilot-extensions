"""Coordinator routing uses private admission/process witnesses, never host cleanup."""
from pathlib import Path
import os
import shutil
from types import SimpleNamespace

import pytest

from test_installation_cells import CELL, PLUGIN, _patch_selection_runtime, _roots, _selection_fixture


def _cleanup_witness(tmp_path, monkeypatch, events):
    scripts = tmp_path / "cleanup" / "scripts"
    scripts.mkdir(parents=True)
    extension = "ps1" if os.name == "nt" else "sh"
    for name in ("installer_instance_reconcile.py", f"installer-instance-reconcile.{extension}"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    calls = tmp_path / "cleanup-calls"
    if os.name == "nt":
        backend = """
function Invoke-DaemonInstanceReconcile {
    param($RegistryRoot, $Limit, $MaxSeconds)
    [IO.File]::AppendAllText($env:FIXTURE_GC_CALLS, "gc:${Limit}:${MaxSeconds}`n")
}
"""
    else:
        backend = """reconcile_daemon_instances() {
    printf 'gc:%s:%s\\n' "$1" "$2" >> "$FIXTURE_GC_CALLS"
}
"""
    (scripts / f"daemon_instances.{extension}").write_bytes(backend.encode())
    original = CELL._reconcile_installer_instances

    def reconcile():
        with monkeypatch.context() as local:
            local.setattr(CELL, "__file__", str(scripts / "cell-runtime.py"))
            local.setenv("FIXTURE_GC_CALLS", str(calls))
            local.delenv("PYTEST_CURRENT_TEST", raising=False)
            local.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
            original()
        events.append("gc")

    monkeypatch.setattr(CELL, "_reconcile_installer_instances", reconcile)
    return calls


def _admission(monkeypatch, validated, events, *, admitted=True):
    def validate(*_args, **_kwargs):
        events.append("validate")
        return validated

    def status(*_args):
        events.append("status")
        return {
            "status": "ready" if admitted else "unknown",
            "reason": "namespaced-active" if admitted else "unverified",
            "actualMode": "namespaced",
        }

    monkeypatch.setattr(CELL, "_validate_context", validate)
    monkeypatch.setattr(CELL, "_installation_status", status)


def _arguments(context, tmp_path):
    return SimpleNamespace(
        context=str(context), durable_home=str(tmp_path), expected_marketplace_id="example--1234",
    )


@pytest.mark.parametrize("state", ("fresh", "healthy", "refused"))
def test_provision_cleanup_follows_existing_admission_before_candidate(tmp_path, monkeypatch, state):
    validated = _roots(tmp_path)
    root = Path(validated["pluginRoot"])
    root.mkdir(parents=True)
    context = root / "install.json"
    context.write_text("private context\n")
    proof = root / "versions" / "production" / "proof"
    proof.parent.mkdir(parents=True)
    proof.write_bytes(b"protected production slot")
    selector = root / "current-version"
    selector.write_text("production\n")
    before = {p: p.read_bytes() for p in (context, selector, proof)}
    events = []
    calls = _cleanup_witness(tmp_path, monkeypatch, events)
    _admission(monkeypatch, validated, events, admitted=state != "refused")

    def candidate(*_args):
        assert events == ["validate", "status", "validate", "gc"]
        events.append(state)
        return {"status": "ready", "candidate": state}

    monkeypatch.setattr(CELL, "_provision_locked", candidate)
    for _ in range(2):
        events.clear()
        calls.unlink(missing_ok=True)
        if state == "refused":
            with pytest.raises(CELL.CellError, match="active validated namespaced"):
                CELL.provision(_arguments(context, tmp_path), PLUGIN)
            assert not calls.exists()
        else:
            result = CELL.provision(_arguments(context, tmp_path), PLUGIN)
            assert result["candidate"] == state
            assert calls.read_text().splitlines() == ["gc:128:5"]
        assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("state", ("admitted", "governance-blocked", "invalid-target", "unknown-marker"))
def test_pending_provision_cleanup_requires_successful_existing_recovery(tmp_path, monkeypatch, state):
    validated, root, context, _target, _prior, _transaction = _selection_fixture(
        tmp_path, prior_version="1.0.0", target_version="2.0.0",
    )
    proof = root / "versions" / "1.0.0" / "proof"
    proof.write_bytes(b"protected prior production slot")
    immutable = {p: p.read_bytes() for p in (context, proof)}
    events = []
    calls = _cleanup_witness(tmp_path, monkeypatch, events)
    _admission(monkeypatch, validated, events)
    governance = (
        [(False, {"status": "unknown", "reason": "unverified"})]
        if state == "governance-blocked" else None
    )
    _patch_selection_runtime(monkeypatch, root, governance=governance)
    if state == "invalid-target":
        def refuse(*_args):
            raise CELL.CellError("unverified transaction target")

        monkeypatch.setattr(CELL, "_validate_transaction_target", refuse)
    elif state == "unknown-marker":
        (root / "current-version").write_text("unknown-selection\n")
    finish = CELL._finish_selection_transaction

    def committed(*args, **kwargs):
        result = finish(*args, **kwargs)
        events.append("finished:" + kwargs["outcome"])
        return result

    monkeypatch.setattr(CELL, "_finish_selection_transaction", committed)
    if state in {"invalid-target", "unknown-marker"}:
        with pytest.raises(CELL.CellError):
            CELL.provision(_arguments(context, tmp_path), PLUGIN)
        assert not calls.exists()
    else:
        result = CELL.provision(_arguments(context, tmp_path), PLUGIN)
        if state == "admitted":
            assert result["status"] == "ready"
            assert events.index("finished:committed") < events.index("gc")
            assert calls.read_text().splitlines() == ["gc:128:5"]
            assert (root / "current-version").read_text().strip() == "2.0.0"
        else:
            assert result["status"] == "governance-blocked"
            assert not calls.exists()
            assert (root / "current-version").read_text().strip() == "1.0.0"
    assert all(p.read_bytes() == value for p, value in immutable.items())
