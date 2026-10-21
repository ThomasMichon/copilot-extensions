"""The normal updater repairs only its selected, already-adopted Manager."""

from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
import subprocess

import pytest
from agent_procutil import SyncProcessLaunchError

from agent_worktrees import manager_launch_cli as launch, manager_recovery as recovery


def legacy_stub() -> tuple[str, str]:
    if os.name == "nt":
        return "worktree-manager.cmd", (
            "@echo off\r\nsetlocal\r\n"
            'set "PYTHONUTF8=1"\r\n'
            'if "%WORKTREE_MANAGER_ROOT%"=="" set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
            'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
            'uv run --quiet --project "%WORKTREE_MANAGER_ROOT%\\versions\\%VER%" '
            "python -m worktree_manager %*\r\n"
        )
    return "worktree-manager", (
        "#!/usr/bin/env bash\n"
        "# worktree-manager binstub (versioned) \u2014 resolves the current-version marker.\n"
        "set -euo pipefail\n"
        'root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"\n'
        'ver="$(cat "$root/current-version")"\n'
        'exec uv run --quiet --project "$root/versions/$ver" python -m worktree_manager "$@"\n'
    )


@pytest.fixture
def adopted(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / ".worktree-manager"
    slot = root / "versions" / "0.6.35-dev1"
    package = slot / "src" / "worktree_manager"
    package.mkdir(parents=True)
    repo = tmp_path / "selected-source"
    repo.mkdir()
    (slot / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="0.6.35-dev1"\n',
    )
    (package / "source_config.py").write_text(
        f"DEFAULT_REPO = {str(repo)!r}\nDEFAULT_REF = 'selected-release'\n",
    )
    (root / "current-version").write_text(slot.name)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    name, body = legacy_stub()
    stub = bin_dir / name
    stub.write_bytes(body.encode("utf-8"))
    providers = home / "providers"
    providers.mkdir()
    manifest = providers / "worktree-manager.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager", "command": [str(stub)],
        "minimum_version": "0.1.0-dev21", "provider_root": str(root),
    }))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", str(providers))
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", raising=False)
    monkeypatch.setattr(launch, "_core", lambda: SimpleNamespace())
    return SimpleNamespace(root=root, slot=slot, stub=stub, manifest=manifest, repo=repo, providers=providers)


def test_unadopted_host_is_untouched(adopted, monkeypatch):
    adopted.manifest.unlink()
    monkeypatch.setattr(recovery, "_run", lambda *a, **k: pytest.fail("unadopted host executed a child"))
    assert recovery.recover() == {"status": "not-adopted"}
    assert (adopted.root / "current-version").read_text() == "0.6.35-dev1"


def test_selected_other_provider_is_not_manager_adoption(adopted, monkeypatch):
    data = json.loads(adopted.manifest.read_text())
    data["provider"] = "other"
    adopted.manifest.write_text(json.dumps(data))
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", "other")
    assert recovery.recover() == {"status": "not-adopted"}


def test_ambiguous_registration_refuses_before_acquisition(adopted):
    data = json.loads(adopted.manifest.read_text())
    data["provider"] = "other"
    (adopted.providers / "other.json").write_text(json.dumps(data))
    with pytest.raises(recovery.RecoveryRefused, match="Multiple"):
        recovery.recover()


def test_foreign_command_refuses(adopted):
    adopted.stub.write_text("echo foreign")
    with pytest.raises(recovery.RecoveryRefused, match="foreign"):
        recovery.recover()


def test_manager_owns_source_selection_not_updater_marketplace(adopted):
    config = adopted.root / "config.toml"
    config.write_text(f'[source]\nrepo={json.dumps(str(adopted.repo))}\nref="explicit-pin"\n')
    snapshot = recovery.adopted_manager().with_source()
    assert snapshot.repo == str(adopted.repo) and snapshot.ref == "explicit-pin"
    config.write_text(config.read_text().replace("explicit-pin", "changed-pin"))
    with pytest.raises(recovery.RecoveryRefused, match="changed"):
        snapshot.verify()


def test_healthy_constructed_manager_never_acquires_source(adopted, monkeypatch):
    (adopted.root / "config.toml").write_text("malformed = [")
    monkeypatch.setattr(recovery, "_healthy", lambda *args: True)
    monkeypatch.setattr(recovery, "_runtime_api", lambda: pytest.fail("healthy Manager acquired custody/rebuilt"))
    assert recovery.recover() == {"status": "current"}
    assert not (adopted.root / ".installer-inputs").exists()


def test_foreign_stub_retaining_known_tokens_is_refused(adopted):
    adopted.stub.write_bytes(adopted.stub.read_bytes() + b"\necho foreign\n")
    with pytest.raises(recovery.RecoveryRefused, match="foreign"):
        recovery.adopted_manager()


def test_duplicate_provider_registration_is_not_silently_ignored(adopted):
    (adopted.providers / "duplicate.json").write_bytes(adopted.manifest.read_bytes())
    with pytest.raises(recovery.RecoveryRefused, match="Duplicate"):
        recovery.adopted_manager()


def test_invalid_registration_is_not_silently_ignored(adopted):
    (adopted.providers / "other.json").write_text("{")
    with pytest.raises(recovery.RecoveryRefused, match="Invalid"):
        recovery.adopted_manager()


def test_registration_added_during_health_refuses_success(adopted, monkeypatch):
    def health(*args):
        (adopted.providers / "new.json").write_bytes(adopted.manifest.read_bytes())
        return True

    monkeypatch.setattr(recovery, "_healthy", health)
    with pytest.raises(recovery.RecoveryRefused, match="registrations changed"):
        recovery.recover()


def test_selection_and_admission_evidence_use_the_same_bytes(adopted, monkeypatch):
    original = recovery._slot

    def changing(*args):
        result = original(*args)
        (adopted.root / "current-version").write_text("different-slot")
        return result

    monkeypatch.setattr(recovery, "_slot", changing)
    with pytest.raises(recovery.RecoveryRefused, match="changed"):
        recovery.adopted_manager()


def test_source_snapshot_cannot_replace_an_admission_snapshot(adopted, monkeypatch):
    config = adopted.root / "config.toml"
    config.write_text('[source]\nref="one"\n')
    admission = recovery.adopted_manager()
    original = recovery._source

    def changing(root, slot):
        result = original(root, slot)
        config.write_text('[source]\nref="two"\n')
        return result

    monkeypatch.setattr(recovery, "_source", changing)
    with pytest.raises(recovery.RecoveryRefused, match="changed"):
        admission.with_source()


def test_prefix_health_does_not_substitute_for_registered_command(adopted, monkeypatch):
    python = adopted.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.touch()
    (adopted.slot / ".install-complete.json").write_text("{}")
    bootstrap = adopted.slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.touch()
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(
            arguments, int(arguments[0] == str(adopted.stub)), "worktree-manager 0.6.35-dev1",
        )

    monkeypatch.setattr(recovery, "_run", run)
    assert not recovery._healthy(recovery.adopted_manager(), {})
    assert calls[-1][0] == [str(adopted.stub), "--version"]
    assert calls[-1][1]["env"]["UV_NO_SYNC"] == "1"


@pytest.mark.parametrize("failure", [
    SyncProcessLaunchError(OSError("interpreter is not executable")),
    subprocess.TimeoutExpired(["probe"], 30),
    recovery.RecoveryTimeout("settled probe timeout"),
])
@pytest.mark.parametrize("registered", [False, True])
def test_health_failure_classification_preserves_unsettled_errors(adopted, monkeypatch, failure, registered):
    python = adopted.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.touch()
    (adopted.slot / ".install-complete.json").write_text("{}")
    bootstrap = adopted.slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.touch()

    def run(arguments, **kwargs):
        if not registered or arguments[0] == str(adopted.stub):
            raise failure
        return subprocess.CompletedProcess(arguments, 0, "worktree-manager 0.6.35-dev1")

    monkeypatch.setattr(recovery, "_run", run)
    if os.name == "nt" and isinstance(failure, subprocess.TimeoutExpired):
        with pytest.raises(subprocess.TimeoutExpired):
            recovery._healthy(recovery.adopted_manager(), {})
    else:
        assert not recovery._healthy(recovery.adopted_manager(), {})


def test_health_cleanup_refusal_does_not_license_repair(adopted, monkeypatch):
    from agent_worktrees.installer_run import InstallerCleanupError

    monkeypatch.setattr(
        recovery, "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(InstallerCleanupError("unverified tree")),
    )
    with pytest.raises(InstallerCleanupError, match="unverified"):
        recovery._health_probe(["probe"], adopted.root, {})


@pytest.mark.skipif(os.name != "nt", reason="Windows Job admission error distinction")
def test_health_job_admission_os_error_does_not_license_repair(adopted, monkeypatch):
    failure = OSError("Job admission failed after process creation")

    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(recovery, "_run", failed)
    with pytest.raises(OSError) as caught:
        recovery._health_probe(["probe"], adopted.root, {})
    assert caught.value is failure


@pytest.mark.skipif(os.name != "nt", reason="Windows Job timeout classification")
def test_settled_windows_health_timeout_is_not_a_containment_refusal(adopted, monkeypatch):
    from agent_worktrees import installer_run

    monkeypatch.setattr(installer_run, "run_installer", lambda *args, **kwargs: SimpleNamespace(
        rc=None, status="timed out (deadline hit during build)",
    ))
    with pytest.raises(recovery.RecoveryTimeout):
        recovery._run(["probe"], cwd=adopted.root, env={}, timeout=30)


@pytest.fixture
def broken(adopted, monkeypatch):
    monkeypatch.setattr(recovery, "_healthy", lambda *args: False)
    held = []

    @contextmanager
    def retaining(root, version):
        held.append(version)
        try:
            yield root / "versions" / version
        finally:
            held.remove(version)

    monkeypatch.setattr(recovery, "_runtime_api", lambda: SimpleNamespace(
        _validate_dev_previous_version=lambda version: None,
        version_dir=lambda root, version: root / "versions" / version,
        retention_lease=retaining,
    ))
    adopted.held = held
    return adopted


def test_pin_without_canonical_adopter_stays_blocked_and_selected(broken, monkeypatch):
    calls = []

    def run(arguments, **kwargs):
        assert broken.held == ["0.6.35-dev1"]
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "a" * 40 if "rev-parse" in arguments else "")

    monkeypatch.setattr(recovery, "_run", run)
    with pytest.raises(recovery.RecoveryRefused, match="source/pin"):
        recovery.recover()
    assert (broken.root / "current-version").read_text() == "0.6.35-dev1"
    assert not broken.held
    assert not any("--recover-adopted" in args for args in calls)


def test_admission_failure_never_rewrites_selection(broken, monkeypatch):
    monkeypatch.setattr(recovery, "_source_environment", lambda *args: (_ for _ in ()).throw(
        recovery.RecoveryRefused("credential admission failed"),
    ))
    monkeypatch.setattr(recovery, "_run", lambda *a, **k: pytest.fail("failed admission launched source"))
    with pytest.raises(recovery.RecoveryRefused, match="admission"):
        recovery.recover()
    assert (broken.root / "current-version").read_text() == "0.6.35-dev1"
    assert not broken.held


def test_staged_source_is_exactly_selected_ref_not_main(broken, monkeypatch):
    calls = []
    environments = []

    def run(arguments, *, cwd, **kwargs):
        calls.append(arguments)
        environments.append(kwargs["env"])
        if "checkout" in arguments:
            entry = cwd / "worktree-manager" / "scripts" / "runtime_install.py"
            entry.parent.mkdir(parents=True)
            entry.write_text("# --recover-adopted")
            (entry.parent.parent / "pyproject.toml").write_text(
                '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.0.0"\n',
            )
        if "--recover-adopted" in arguments:
            return subprocess.CompletedProcess(arguments, 1, "constructor refused")
        if "--describe-recovery" in arguments:
            return subprocess.CompletedProcess(arguments, 0, json.dumps({
                "schema": "copilot-extensions.manager-recovery-entry",
                "version": 1, "provider": "worktree-manager",
            }))
        return subprocess.CompletedProcess(arguments, 0, "a" * 40 if "rev-parse" in arguments else "")

    monkeypatch.setattr(recovery, "_run", run)
    with pytest.raises(recovery.RecoveryRefused, match="constructor refused"):
        recovery.recover()
    fetch = next(args for args in calls if "fetch" in args)
    assert fetch[-2:] == [str(broken.repo), "selected-release"]
    assert "main" not in fetch
    environment = environments[calls.index(fetch)]
    git_config = {
        environment[f"GIT_CONFIG_KEY_{index}"]: environment[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(environment["GIT_CONFIG_COUNT"]))
    }
    assert git_config["core.fsmonitor"] == "false"
    assert git_config["core.hooksPath"].endswith("disabled-hooks")
    if os.name == "nt":
        assert git_config["core.longpaths"] == "true"
    constructor = next(args for args in calls if "--recover-adopted" in args)
    assert constructor[1:5] == ["-I", "-S", "-B", "-X"]
    assert (broken.root / "current-version").read_text() == "0.6.35-dev1"


@pytest.mark.parametrize("failed_operation", ["init", "fetch"])
def test_source_auth_use_requires_a_launched_fetch(broken, monkeypatch, failed_operation):
    used = []
    monkeypatch.setattr(
        recovery, "_source_environment",
        lambda adoption, cwd, env: (env, lambda: used.append("fetch")),
    )

    def run(arguments, **kwargs):
        return subprocess.CompletedProcess(arguments, int(failed_operation in arguments), "refused")

    monkeypatch.setattr(recovery, "_run", run)
    with pytest.raises(recovery.RecoveryRefused, match=f"source {failed_operation} failed"):
        recovery.recover()
    assert used == ([] if failed_operation == "init" else ["fetch"])
    assert (broken.root / "current-version").read_text() == "0.6.35-dev1"


def test_manager_auth_uses_its_source_and_records_fallback_use(adopted, monkeypatch):
    from agent_worktrees import update_auth as auth, update_auth_context as context, update_events

    selected = recovery.adopted_manager().with_source()
    sources, results, uses = [], [], []

    def source(identity, url, **kwargs):
        sources.append((identity, url, kwargs))
        return SimpleNamespace(id=identity)

    item = SimpleNamespace(
        source=SimpleNamespace(id="manager-recovery"),
        summary=lambda: {"id": "manager-recovery", "required": True, "status": "fixable_by_fallback"},
    )
    report = SimpleNamespace(results=[item])
    overlay = SimpleNamespace(
        denies=set(), applies_to={"manager-recovery"},
        environment=lambda env: {**env, "FIXTURE_AUTH_ROUTE": "manager"},
    )
    monkeypatch.setattr(auth, "source", source)
    monkeypatch.setattr(auth, "non_repository_environment", lambda env, cwd: dict(env))
    monkeypatch.setattr(auth, "report", lambda values, **kwargs: report)
    monkeypatch.setattr(auth, "fallback", lambda report, **kwargs: overlay)
    monkeypatch.setattr(context, "mode", lambda: "on")
    monkeypatch.setattr(context, "describe", lambda report: None)
    monkeypatch.setattr(update_events, "auth_result", lambda item, **kwargs: results.append((item, kwargs)))
    monkeypatch.setattr(update_events, "auth_used", uses.append)
    environment, used = recovery._source_environment(selected, adopted.repo, {})
    assert environment["FIXTURE_AUTH_ROUTE"] == "manager"
    assert sources == [("manager-recovery", str(adopted.repo), {"purpose": "manager", "cwd": adopted.repo})]
    assert results[0][1] == {"applied": False, "available": True}
    assert not uses
    used()
    assert uses == [item.summary()]


@pytest.mark.parametrize("name,version", [
    ("foreign-package", "2.0.0"), ("copilot-extensions-worktree-manager", "0.0.1"),
])
def test_selected_target_contract_refuses_before_constructor(broken, monkeypatch, name, version):
    calls = []

    def run(arguments, *, cwd, **kwargs):
        calls.append(arguments)
        if "checkout" in arguments:
            payload = cwd / "worktree-manager"
            (payload / "scripts").mkdir(parents=True)
            (payload / "scripts" / "runtime_install.py").write_text("print('foreign')")
            (payload / "pyproject.toml").write_text(
                f'[project]\nname="{name}"\nversion="{version}"\n',
            )
        return subprocess.CompletedProcess(arguments, 0, "a" * 40 if "rev-parse" in arguments else "")

    monkeypatch.setattr(recovery, "_run", run)
    with pytest.raises(recovery.RecoveryRefused, match="package/version contract"):
        recovery.recover()
    assert not any("--describe-recovery" in arguments or "--recover-adopted" in arguments for arguments in calls)
    assert (broken.root / "current-version").read_text() == "0.6.35-dev1"
