"""Registered-origin acquisition: real SDK identity, synthetic CLI effects only."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import _installation_context as primitive
from worktree_manager import engine_origin as origin_api

PLUGIN = "agent-worktrees"
DEFINITION = {
    "source": {
        "source": "github", "repo": "Example/Extensions", "ref": "release/canary",
        "location": "unchanged-native-location",
    },
}


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    cli = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    cli.write_text("synthetic executable; never launched", encoding="utf-8")
    cli.chmod(0o700)
    settings = home / "settings.json"
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {"fixture": copy.deepcopy(DEFINITION)},
        "enabledPlugins": {f"{PLUGIN}@fixture": False},
    }), encoding="utf-8")
    normalized = primitive.normalize_source(DEFINITION["source"], home)
    identity = primitive.source_identity(normalized, "fixture")
    env = {
        "HOME": str(tmp_path / "account"), "USERPROFILE": str(tmp_path / "account"),
        "PATH": str(tools), "COPILOT_HOME": str(tmp_path / "unrelated-home"),
        "COPILOT_CACHE_HOME": str(tmp_path / "cache"),
        "GIT_DIR": str(tmp_path / "must-not-route-git"),
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
    }
    calls = []
    native = origin_api._native_acquisition

    def forbidden(*args, **kwargs):
        pytest.fail("unexpected native acquisition boundary")

    monkeypatch.setattr(origin_api, "_native_acquisition", forbidden)
    return SimpleNamespace(
        home=home, cli=cli, settings=settings, env=env, calls=calls, identity=identity,
        payload=home / "installed-plugins/fixture" / PLUGIN,
        native=native,
    )


def _select(case, **kwargs):
    return origin_api.select_registered_origin(
        primitive, copilot_home=case.home, plugin_id=PLUGIN,
        source_fingerprint=case.identity["fingerprint"], **kwargs,
    )


def _materialize(case, *, name=PLUGIN, installer=True):
    case.payload.mkdir(parents=True, exist_ok=True)
    (case.payload / "plugin.json").write_text(json.dumps({
        "name": name, "version": "1.0.0",
    }), encoding="utf-8")
    if installer:
        scripts = case.payload / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "install.sh").write_text("#!/bin/sh\nexit 0\n")
        (scripts / "install.ps1").write_text("exit 0\n")
    _write_ledger(case)


def _write_ledger(case, *, entries=None):
    if entries is None:
        entries = [{
            "name": PLUGIN, "marketplace": "fixture", "version": "1.0.0",
            "cache_path": str(case.payload), "enabled": False,
        }]
    (case.home / "config.json").write_text(
        "// User settings belong in settings.json.\n// Managed native ledger.\n"
        + json.dumps({"installedPlugins": entries}), encoding="utf-8",
    )


def _effect(case, monkeypatch, *, after=None):
    def acquire(argv, environment, cwd, **kwargs):
        case.calls.append((argv, environment, cwd))
        assert environment["HOME"] == case.env["HOME"]
        assert environment["USERPROFILE"] == case.env["USERPROFILE"]
        assert environment["COPILOT_HOME"] == str(case.home)
        assert "GIT_DIR" not in environment
        assert environment["GIT_SSH_COMMAND"] == case.env["GIT_SSH_COMMAND"]
        assert cwd != Path.cwd()
        _materialize(case)
        if after is not None:
            after()
        return subprocess.CompletedProcess(
            argv, 0, "updated successfully (v1.0.0, already at latest)", "",
        )

    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)


@pytest.mark.parametrize("state", ["corrupt-same-version", "whole-payload-missing", "missing-ledger"])
def test_refresh_registered_origin_rehydrates_stable_true_source(case, monkeypatch, state):
    if state != "missing-ledger":
        _materialize(case)
    selected = _select(case)
    if state == "whole-payload-missing":
        case.payload.rename(case.payload.with_name("payload-backup"))
    elif state == "corrupt-same-version":
        script = "install.ps1" if origin_api._WINDOWS else "install.sh"
        (case.payload / "scripts" / script).write_text("corrupt fixture bytes\n")
    before_env = dict(case.env)
    before_definition = json.loads(case.settings.read_text())[
        "extraKnownMarketplaces"
    ]
    _effect(case, monkeypatch)
    actual = origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    verb = "install" if state == "missing-ledger" else "update"
    assert Path(case.calls[0][0][0]) == case.cli
    assert case.calls[0][0][1:] == ["plugin", verb, f"{PLUGIN}@fixture"]
    assert actual["source"]["canonical"] == "github:example/extensions"
    assert actual["source"]["ref"] == "release/canary"
    assert actual["sourceFingerprint"] == case.identity["fingerprint"]
    assert actual["payloadRoot"] == str(case.payload)
    assert selected.definition == DEFINITION
    assert origin_api._registrations(case.home) == before_definition
    assert case.env == before_env


def test_select_missing_payload_uses_registration_not_directory_bytes(case):
    selected = _select(case, payload_hint=case.payload)
    assert not selected.payload.exists()
    assert selected.source == f"{PLUGIN}@fixture"
    assert selected.provenance["source"]["kind"] == "github"
    assert selected.definition == DEFINITION


def test_attributable_native_path_selects_its_registered_identity_without_legacy_owner(case):
    with pytest.raises(RuntimeError, match="captured identity or attributable payload"):
        origin_api.select_registered_origin(
            primitive, copilot_home=case.home, plugin_id=PLUGIN, source_fingerprint=None,
        )
    selected = origin_api.select_registered_origin(
        primitive, copilot_home=case.home, plugin_id=PLUGIN, source_fingerprint=None,
        payload_hint=case.payload,
    )
    assert selected.provenance["sourceFingerprint"] == case.identity["fingerprint"]
    assert selected.payload == case.payload


def test_true_registered_directory_source_is_not_relabelled_github(case, monkeypatch):
    checkout = case.home.parent / "declared-checkout"
    checkout.mkdir()
    definition = {"source": {"source": "directory", "path": str(checkout)}}
    case.settings.write_text(json.dumps({"extraKnownMarketplaces": {"fixture": definition}}))
    normalized = primitive.normalize_source(definition["source"], case.home)
    expected = primitive.source_identity(normalized, "fixture")
    selected = origin_api.select_registered_origin(
        primitive, copilot_home=case.home, plugin_id=PLUGIN,
        source_fingerprint=expected["fingerprint"],
    )
    _effect(case, monkeypatch)
    actual = origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert actual["source"]["kind"] == "directory"
    assert actual["source"]["canonical"] == "directory:" + str(checkout)
    with pytest.raises(RuntimeError):
        _select(case)


def test_select_alias_ambiguity_requires_exact_scope(case):
    settings = origin_api._registrations(case.home)
    settings["alias"] = copy.deepcopy(DEFINITION)
    case.settings.write_text(json.dumps({"extraKnownMarketplaces": settings}))
    with pytest.raises(RuntimeError, match="ambiguous"):
        _select(case)
    assert _select(case, marketplace_id=case.identity["marketplaceId"]).source.endswith("@fixture")
    alias = case.home / "installed-plugins/alias" / PLUGIN
    assert _select(case, payload_hint=alias).source.endswith("@alias")


@pytest.mark.parametrize("mismatch", ["fingerprint", "marketplace", "payload"])
def test_select_rejects_foreign_identity(case, mismatch):
    kwargs = {
        "copilot_home": case.home, "plugin_id": PLUGIN,
        "source_fingerprint": case.identity["fingerprint"],
    }
    if mismatch == "fingerprint":
        kwargs["source_fingerprint"] = "sha256:" + "0" * 64
    elif mismatch == "marketplace":
        kwargs["marketplace_id"] = "foreign--0000000000000000"
    else:
        kwargs["payload_hint"] = case.home / "foreign-payload"
    with pytest.raises(RuntimeError):
        origin_api.select_registered_origin(primitive, **kwargs)


@pytest.mark.parametrize("descriptor", [
    {}, {"source": "github", "repo": ""},
    {"source": "opaque", "id": "not-native-acquisition"},
    {"source": "github", "repo": "Foreign/Repo", "canonical": "github:example/extensions"},
])
def test_select_cannot_mint_authority_from_missing_or_receipt_only_bytes(case, descriptor):
    case.settings.write_text(json.dumps({
        "extraKnownMarketplaces": {"fixture": {"source": descriptor}},
    }))
    with pytest.raises(RuntimeError):
        _select(case)


@pytest.mark.parametrize("timing", ["before", "after"])
@pytest.mark.parametrize("change", ["ref", "location", "repo"])
def test_refresh_revalidates_complete_definition_before_and_after_cli(
    case, monkeypatch, timing, change,
):
    selected = _select(case)

    def alter():
        definition = copy.deepcopy(DEFINITION)
        definition["source"][change] = "changed-source-choice"
        case.settings.write_text(json.dumps({
            "extraKnownMarketplaces": {"fixture": definition},
        }))

    if timing == "before":
        alter()
    else:
        _effect(case, monkeypatch, after=alter)
    with pytest.raises(RuntimeError):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert len(case.calls) == (1 if timing == "after" else 0)


@pytest.mark.parametrize("failure", [
    "no-files", "foreign-manifest", "foreign-marketplace", "no-installer", "indirect-installer",
])
def test_cli_zero_and_already_latest_are_not_materialization_proof(case, monkeypatch, failure):
    selected = _select(case)

    def effect(argv, environment, cwd, **kwargs):
        if failure != "no-files":
            _materialize(
                case, name="foreign-plugin" if failure == "foreign-manifest" else PLUGIN,
                installer=failure != "no-installer",
            )
        if failure == "indirect-installer":
            script = case.payload / "scripts" / ("install.ps1" if origin_api._WINDOWS else "install.sh")
            target = case.payload / "unrelated"
            script.rename(target)
            try:
                script.symlink_to(target)
            except OSError as error:
                pytest.skip(f"fixture symlink unavailable: {error}")
        if failure == "foreign-marketplace":
            path = case.payload / "plugin.json"
            manifest = json.loads(path.read_text())
            manifest["marketplace"] = "foreign"
            path.write_text(json.dumps(manifest))
        return subprocess.CompletedProcess(argv, 0, "already at latest", "")

    monkeypatch.setattr(origin_api, "_native_acquisition", effect)
    with pytest.raises(RuntimeError):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)


@pytest.mark.parametrize("ledger", ["malformed", "duplicate", "foreign-path"])
def test_ledger_errors_do_not_trigger_install_fallback(case, ledger):
    if ledger == "malformed":
        (case.home / "config.json").write_text("// native\n{broken")
    else:
        entry = {"name": PLUGIN, "marketplace": "fixture", "cache_path": str(case.payload)}
        if ledger == "duplicate":
            _write_ledger(case, entries=[entry, entry])
        else:
            entry["cache_path"] = str(case.home / "foreign")
            _write_ledger(case, entries=[entry])
    with pytest.raises(RuntimeError):
        _select(case)


def test_native_error_is_bounded_redacted_and_not_replayed(case, monkeypatch):
    selected = _select(case)
    calls = []

    def denied(argv, environment, cwd, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 7, "", "authorization token password ghp_private_value\n" + "detail " * 1000,
        )

    monkeypatch.setattr(origin_api, "_native_acquisition", denied)
    with pytest.raises(RuntimeError) as error:
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert "exit 7" in str(error.value)
    assert "ghp_private_value" not in str(error.value)
    assert len(str(error.value)) < 700
    assert len(calls) == 1


def test_native_zero_cannot_hide_inconsistent_materialization_ledger(case, monkeypatch):
    selected = _select(case)

    def alter_version():
        _write_ledger(case, entries=[{
            "name": PLUGIN, "marketplace": "fixture", "version": "2.0.0",
            "cache_path": str(case.payload),
        }])

    _effect(case, monkeypatch, after=alter_version)
    with pytest.raises(RuntimeError, match="versions differ"):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)


def test_native_windows_batch_transport_uses_existing_sdk_quoting(case, monkeypatch):
    selected = _select(case)
    batch = case.cli.with_name("copilot wrapper.cmd")
    batch.write_text("unused fixture")
    batch.chmod(0o700)
    cmd = case.cli.with_name("cmd.exe")
    cmd.write_text("unused fixture")
    cmd.chmod(0o700)
    case.env["COMSPEC"] = str(cmd)
    monkeypatch.setattr(origin_api, "_WINDOWS", True)
    _effect(case, monkeypatch)
    origin_api.refresh_registered_origin(
        primitive, selected, environment=case.env, copilot_command=(str(batch),),
    )
    argv, env, _ = case.calls[0]
    assert argv[:6] == [str(cmd), "/d", "/v:off", "/s", "/c", "%COPILOT_VENUE_BATCH_EXECUTABLE%"]
    assert env["COPILOT_VENUE_BATCH_EXECUTABLE"] == '"' + str(batch) + '"'
    assert argv[-3:] == ["plugin", "install", selected.source]


def test_native_powershell_command_prefix_remains_argv_not_shell_text(case, monkeypatch):
    selected = _select(case)
    host = case.cli.with_name("pwsh.exe")
    host.write_text("unused fixture")
    host.chmod(0o700)
    wrapper = str(case.home / "copilot wrapper.ps1")
    _effect(case, monkeypatch)
    prefix = (str(host), "-NoProfile", "-File", wrapper)
    origin_api.refresh_registered_origin(
        primitive, selected, environment=case.env, copilot_command=prefix,
    )
    assert case.calls[0][0] == [*prefix, "plugin", "install", selected.source]


def test_native_timeout_is_explicit_and_not_replayed(case, monkeypatch):
    _write_ledger(case)
    selected = _select(case)
    calls = []

    def timeout(argv, environment, cwd, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 180)

    monkeypatch.setattr(origin_api, "_native_acquisition", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert len(calls) == 1


def test_owned_windows_job_quiesces_once_on_timeout(case, monkeypatch):
    events = []

    class Process:
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(["copilot"], timeout)
            events.append("wait")
            return 1

    class Job:
        def terminate_and_wait(self, timeout):
            events.append("quiesce")

        def close(self):
            events.append("close")

    monkeypatch.setattr(origin_api, "_WINDOWS", True)
    monkeypatch.setattr(
        origin_api, "spawn_sync_in_kill_on_close_job", lambda *args, **kwargs: (Process(), Job()),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        case.native(["copilot"], case.env, case.home)
    assert events == ["quiesce", "close", "wait"]


def test_registered_url_alias_preserves_full_definition(case, monkeypatch):
    definition = {"source": {"source": "url", "url": "https://example.test/repo.git", "ref": "v1"}}
    case.settings.write_text(json.dumps({"extraKnownMarketplaces": {"fixture": definition}}))
    fp = primitive.source_identity(primitive.normalize_source(definition["source"]), "fixture")
    selected = origin_api.select_registered_origin(
        primitive, copilot_home=case.home, plugin_id=PLUGIN, source_fingerprint=fp["fingerprint"],
    )
    _effect(case, monkeypatch)
    actual = origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert actual["source"]["kind"] == "git"
    assert selected.definition == definition == origin_api._registrations(case.home)["fixture"]


def test_registered_user_local_origin_remains_in_its_original_scope(case, monkeypatch):
    local = case.home / "settings.local.json"
    local.write_text(case.settings.read_text(), encoding="utf-8")
    case.settings.write_text("{}", encoding="utf-8")
    before = local.read_bytes(), case.settings.read_bytes()
    selected = _select(case)
    _effect(case, monkeypatch)
    actual = origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert actual["sourceFingerprint"] == case.identity["fingerprint"]
    assert (local.read_bytes(), case.settings.read_bytes()) == before


@pytest.mark.parametrize("unrelated", [
    {"source": {"source": "npm", "package": "@example/plugins"}},
    {"source": {"source": "git-subdir", "url": "https://example.test/other.git", "path": "sub"}},
    {"source": {"source": "directory", "path": "/definitely-missing-unrelated-marketplace"}},
])
def test_unrelated_native_registration_does_not_block_owner(case, unrelated):
    settings = {"fixture": DEFINITION, "unrelated": unrelated}
    case.settings.write_text(json.dumps({"extraKnownMarketplaces": settings}))
    assert _select(case).source == f"{PLUGIN}@fixture"


def test_invalid_selected_owner_is_not_hidden_by_valid_unrelated_origin(case):
    case.settings.write_text(json.dumps({"extraKnownMarketplaces": {
        "fixture": {"source": {"source": "github", "repo": ""}},
        "unrelated": {"source": {"source": "github", "repo": "Other/Repo"}},
    }}))
    with pytest.raises(RuntimeError):
        _select(case, marketplace_id=case.identity["marketplaceId"], payload_hint=case.payload)


def test_matching_deleted_directory_still_fails_strict_admission(case):
    directory = case.home.parent / "deleted-owner"
    directory.mkdir()
    descriptor = {"source": "directory", "path": str(directory)}
    expected = primitive.source_identity(primitive.normalize_source(descriptor), "fixture")
    directory.rmdir()
    case.settings.write_text(json.dumps({"extraKnownMarketplaces": {
        "fixture": {"source": descriptor}, "unrelated": DEFINITION,
    }}))
    with pytest.raises(RuntimeError, match="resolve|directory"):
        origin_api.select_registered_origin(
            primitive, copilot_home=case.home, plugin_id=PLUGIN,
            source_fingerprint=expected["fingerprint"],
        )


def test_unconfirmed_cleanup_retains_scratch_and_custody(case, monkeypatch):
    _write_ledger(case)
    selected = _select(case)
    failure = origin_api.OwnedCleanupError(SimpleNamespace(pid=123))

    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(origin_api, "_native_acquisition", fail)
    with pytest.raises(origin_api.OwnedCleanupError) as error:
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    try:
        assert error.value is failure and failure.neutral_directory.is_dir()
    finally:
        shutil.rmtree(failure.neutral_directory)


@pytest.mark.parametrize("configured,budget", [
    (None, 180), ("600", 600), ("1.2e3", 1200), ("3600", 3600),
])
def test_windows_sharing_update_retries_within_original_budget(case, monkeypatch, configured, budget):
    _write_ledger(case)
    selected = _select(case)
    if configured is not None:
        case.env["WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"] = configured
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(origin_api, "_WINDOWS", True)
    monkeypatch.setattr(origin_api, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(origin_api, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                        raising=False)

    def acquire(argv, environment, cwd, *, timeout=180):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            clock[0] += 60
            return subprocess.CompletedProcess(argv, 1, "", "sharing (os error 32)")
        _materialize(case)
        return subprocess.CompletedProcess(argv, 0, "updated", "")

    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    actual = origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert actual["sourceFingerprint"] == case.identity["fingerprint"]
    assert timeouts == [budget, budget - 61]
    assert clock[0] == 61


@pytest.mark.parametrize("configured", [
    "", "0", "-1", "nan", "inf", "-inf", "3601", "1e300", "invalid",
])
def test_invalid_acquisition_budget_refuses_before_native_acquisition(case, configured):
    _write_ledger(case)
    selected = _select(case)
    case.env["WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"] = configured
    with pytest.raises(RuntimeError, match="positive finite number no greater than 3600"):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)


def test_configured_acquisition_timeout_is_not_replayed(case, monkeypatch):
    _write_ledger(case)
    selected = _select(case)
    case.env["WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"] = "600"
    calls = []
    monkeypatch.setattr(origin_api, "monotonic", lambda: 0)

    def acquire(argv, environment, cwd, *, timeout):
        calls.append(timeout)
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    with pytest.raises(RuntimeError, match="timed out"):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert calls == [600]


@pytest.mark.parametrize("windows,installed,detail", [
    (True, False, "sharing (os error 32)"),
    (False, True, "sharing (os error 32)"),
    (True, True, "permission denied; sharing (os error 32)"),
    (True, True, "authentication required; sharing (os error 32)"),
    (True, True, "policy refusal; sharing (os error 32)"),
    (True, True, "checksum mismatch; sharing (os error 32)"),
    (True, True, "signature invalid; sharing (os error 32)"),
    (True, True, "CERT_HAS_EXPIRED; sharing (os error 32)"),
    (True, True, "ERR_TLS_CERT_ALTNAME_INVALID; sharing (os error 32)"),
    (True, True, "sharing (os error 32); access failure (os error 5)"),
    (True, True, "sharing (os error 320)"),
    (True, True, "unknown native failure"),
    (True, True, "sharing (os error 32)\n" + "x" * 8192),
])
def test_sharing_recovery_never_replays_install_posix_or_refusal(
    case, monkeypatch, windows, installed, detail,
):
    if installed:
        _write_ledger(case)
    selected = _select(case)
    calls = []
    monkeypatch.setattr(origin_api, "_WINDOWS", windows)

    def acquire(argv, environment, cwd, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", detail)

    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    with pytest.raises(RuntimeError):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert len(calls) == 1


@pytest.mark.parametrize("returncode", [2, 7, 124, 137, -9, 3221225786])
def test_sharing_recovery_does_not_replay_other_exit_kinds(case, monkeypatch, returncode):
    _write_ledger(case)
    selected = _select(case)
    calls = []
    monkeypatch.setattr(origin_api, "_WINDOWS", True)

    def acquire(argv, environment, cwd, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, "", "sharing (os error 32)")

    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    with pytest.raises(RuntimeError):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert len(calls) == 1


@pytest.mark.parametrize("detail", ["[WinError 32] sharing", "lock (os error 33)"])
def test_permanent_windows_sharing_is_bounded(case, monkeypatch, detail):
    _write_ledger(case)
    selected = _select(case)
    calls = []
    monkeypatch.setattr(origin_api, "_WINDOWS", True)
    monkeypatch.setattr(origin_api, "sleep", lambda seconds: None, raising=False)

    def acquire(argv, environment, cwd, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", detail)

    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    with pytest.raises(RuntimeError):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert len(calls) == 3


@pytest.mark.parametrize("change", ["definition", "unrelated-registration", "ledger"])
def test_sharing_retry_revalidates_after_backoff(case, monkeypatch, change):
    _write_ledger(case)
    selected = _select(case)
    calls = []
    monkeypatch.setattr(origin_api, "_WINDOWS", True)

    def alter(seconds):
        if change == "ledger":
            _write_ledger(case, entries=[])
        else:
            document = json.loads(case.settings.read_text())
            if change == "definition":
                document["extraKnownMarketplaces"]["fixture"]["source"]["ref"] = "another-ref"
            else:
                document["extraKnownMarketplaces"]["other"] = DEFINITION
            case.settings.write_text(json.dumps(document))

    def acquire(argv, environment, cwd, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "sharing (os error 32)")

    monkeypatch.setattr(origin_api, "sleep", alter, raising=False)
    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    with pytest.raises(RuntimeError, match="changed|[Rr]egister"):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert len(calls) == 1


@pytest.mark.parametrize("configured,budget", [(None, 180), ("600", 600)])
def test_sharing_retry_does_not_extend_exhausted_deadline(case, monkeypatch, configured, budget):
    _write_ledger(case)
    selected = _select(case)
    if configured is not None:
        case.env["WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"] = configured
    clock = [0.0]
    calls = []
    monkeypatch.setattr(origin_api, "_WINDOWS", True)
    monkeypatch.setattr(origin_api, "monotonic", lambda: clock[0], raising=False)

    def acquire(argv, environment, cwd, **kwargs):
        calls.append(argv)
        clock[0] = budget
        return subprocess.CompletedProcess(argv, 1, "", "sharing (os error 32)")

    def unexpected_sleep(seconds):
        pytest.fail("An exhausted acquisition budget must not back off or launch again")

    monkeypatch.setattr(origin_api, "sleep", unexpected_sleep, raising=False)
    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    with pytest.raises(RuntimeError):
        origin_api.refresh_registered_origin(primitive, selected, environment=case.env)
    assert len(calls) == 1


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="explicit native Windows sharing-recovery control",
)
@pytest.mark.parametrize("release_holder", [True, False])
def test_windows_sharing_recovery_preserves_real_directory_holder(
    case, monkeypatch, release_holder,
):
    _materialize(case)
    selected = _select(case)
    case.env["WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"] = "600"
    before = case.settings.read_bytes(), (case.home / "config.json").read_bytes()
    cli = case.home / "native_cli.py"
    cli.write_text(
        "import pathlib,sys\n"
        "root=pathlib.Path(sys.argv[1]); target=root.with_name('payload-move')\n"
        "assert sys.argv[2:] == ['plugin','update','agent-worktrees@fixture']\n"
        "try:\n"
        " root.rename(target); target.rename(root)\n"
        "except OSError as error:\n"
        " print('native sharing (os error %s)' % error.winerror, file=sys.stderr)\n"
        " raise SystemExit(1)\n",
        encoding="utf-8",
    )
    holder = subprocess.Popen(
        [sys._base_executable, "-I", "-c",
         "import sys; print('ready',flush=True); sys.stdin.buffer.read(1)"],
        cwd=case.payload, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **origin_api.no_window_kwargs(),
    )
    attempts = []

    def acquire(*args, **kwargs):
        assert 180 < kwargs["timeout"] <= 600
        result = case.native(*args, **kwargs)
        attempts.append(result.returncode)
        if len(attempts) == 1:
            assert result.returncode == 1 and "os error 32" in result.stderr
            assert holder.poll() is None
            assert case.payload.is_dir()
            if release_holder:
                holder.communicate(input=b"x", timeout=10)
                assert holder.returncode == 0
        return result

    monkeypatch.setattr(origin_api, "_native_acquisition", acquire)
    try:
        assert holder.stdout.readline() in {b"ready\n", b"ready\r\n"}
        command = (sys._base_executable, "-I", str(cli), str(case.payload))
        if release_holder:
            origin_api.refresh_registered_origin(
                primitive, selected, environment=case.env, copilot_command=command,
            )
            assert attempts == [1, 0]
        else:
            with pytest.raises(RuntimeError, match="os error 32"):
                origin_api.refresh_registered_origin(
                    primitive, selected, environment=case.env, copilot_command=command,
                )
            assert attempts == [1, 1, 1]
            assert holder.poll() is None
            holder.communicate(input=b"x", timeout=10)
        assert case.payload.is_dir()
        assert (case.settings.read_bytes(), (case.home / "config.json").read_bytes()) == before
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        for stream in (holder.stdin, holder.stdout, holder.stderr):
            if stream is not None:
                stream.close()
