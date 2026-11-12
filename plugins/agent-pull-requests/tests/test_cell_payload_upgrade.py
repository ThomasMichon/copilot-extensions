"""Normal payload init owns same-source receipt refresh and live-owner upgrade."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from time import monotonic

import pytest
from agent_procutil import no_window_kwargs
from test_cell_installer_slice import PAYLOAD
from test_cell_upgrade import _pending
from test_watch_installation import _policy
from zdd import diagnostics, lifecycle, process_exit, publication_history, routing

from agent_pull_requests import _installation_context as api
from agent_pull_requests.watch_storage import read_subscriptions_state


@pytest.fixture
def native_profile(request):
    if os.environ.get("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE") != "1":
        return None
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        pytest.fail("native account acceptance requires a disposable GitHub-hosted runner")
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, buffer)
        if result != 0:
            raise OSError(f"native profile lookup failed: {result}")
        profile = Path(buffer.value).resolve()
        source = "windows-CSIDL_PROFILE"
    else:
        import pwd

        profile = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        source = "posix-account-database"
    assert Path.home().resolve() == profile, "Python home must match the native OS account"
    for name in ("HOME", "USERPROFILE"):
        if os.environ.get(name):
            assert Path(os.environ[name]).resolve() == profile, f"{name} is a profile override"
    for path in (
        profile / ".copilot" / "settings.json",
        profile / ".copilot-extensions",
        profile / ".agent-pull-requests",
    ):
        assert not os.path.lexists(path), f"native acceptance requires fresh state: {path.name}"
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"native account acceptance: {source}; home/profile match; fresh state")
    # The hosted job owns this whole fresh account and discards it afterward.
    # Each test still gracefully stops its exact owners before returning.
    return profile


def _release(root, *, marketplace_name="fixture", profile=None):
    payload = root / "m" / "plugins" / "agent-pull-requests"
    spec = importlib.util.spec_from_file_location(
        "apr_normal_upgrade_release", PAYLOAD / "scripts" / "cell_snapshot.py",
    )
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    producer.materialize(PAYLOAD, payload, api)
    marketplace = root / "m" / ".github" / "plugin" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({
        "name": marketplace_name, "owner": {"name": "Test fixture"},
        "plugins": [{
            "name": "agent-pull-requests", "source": "./plugins/agent-pull-requests",
            "description": "Test-owned release payload",
        }],
    }), encoding="utf-8")
    profile = root / "p" if profile is None else profile
    profile.mkdir(exist_ok=True)
    settings = profile / ".copilot" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            marketplace_name: {"source": {"source": "directory", "path": str(root / "m")}},
        },
        "enabledPlugins": {f"agent-pull-requests@{marketplace_name}": True},
    }), encoding="utf-8")
    _policy(profile)
    return payload, profile


def _refresh_release(payload, version):
    project = payload / "pyproject.toml"
    project.write_text(re.sub(
        r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"',
        project.read_text("utf-8"), count=1,
    ), encoding="utf-8")
    package = payload / "src" / "agent_pull_requests" / "__init__.py"
    package.write_text(re.sub(
        r'(?m)^__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"',
        package.read_text("utf-8"), count=1,
    ), encoding="utf-8")
    path = payload / "plugin.json"
    document = json.loads(path.read_text("utf-8"))
    document["version"] = version
    path.write_text(json.dumps(document), encoding="utf-8")


def _run(script, arguments, environment, caller, *, timeout):
    if os.name == "nt":
        command = [
            shutil.which("pwsh") or shutil.which("powershell"),
            "-NoProfile", "-NonInteractive", "-File", str(script),
        ]
    else:
        target = script.with_suffix("" if script.parent.name == "bin" else ".sh")
        command = ["bash", str(target)]
    try:
        return subprocess.run(  # noqa: S603 -- exact test-owned released payload
            [*command, *arguments],
            env=environment, cwd=caller, capture_output=True, text=True,
            timeout=timeout, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as error:
        print(
            f"Owned payload command timed out after {timeout}s: "
            f"stdout={error.stdout!r}; stderr={error.stderr!r}",
            file=sys.stderr,
        )
        raise


def _run_cutover(script, arguments, environment, caller, *, run_root, phase, timeout):
    try:
        return _run(script, arguments, environment, caller, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            f"Owned {phase} timed out: {_cutover_failure_details(run_root)}; "
            f"stdout={error.stdout!r}; stderr={error.stderr!r}"
        ) from error


def _cutover_failure_details(run_root):
    path = run_root / "watch-owner-transaction.json"
    journal = {"state": "absent"}
    identity = {}
    if path.is_file():
        try:
            document = json.loads(path.read_text("utf-8"))
            journal = {key: document.get(key) for key in ("phase", "exited")}
            owner = document.get("successor")
            if isinstance(owner, dict):
                pid, birth = owner.get("pid"), owner.get("startTime")
                if type(pid) is int and isinstance(birth, str):
                    try:
                        actual = diagnostics.process_start_time(pid)
                        identity["nativeBirthMatches"] = None if actual is None else actual == birth
                        identity["originalExited"] = process_exit.observe_original_exit(pid, birth)
                    except (OSError, ValueError, NotImplementedError) as error:
                        identity["observationError"] = type(error).__name__
        except (OSError, ValueError) as error:
            journal = {"readError": type(error).__name__}
    events = [
        {key: event.get(key) for key in ("ts", "action", "outcome", "version")}
        for event in lifecycle.read_events(run_root, limit=24)
    ]
    return {"journal": journal, "events": events, "identity": identity}


def _scenario_remaining(deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Native scenario deadline expired")
    return remaining


def test_scenario_remaining_preserves_the_original_deadline(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "monotonic", lambda: 100)
    assert _scenario_remaining(140) == 40
    monkeypatch.setattr(sys.modules[__name__], "monotonic", lambda: 141)
    with pytest.raises(TimeoutError, match="scenario deadline expired"):
        _scenario_remaining(140)


def test_payload_command_timeout_retains_both_streams(tmp_path, monkeypatch, capsys):
    timeout_error = subprocess.TimeoutExpired(
        ["fixture"], 5, output="partial stdout", stderr="partial stderr",
    )
    timeout_error.add_note = None

    def timed_out(command, **kwargs):
        raise timeout_error

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        _run(tmp_path / "scripts" / "init.ps1", [], {}, tmp_path, timeout=5)
    captured = capsys.readouterr()
    assert failure.value is timeout_error
    assert "after 5s" in captured.err
    assert "partial stdout" in captured.err
    assert "partial stderr" in captured.err


@pytest.mark.parametrize("phase", ["startup", "upgrade"])
def test_cutover_timeout_retains_caller_evidence_and_both_streams(tmp_path, monkeypatch, phase):
    (tmp_path / "watch-owner-transaction.json").write_text(json.dumps({
        "phase": "committed", "exited": [],
        "successor": {"pid": 42, "startTime": "10", "token": "fixture-private"},
    }), encoding="utf-8")
    lifecycle.record(tmp_path, "fixture", detail={"private": "fixture-private"})
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "10")
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda pid, birth: False)
    timeout_error = subprocess.TimeoutExpired(
        ["fixture"], 5, output="partial stdout", stderr="partial stderr",
    )
    timeout_error.add_note = None

    def timed_out(*_args, **_kwargs):
        raise timeout_error

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(AssertionError) as failure:
        _run_cutover(
            tmp_path / "scripts" / "init.ps1", [], {}, tmp_path,
            run_root=tmp_path, phase=phase, timeout=5,
        )
    message = str(failure.value)
    assert f"Owned {phase} timed out" in message
    assert "'phase': 'committed'" in message
    assert "'action': 'fixture'" in message
    assert "'nativeBirthMatches': True" in message
    assert "'originalExited': False" in message
    assert "partial stdout" in message and "partial stderr" in message
    assert "fixture-private" not in message
    assert failure.value.__cause__ is timeout_error


def test_cutover_failure_details_exclude_private_journal_and_event_fields(tmp_path):
    (tmp_path / "watch-owner-transaction.json").write_text(json.dumps({
        "phase": "committed", "exited": [], "authorization": "fixture-private-capability",
    }), encoding="utf-8")
    lifecycle.record(
        tmp_path, "fixture", detail={"private": "fixture-private-detail"},
    )
    evidence = _cutover_failure_details(tmp_path)
    assert evidence["journal"] == {"phase": "committed", "exited": []}
    assert set(evidence["events"][0]) == {"ts", "action", "outcome", "version"}
    assert "fixture-private" not in json.dumps(evidence)


@pytest.mark.parametrize("actual,exited", [
    ("10", False), ("11", True), (None, True),
])
def test_cutover_failure_details_separate_native_birth_and_original_exit(
    tmp_path, monkeypatch, actual, exited,
):
    (tmp_path / "watch-owner-transaction.json").write_text(json.dumps({
        "phase": "complete", "exited": [],
        "successor": {"pid": 42, "startTime": "10", "token": "fixture-private"},
    }), encoding="utf-8")
    seen = []
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: actual)

    def observe(pid, birth):
        seen.append((pid, birth))
        return exited

    monkeypatch.setattr(process_exit, "observe_original_exit", observe)
    evidence = _cutover_failure_details(tmp_path)
    assert evidence["identity"] == {
        "nativeBirthMatches": None if actual is None else actual == "10",
        "originalExited": exited,
    }
    assert seen == [(42, "10")]
    assert "fixture-private" not in json.dumps(evidence)


def test_cutover_failure_details_preserve_unconfirmed_native_exit(tmp_path, monkeypatch):
    (tmp_path / "watch-owner-transaction.json").write_text(json.dumps({
        "phase": "complete", "exited": [],
        "successor": {"pid": 42, "startTime": "10"},
    }), encoding="utf-8")
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)

    def unavailable(*_args):
        raise PermissionError("fixture-private native failure")

    monkeypatch.setattr(process_exit, "observe_original_exit", unavailable)
    evidence = _cutover_failure_details(tmp_path)
    assert evidence["identity"] == {
        "nativeBirthMatches": None, "observationError": "PermissionError",
    }
    assert "fixture-private" not in json.dumps(evidence)


@pytest.mark.parametrize("parent,filename,expected", [
    ("scripts", "init.ps1", "init.sh"),
    ("bin", "agent-pull-requests.ps1", "agent-pull-requests"),
])
def test_posix_fixture_invokes_the_shipped_launcher_name(
    tmp_path, monkeypatch, parent, filename, expected,
):
    from types import SimpleNamespace

    monkeypatch.setattr(sys.modules[__name__], "os", SimpleNamespace(name="posix"))
    commands = []

    def capture(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", capture)
    _run(tmp_path / parent / filename, ["--json"], {}, tmp_path, timeout=5)
    assert commands == [["bash", str(tmp_path / parent / expected), "--json"]]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
# CLI admission precedes the separate 120-second owner cutover budget.
@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" and os.environ.get("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE") != "1",
    reason="Native POSIX upgrade needs an isolated OS account",
)
def test_normal_payload_init_refreshes_receipt_and_upgrades_live_owner(tmp_path_factory, native_profile):
    started_at = monotonic()
    scenario_deadline = started_at + 900

    def phase(name):
        now = monotonic()
        print(
            f"Owned lifecycle phase: {name}; elapsed={now - started_at:.2f}s; "
            f"remaining={scenario_deadline - now:.2f}s",
            flush=True,
        )

    phase("release preparation")
    root = tmp_path_factory.mktemp("u")
    payload, profile = _release(root, profile=native_profile)
    caller = root / "caller"
    caller.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"COPILOT_EXTENSIONS_CONTEXT", "PYTHONPATH", "PYTHONHOME"}
    }
    if native_profile is None:
        environment.update(HOME=str(profile), USERPROFILE=str(profile))
    init = payload / "scripts" / "init.ps1"
    command = payload / "bin" / "agent-pull-requests.ps1"
    unattributed = root / "unattributed" / "scripts"
    unattributed.mkdir(parents=True)
    for name in ("payload-mode.ps1", "payload-mode.sh"):
        shutil.copyfile(PAYLOAD / "scripts" / name, unattributed / name)
    shutil.copytree(
        PAYLOAD / "scripts" / "installation-context", unattributed / "installation-context",
    )
    policy = profile / ".copilot-extensions" / "installation-mode.json"
    original_policy = policy.read_bytes()
    try:
        for overrides in ("absent", {}, None):
            mode = {"enabled": False}
            if overrides != "absent":
                mode["marketplaces"] = overrides
            policy.write_text(json.dumps({
                "schema": api.POLICY_SCHEMA, "version": 1, "installationMode": mode,
            }), encoding="utf-8")
            admitted = _run(
                unattributed / "payload-mode.ps1", [], environment, caller, timeout=30,
            )
            if overrides is None:
                assert admitted.returncode != 0
                assert admitted.stdout.strip() != "legacy"
            else:
                assert admitted.returncode == 0, admitted.stderr
                assert admitted.stdout.strip() == "legacy"
    finally:
        policy.write_bytes(original_policy)
    assert not (profile / ".agent-pull-requests").exists()
    phase("initial installation")
    initial = _run(init, [], environment, caller, timeout=300)
    phase(f"initial installation returned {initial.returncode}")
    assert initial.returncode == 0, initial.stderr
    receipt, = (profile / ".copilot-extensions" / "marketplaces").glob(
        "*/plugins/agent-pull-requests/install.json",
    )
    plugin = receipt.parent
    before_receipt = json.loads(receipt.read_text("utf-8"))
    old_version = json.loads((payload / "plugin.json").read_text("utf-8"))["version"]
    old_python = plugin / "versions" / old_version / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    state_root = plugin / "state"
    saved = _pending(state_root, old_python)
    run_root = plugin / "run"
    reference = None
    started = False
    try:
        # Once attempted, cleanup inspects/stops through the governed entry even
        # if a caller loses the startup response after the owner commits.
        started = True
        phase("first owner startup")
        result = _run_cutover(
            command, ["restart", "--json"], environment, caller,
            run_root=run_root, phase="startup", timeout=_scenario_remaining(scenario_deadline),
        )
        phase(f"first owner startup returned {result.returncode}")
        assert result.returncode == 0, (
            f"{result.stderr}\n{json.dumps(_cutover_failure_details(run_root), sort_keys=True)}"
        )
        assert json.loads(result.stdout) == {"restarted": True, "was_running": False}
        phase("initial owner health")
        result = _run(command, ["watch", "health", "--json"], environment, caller, timeout=60)
        phase(f"initial owner health returned {result.returncode}")
        assert result.returncode == 0, result.stderr
        old = json.loads(result.stdout)["instance"]
        reference = process_exit.open_process(old["pid"], old["startTime"])
        before_route = routing.read_table(run_root, strict=True)
        journal = json.loads((run_root / "watch-owner-transaction.json").read_text("utf-8"))
        archive = run_root / "watch-owner-history" / f'{journal["transactionId"]}.json'
        archived = archive.read_bytes()
        old_completion = plugin / "versions" / old_version / api.RUNTIME_SLOT_COMPLETION_FILE
        completed = old_completion.read_bytes()
        new_version = "2.0.0"
        assert new_version != old_version
        _refresh_release(payload, new_version)
        # No direct stamp, activation CAS, manual snapshot or runtime edit.
        phase("normal live-owner upgrade")
        result = _run_cutover(
            init, [], environment, caller,
            run_root=run_root, phase="upgrade", timeout=_scenario_remaining(scenario_deadline),
        )
        phase(f"normal live-owner upgrade returned {result.returncode}")
        if result.returncode != 0:
            pytest.fail(
                f"Owned upgrade refused:\n{result.stderr}\n"
                f"{json.dumps(_cutover_failure_details(run_root), sort_keys=True)}",
                pytrace=False,
            )
        assert reference.wait(0)
        assert old_completion.read_bytes() == completed
        assert archive.read_bytes() == archived
        after_receipt = json.loads(receipt.read_text("utf-8"))
        assert after_receipt["generation"] > before_receipt["generation"]
        assert after_receipt["payload"]["version"] == new_version
        assert after_receipt["payload"]["root"] == before_receipt["payload"]["root"]
        phase("upgraded owner health")
        result = _run(command, ["watch", "health", "--json"], environment, caller, timeout=60)
        phase(f"upgraded owner health returned {result.returncode}")
        assert result.returncode == 0, result.stderr
        health = json.loads(result.stdout)
        transaction = json.loads((run_root / "watch-owner-transaction.json").read_text("utf-8"))
        assert transaction["phase"] == "complete"
        assert transaction["exited"] == ["predecessor"]
        assert transaction["predecessor"] == old
        assert transaction["successor"] == health["instance"]
        assert (run_root / "watch-owner-history" / f'{transaction["transactionId"]}.json').is_file()
        assert health["instance"]["pid"] != old["pid"]
        assert health["instance"]["runtimeVersion"] == new_version
        assert all(health[field] for field in ("writerHeld", "readReady", "effectsEnabled"))
        after_route = routing.read_table(run_root, strict=True)
        assert after_route["active"]["version"] == new_version
        assert after_route["active"]["generation"] > before_route["active"]["generation"]
        assert after_route["previous"] == before_route["active"]
        assert all(entry in after_route[publication_history.KEY]["entries"]
                   for entry in before_route[publication_history.KEY]["entries"])
        for name in (api.CURRENT_VERSION_FILE, api.LAST_KNOWN_GOOD_FILE):
            assert (plugin / name).read_text("utf-8").strip() == new_version
        manifest = json.loads((plugin / "deploy-manifest.json").read_text("utf-8"))
        assert manifest["source"]["version"] == new_version
        assert read_subscriptions_state(state_root) == [saved]
        assert not (profile / ".agent-pull-requests").exists()
        assert not (profile / ".local" / "bin").exists()
        assert not list(caller.iterdir())
        phase("same-version owner restart")
        restarted = _run(
            command, ["restart", "--json"], environment, caller,
            timeout=_scenario_remaining(scenario_deadline),
        )
        phase(f"same-version owner restart returned {restarted.returncode}")
        assert restarted.returncode == 0, restarted.stderr
        assert json.loads(restarted.stdout) == {"restarted": True, "was_running": True}
        phase("restarted owner health")
        result = _run(command, ["watch", "health", "--json"], environment, caller, timeout=60)
        phase(f"restarted owner health returned {result.returncode}")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["instance"]["instanceId"] != health["instance"]["instanceId"]
        assert read_subscriptions_state(state_root) == [saved]
        phase("all lifecycle body assertions passed")
    finally:
        try:
            if started:
                body_error = sys.exc_info()[1]
                if body_error is not None:
                    print(
                        f"Owned lifecycle body failed ({type(body_error).__name__}): {body_error}",
                        flush=True,
                    )
                phase("governed cleanup")
                stopped = _run(
                    command, ["stop", "--json"], environment, caller, timeout=150,
                )
                phase(f"governed cleanup returned {stopped.returncode}")
                assert stopped.returncode == 0, stopped.stderr
        finally:
            if reference is not None:
                reference.close()
