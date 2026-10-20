"""Helper rollback follows terminal backend state, never exception presence."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

from test_source_python_publication import (
    DECLARED_VERSION, admitted, native_hosts, publication_case as shared_publication_case,
    publication_transform as default_publication_transform,
)

publication_case = shared_publication_case
publication_transform = default_publication_transform


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize(("shell", "executable"), native_hosts())
@pytest.mark.parametrize("candidate_live", [False, True], ids=["previous-restored", "candidate-committed"])
@pytest.mark.parametrize("failure", ["late-activation", "late-manifest", "cutover"])
def test_terminal_settlement_decides_helper_outcome(
    publication_case, monkeypatch, shell, executable, candidate_live, failure,
):
    from agent_worktrees import config as cfg, runtime_deployment as deployment, status_monitor_cutover as cutover

    driver, root, slot, owner, content, _identity = publication_case
    monkeypatch.setattr(driver, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(cfg, "install_dir", lambda: root if candidate_live else root / "other")
    monkeypatch.setattr(driver, "_monitor_identity", lambda _root: None)
    # This is the monitor's terminal witness seam, not a live-daemon qualification.
    monkeypatch.setattr(driver, "_candidate_serving", lambda _root, candidate: (
        candidate_live and driver.runtime.current_version(root) == candidate.name
    ))
    activate, publish = driver.runtime.activate, deployment.publish
    activation_failed = manifest_failed = False

    def activate_then_error(*args, **kwargs):
        nonlocal activation_failed
        result = activate(*args, **kwargs)
        if failure == "late-activation" and not activation_failed:
            activation_failed = True
            raise OSError("Injected after selector publication")
        return result

    def manifest_then_error(*args, **kwargs):
        nonlocal manifest_failed
        publish(*args, **kwargs)
        if failure == "late-manifest" and not manifest_failed:
            manifest_failed = True
            raise OSError("Injected after manifest publication")

    def transition(**_kwargs):
        if failure == "cutover":
            raise OSError("Injected cutover failure")
        return {"action": "restart", "restart": {"spawned": True}}

    monkeypatch.setattr(cutover, "activate_after_update", transition)
    # A cutover fault needs monitor scope; a rolled-back terminal witness is false.
    if failure == "cutover":
        monkeypatch.setattr(cfg, "install_dir", lambda: root)
    with admitted(driver, root, owner, content, monkeypatch, hold=False) as (governance, _lease):
        previous = root / "versions" / "1.5.4"
        python = previous / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"previous fixture")
        driver.runtime.mark_complete(root, previous.name, payload_hash="previous")
        activate(root, previous.name, link_free=True)
        before_manifest = (root / "deploy-manifest.json").read_bytes()
        targets = {
            root / "bin" / name: content / "scripts" / name
            for name in ("resolve-runtime.sh", "resolve-runtime.ps1")
        }
        targets.update({
            root.parent / ".local/bin" / name: content / "bin" / name
            for name in ("agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1")
        })
        before_helpers = {}
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"previous helper\n")
            before_helpers[target] = target.read_bytes()
        monkeypatch.setattr(driver.runtime, "activate", activate_then_error)
        monkeypatch.setattr(deployment, "publish", manifest_then_error)
        with pytest.raises(OSError, match="Injected"):
            driver.settle_candidate(
                root, slot.name, payload=owner, payload_hash="candidate",
                expected_governance=governance.signature(), previous=previous.name,
                package_version=DECLARED_VERSION,
                frontend={"shell": shell, "executable": executable, "scope": "resolvers"},
                publication_source_root=content, child_timeout=20,
                base_python=Path(sys._base_executable),
            )
        if candidate_live:
            assert driver.runtime.current_version(root) == slot.name
            assert (root / "deploy-manifest.json").read_bytes() != before_manifest
            assert {target: target.read_bytes() for target in targets} == {
                target: source.read_bytes() for target, source in targets.items()
            }
        else:
            assert driver.runtime.current_version(root) == previous.name
            assert (root / "deploy-manifest.json").read_bytes() == before_manifest
            assert {target: target.read_bytes() for target in targets} == before_helpers
