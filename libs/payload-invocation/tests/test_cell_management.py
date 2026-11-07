"""Explicit cell publication borrows existing installation custody."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from test_command_binding import (
    _snapshot, _write, binding_fixture as binding_fixture, powershell_host as powershell_host,
)
from test_installer_publication import (
    _cell, _installer_environment, _policy, backend as backend,
)
from test_native_publication import SHELLS, _command, _run

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


@pytest.mark.parametrize("state", ["missing", "standalone", "managed"])
def test_management_lease_borrows_exact_existing_locks(binding_fixture: dict, state: str) -> None:
    primitive = binding_fixture["primitive"]
    root, arguments = _cell(binding_fixture, {})
    _policy(binding_fixture, root, arguments, state)
    with primitive.runtime_publication_lease(**arguments) as patch:
        borrowed_arguments = {
            **arguments, "environment": {**arguments["environment"], **patch},
        }
        before = _snapshot(binding_fixture["home"])
        locks = primitive.borrow_runtime_publication(**borrowed_arguments)
        assert _snapshot(binding_fixture["home"]) == before
        assert [lock.kind for lock in locks] == ["genesis", "install"]
        for lock in locks:
            owner = lock._owner()
            assert owner["pid"] == os.getpid()
            assert owner["runtimePublication"]["origin"] == "management"
            assert owner["runtimePublication"]["operation"] == "publish"
            lock.assert_owned()
    with pytest.raises(primitive.InstallationContextError):
        primitive.borrow_runtime_publication(**borrowed_arguments)
    assert all(not lock.path.exists() for lock in locks)


@pytest.mark.parametrize("state", ["missing", "standalone", "managed"])
def test_management_retains_active_cell_when_mode_policy_is_disabled(
    binding_fixture: dict, state: str,
) -> None:
    primitive = binding_fixture["primitive"]
    root, arguments = _cell(binding_fixture, {})
    _policy(binding_fixture, root, arguments, state)
    _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": False, "marketplaces": {}},
    })
    decision = primitive.resolve_runtime_deployment(**arguments)
    assert decision["status"] == "deactivation-required"
    assert decision["actualMode"] == "namespaced"
    assert decision["deployment"]["state"] in {"missing", "valid"}
    with primitive.runtime_publication_lease(**arguments) as patch:
        primitive.borrow_runtime_publication(**{
            **arguments, "environment": {**arguments["environment"], **patch},
        })


@pytest.mark.parametrize("state", ["invalid", "foreign", "future"])
def test_management_refuses_bad_policy_before_lock_writes(
    binding_fixture: dict, state: str,
) -> None:
    primitive = binding_fixture["primitive"]
    root, arguments = _cell(binding_fixture, {})
    _policy(binding_fixture, root, arguments, state)
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(primitive.InstallationContextError, match="Runtime publication refused"):
        with primitive.runtime_publication_lease(**arguments):
            pytest.fail("Invalid policy acquired management custody")
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("profiles", [{}, {"backend": "cpu"}, {"allowSourceFallback": False}])
def test_management_profile_hold_precedes_lease_borrow_and_cli(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch, capsys, managed: bool, profiles: dict,
) -> None:
    primitive = binding_fixture["primitive"]
    root, arguments = _cell(binding_fixture, {})
    _policy(binding_fixture, root, arguments, "managed" if managed else "standalone")
    path = root / "runtime-deployment.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["runtimeProfiles"] = profiles
    _write(path, document)
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(primitive.InstallationContextError, match="profile-aware construction"):
        with primitive.runtime_publication_lease(**arguments):
            pytest.fail("Profile hold acquired management custody")
    with pytest.raises(primitive.InstallationContextError, match="profile-aware construction"):
        primitive.borrow_runtime_publication(**arguments)
    for key, value in binding_fixture["environment"].items():
        monkeypatch.setenv(key, value)
    command = ["probe-management-publication"]
    for key in ("payload_root", "plugin_id", "legacy_root", "context", "durable_home"):
        command.extend(["--" + key.replace("_", "-"), str(arguments[key])])
    assert primitive.main(command) == 1
    output = capsys.readouterr()
    assert "profile-aware construction" in output.err
    assert "managementPublication" not in output.out
    assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("change", [
    "purpose", "purpose-version", "purpose-generation", "host", "pid",
    "different-pid", "token", "generation", "policy",
    "profile-empty", "profile-cpu", "profile-source",
])
def test_borrower_rejects_changed_custody(binding_fixture: dict, change: str) -> None:
    primitive = binding_fixture["primitive"]
    root, arguments = _cell(binding_fixture, {})
    _policy(binding_fixture, root, arguments, "managed")
    with primitive.runtime_publication_lease(**arguments) as patch:
        borrowed_arguments = {**arguments, "environment": {**arguments["environment"], **patch}}
        locks = primitive.borrow_runtime_publication(**borrowed_arguments)
        lock = locks[-1]
        before = lock.owner_path.read_bytes()
        owner = dict(lock._owner())
        receipt_path = Path(arguments["context"])
        receipt_before = receipt_path.read_bytes()
        policy_path = root / "runtime-deployment.json"
        policy_before = policy_path.read_bytes()
        try:
            if change == "purpose":
                owner["runtimePublication"]["operation"] = "teardown"
            elif change == "purpose-version":
                owner["runtimePublication"]["version"] = True
            elif change == "purpose-generation":
                owner["runtimePublication"]["installGeneration"] += 1
            elif change == "host":
                owner["host"] = "foreign"
            elif change == "pid":
                owner["pid"] = 2 ** 30
            elif change == "different-pid":
                owner["pid"] = os.getppid()
            elif change == "token":
                owner["token"] = "0" * 32
            elif change == "generation":
                receipt = json.loads(receipt_before)
                receipt["generation"] += 1
                _write(receipt_path, receipt)
            elif change.startswith("profile-"):
                document = json.loads(policy_before)
                document["runtimeProfiles"] = {
                    "profile-empty": {},
                    "profile-cpu": {"backend": "cpu"},
                    "profile-source": {"allowSourceFallback": False},
                }[change]
                _write(policy_path, document)
            else:
                _policy(binding_fixture, root, arguments, "invalid")
            if change not in {"generation", "policy"} and not change.startswith("profile-"):
                _write(lock.owner_path, owner)
            inventory = _snapshot(binding_fixture["home"])
            with pytest.raises(primitive.InstallationContextError):
                primitive.borrow_runtime_publication(**borrowed_arguments)
            assert _snapshot(binding_fixture["home"]) == inventory
        finally:
            lock.owner_path.write_bytes(before)
            receipt_path.write_bytes(receipt_before)
            policy_path.write_bytes(policy_before)


@pytest.mark.parametrize("ticket", ["1", "true", "{}", '{"version":true,"tokens":[]}'])
def test_environment_ticket_alone_never_authorizes(binding_fixture: dict, ticket: str) -> None:
    primitive = binding_fixture["primitive"]
    _, arguments = _cell(binding_fixture, {})
    arguments["environment"] = {
        **arguments["environment"], primitive.RUNTIME_PUBLICATION_ENV: ticket,
    }
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(primitive.InstallationContextError):
        primitive.borrow_runtime_publication(**arguments)
    assert _snapshot(binding_fixture["home"]) == before


def test_management_rechecks_after_both_real_locks(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    primitive = binding_fixture["primitive"]
    root, arguments = _cell(binding_fixture, {})
    original = primitive._DirectoryLock
    observed = []

    class ObservingLock(original):
        def acquire(self) -> None:
            super().acquire()
            observed.append(self.kind)
            if self.kind == "install":
                _policy(binding_fixture, root, arguments, "invalid")

    monkeypatch.setattr(primitive, "_DirectoryLock", ObservingLock)
    with pytest.raises(primitive.InstallationContextError, match="Runtime publication refused"):
        with primitive.runtime_publication_lease(**arguments):
            pytest.fail("Late invalid policy entered management")
    assert observed == ["genesis", "install"]
    assert not (root / "versions").exists()


@pytest.mark.parametrize("mode_enabled", [True, False])
def test_backend_borrows_outer_management_without_reacquiring(
    binding_fixture: dict, backend, monkeypatch: pytest.MonkeyPatch, mode_enabled: bool,
) -> None:
    primitive = binding_fixture["primitive"]
    environment = {}
    root, arguments = _cell(binding_fixture, environment)
    _policy(binding_fixture, root, arguments, "managed")
    if not mode_enabled:
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": False, "marketplaces": {}},
        })
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", environment["COPILOT_EXTENSIONS_CONTEXT"])
    with primitive.runtime_publication_lease(**arguments) as patch:
        monkeypatch.setenv(primitive.RUNTIME_PUBLICATION_ENV, patch[primitive.RUNTIME_PUBLICATION_ENV])
        governance = backend.Governance(root, binding_fixture["payload"])
        before = _snapshot(binding_fixture["home"])
        with governance.held(timeout=.1):
            assert len(governance.publication_locks) == 2
            assert all(lock._owner()["pid"] == os.getpid() for lock in governance.publication_locks)
            locks = governance.publication_locks
            with governance.held(timeout=.1):
                assert governance.publication_locks == locks
                governance.recheck()
            assert governance.publication_locks == locks
        assert _snapshot(binding_fixture["home"]) == before
        primitive.borrow_runtime_publication(**{
            **arguments, "environment": {**arguments["environment"], **patch},
        })


def _installer_command(fixture: dict, shell: str, root: Path, python: Path) -> list[str]:
    if shell == "bash":
        return [
            *_command(fixture, shell, "install"), "update", "--install-dir", str(root),
            "--runtime-publication-python", str(python),
        ]
    wrapper = fixture["payload"] / "scripts/managed-cell-probe.ps1"
    wrapper.write_text(
        "& (Join-Path $PSScriptRoot 'install.ps1') update -InstallDir '"
        + str(root).replace("'", "''") + "' -BootstrapPython '"
        + str(python).replace("'", "''") + "'\n", encoding="utf-8-sig",
    )
    return _command(fixture, shell, "managed-cell-probe")


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mode_enabled", [True, False])
def test_native_management_borrows_custody_before_and_after_staging(
    binding_fixture: dict, shell: str, mode_enabled: bool, powershell_host,
) -> None:
    fixture, primitive = binding_fixture, binding_fixture["primitive"]
    environment = _installer_environment(fixture, shell)
    root, arguments = _cell(
        fixture, environment, durable_home=fixture["home"] / "custody-\u5b89",
    )
    _policy(fixture, root, arguments, "managed")
    if not mode_enabled:
        _write(fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": False, "marketplaces": {}},
        })
    python = Path(sys.executable) if os.name == "nt" else fixture["home"] / "test-bin/python3"
    command = _installer_command(fixture, shell, root, python)
    with primitive.runtime_publication_lease(**arguments) as patch:
        environment.update(patch)
        result = _run(fixture, environment, command)
        assert result.returncode == 0, result.stdout + result.stderr
        smoke = json.loads((fixture["legacy"] / "smoke.json").read_text(encoding="utf-8-sig"))
        assert smoke["staged"] is True
        assert Path(smoke["ran_from"]) != fixture["payload"] / "scripts"
        primitive.borrow_runtime_publication(**{
            **arguments, "environment": {**arguments["environment"], **patch},
        })


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("ticket", ["forged", "expired"])
def test_native_management_refuses_without_live_custody_before_writes(
    binding_fixture: dict, shell: str, ticket: str, powershell_host,
) -> None:
    fixture, primitive = binding_fixture, binding_fixture["primitive"]
    environment = _installer_environment(fixture, shell)
    root, arguments = _cell(
        fixture, environment, durable_home=fixture["home"] / "custody-\u5b89",
    )
    _policy(fixture, root, arguments, "managed")
    if ticket == "expired":
        with primitive.runtime_publication_lease(**arguments) as patch:
            environment.update(patch)
    else:
        environment[primitive.RUNTIME_PUBLICATION_ENV] = json.dumps({
            "version": 1, "tokens": ["0" * 32, "0" * 32],
        })
    python = Path(sys.executable) if os.name == "nt" else fixture["home"] / "test-bin/python3"
    command = _installer_command(fixture, shell, root, python)
    host = powershell_host(fixture, environment) if shell == "powershell" else None
    before = _snapshot(fixture["home"])
    result = _run(fixture, environment, command, host)
    assert result.returncode != 0, result.stdout + result.stderr
    assert _snapshot(fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(fixture["home"]) == before


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Git Bash")
@pytest.mark.parametrize("mode_enabled", [True, False])
def test_windows_bash_management_borrows_native_paths(
    binding_fixture: dict, mode_enabled: bool,
) -> None:
    fixture, primitive = binding_fixture, binding_fixture["primitive"]
    git = shutil.which("git")
    bash = Path(git).resolve().parent.parent / "bin/bash.exe" if git else None
    if bash is None or not bash.is_file():
        pytest.skip("Native Windows Git Bash is unavailable")
    environment = _installer_environment(fixture, "bash")
    root, arguments = _cell(
        fixture, environment, durable_home=fixture["home"] / "custody-\u5b89",
    )
    _policy(fixture, root, arguments, "managed")
    if not mode_enabled:
        _write(fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": False, "marketplaces": {}},
        })
    command = [
        str(bash), "-c",
        'exec bash "$1" update --install-dir "$(cygpath -u "$2")" '
        '--runtime-publication-python "$(cygpath -u "$3")"',
        "--", (fixture["payload"] / "scripts/install.sh").as_posix(), str(root), sys.executable,
    ]
    with primitive.runtime_publication_lease(**arguments) as patch:
        result = _run(fixture, {**environment, **patch}, command)
        assert result.returncode == 0, result.stdout + result.stderr
        smoke = json.loads((fixture["legacy"] / "smoke.json").read_text(encoding="utf-8-sig"))
        assert smoke["staged"] is True
