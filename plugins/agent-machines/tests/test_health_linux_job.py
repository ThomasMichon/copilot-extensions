"""Portable finite-action controls, not native systemd qualification."""

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from agent_machines import _peer_launch, health_job, health_job_support, health_jobs as jobs
from agent_procutil import owned_process
from agent_procutil.owned_budget import OwnedExecutionBudget

TOKEN = "a" * 64
IDENTITY = "1" * 32 + ":" + "2" * 64
OWNER = "urn:copilot-extensions:health-job:" + "c" * 64


def test_installed_pair_pins_the_retained_finite_action_without_payload_or_account_switch() -> None:
    slot = '/example/runtime $one%/versions/1.2'
    result = jobs.installed_linux_definition(
        slot=slot, python=slot + "/.venv/bin/python", identity=IDENTITY,
        store='/example/store "three"%', policy_token=TOKEN, owner=OWNER,
        account_uid=1000, enabled=False,
    )
    service, timer = result["artifacts"].values()
    assert 'ExecStart="/example/runtime $one%%/versions/1.2/.venv/bin/python" "-I" "-S"' in service
    assert '"/example/runtime $$one%%/versions/1.2/.runtime-bootstrap/runtime_launcher.py"' in service
    assert f'"--runtime-identity" "{IDENTITY}"' in service
    assert '"-m" "agent_machines.health_job" "--store" "/example/store \\"three\\"%%"' in service
    assert "WorkingDirectory=" + slot.replace("%", "%%") + "\n" in service
    assert "TimeoutStartSec=55s\nTimeoutStopSec=5s\nKillMode=control-group\nRestart=no\n" in service
    assert "User=" not in service and "/bin/bash" not in service and "/bin/agent-machines" not in service
    assert "OnCalendar=*-*-* *:00:15 UTC\nPersistent=true\n" in timer
    assert "Unit=ce-health-" + "c" * 32 + ".service\n" in timer


@pytest.mark.parametrize(("slot", "python"), [
    ("/example/versions/dev", "/example/versions/dev/bin/python"),
    ("/example/versions/../1.2", "/example/versions/../1.2/bin/python"),
    ("/example/versions/1.2", "/usr/bin/python3"),
    ("/example/versions/1.2", "/example/versions/1.2/bin/bash"),
    ("/example/versions/1.2", "/example/versions/1.2/bin/python3"),
    ("/example/versions/1.2", "/example/versions/1.2/.venv/bin/python3.13"),
])
def test_pair_refuses_mutable_or_unbound_closures(slot: str, python: str) -> None:
    with pytest.raises(jobs.JobError, match="immutable-job-runtime-required"):
        jobs.installed_linux_definition(
            slot=slot, python=python, identity=IDENTITY, store="/example/store",
            policy_token=TOKEN, owner=OWNER, account_uid=1000, enabled=False,
        )


def test_collection_support_uses_only_one_package_in_selected_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "runtime" / "versions" / "1.0" / ".venv"
    python = environment / "bin" / "python"
    package = environment / "lib" / "python3.13" / "site-packages" / "agent_machines"
    package.mkdir(parents=True)
    path = package / "health-collection-v1.json"
    declaration = {
        "schema": "agent-machines.health.collection", "version": 1,
        "operations": ["preview", "apply", "installed-job"],
    }
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ValueError):
        health_job_support.require_collection_support(python)
    path.write_text(json.dumps(declaration))
    health_job_support.require_collection_support(python)
    path.write_text(json.dumps({**declaration, "operations": ["preview", "apply"]}))
    with pytest.raises(ValueError, match="collection-unsupported"):
        health_job_support.require_collection_support(python)
    path.write_text(json.dumps(declaration))
    (environment / "lib" / "python3.12" / "site-packages" / "agent_machines").mkdir(parents=True)
    with pytest.raises(ValueError, match="collection-unsupported"):
        health_job_support.require_collection_support(python)


def test_finite_job_keeps_owned_budget_and_retained_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "runtime" / "versions" / "1.0" / ".venv"
    python = environment / "bin" / "python"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setattr(sys, "executable", str(python))
    package = environment / "lib" / "python3.13" / "site-packages" / "agent_machines"
    package.mkdir(parents=True)
    declaration = package / "health-collection-v1.json"
    declaration.write_text(json.dumps({
        "schema": "agent-machines.health.collection", "version": 1,
        "operations": ["preview", "apply", "installed-job"],
    }))
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", lambda *_args: IDENTITY)
    prefix = [str(python), "-I", "-S", "retained-launcher", "--runtime-identity", IDENTITY]
    monkeypatch.setattr(_peer_launch, "retained_runtime_prefix", lambda *_args, **_kwargs: prefix)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(owned_process, "run_caller_owned", run)
    assert health_job.main(["--store", str(tmp_path / "store"), "--expect-policy", TOKEN]) == 0
    argv, options = calls[0]
    assert argv == [*prefix, "health", "run", "--store-dir", str(tmp_path / "store"),
                    "--apply", "--expect-policy", TOKEN]
    budget = options["budget"]
    assert options["timeout"] == 55 and isinstance(budget, OwnedExecutionBudget)
    assert budget.cleanup_reserve == 5 and options["cwd"] == environment.parent
    declaration.unlink()
    assert health_job.main(["--store", str(tmp_path / "store"), "--expect-policy", TOKEN]) == 2
    assert len(calls) == 1
