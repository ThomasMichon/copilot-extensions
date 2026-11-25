"""Tests for the process-boundary engine client (Phase 6b).

The Worktree Manager reaches the agent-worktrees engine ONLY by shelling out to
its ``--json`` verbs (never importing it). These tests drive that seam with a
faked ``subprocess.run`` + ``engine_path`` so no real engine is required, and
assert the parsing, the version-skew retry, and the error paths.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest
from machine_transport import MachineEntry, SSHEnvironment

from worktree_manager import engine_client as ec
from worktree_manager import companion_client as cc
from worktree_manager import machine_roster


@pytest.fixture(autouse=True)
def _reset_engine_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    ec.set_engine_command(None)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.delenv("AGENT_HOME", raising=False)


def _fake_completed(
    cmd: list[str], returncode: int = 0, stdout: str = "", stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def _hosting_policy() -> dict[str, Any]:
    return {
        "version": 1, "repository": str(Path.cwd()), "expose_agent": None,
        "sources": [], "denied_by": [], "allowed": True,
    }


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[list[str], dict[str, Any]], subprocess.CompletedProcess[str]],
) -> None:
    """Point the client at a fake engine binstub + a scripted ``subprocess.run``."""
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    ec.set_engine_command(None)
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda: ["/fake/agent-worktrees"]
    )
    def with_policy(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        verb = 3 if cmd[1:2] == ["--project"] else 1
        if cmd[verb:verb + 2] == ["agent-policy", "check"]:
            return _fake_completed(cmd, stdout=json.dumps(_hosting_policy()))
        return handler(cmd, kw)

    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: with_policy(cmd, kw))
    monkeypatch.setattr(
        "worktree_manager.engine_process.run", lambda cmd, **kw: with_policy(cmd, kw),
    )


def test_engine_environment_removes_parent_python_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited = {
        "PYTHONHOME": "/parent/python",
        "PYTHONPATH": "/parent/modules",
        "PYTHONEXECUTABLE": "/parent/python/bin/python",
        "VIRTUAL_ENV": "/parent/venv",
        "UV_INTERNAL__PYTHONHOME": "/uv/python",
        "__PYVENV_LAUNCHER__": "/parent/python",
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://example.invalid/simple")

    env = ec._engine_environment()

    assert not inherited.keys() & env.keys()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONSAFEPATH"] == "1"
    assert env["UV_DEFAULT_INDEX"] == "https://example.invalid/simple"


def test_worktree_status_bundle_uses_selected_engine_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = {"worktree_id": "wt-selected", "facts": {"claims": {"value": None}}}
    calls: list[tuple[str | None, list[str]]] = []
    def request(project: str | None, args: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append((project, args))
        return bundle
    monkeypatch.setattr(
        ec, "run_json",
        request,
    )
    assert cc.worktree_status_bundle("project", "wt-selected") is bundle
    assert calls == [("project", [
        "worktree-status-bundle", "--worktree", "wt-selected", "--json",
    ])]


@pytest.mark.parametrize("payload", [
    {"worktree_id": "other", "facts": {}},
    {"worktree_id": "wt-selected", "facts": []},
    {"error": "not found"},
    None,
])
def test_worktree_status_bundle_refuses_wrong_or_unavailable_identity(
    monkeypatch: pytest.MonkeyPatch, payload: Any,
) -> None:
    monkeypatch.setattr(ec, "run_json", lambda *a, **k: payload)
    with pytest.raises(ec.EngineError, match="selected worktree"):
        cc.worktree_status_bundle(None, "wt-selected")


def test_worktree_status_bundle_through_real_subprocess(tmp_path: Path) -> None:
    script = tmp_path / "example engine.py"
    script.write_text(
        "import json, sys\n"
        "assert sys.argv[1:] == ['--project', 'example project', "
        "'worktree-status-bundle', '--worktree', 'wt-selected', '--json']\n"
        "print(json.dumps({'worktree_id': 'wt-selected', 'facts': {"
        "'claims': {'value': {'resources': []}, 'confirmed': True, "
        "'observed_at': 1700000000}}}))\n",
        encoding="utf-8",
    )
    ec.set_engine_command([sys.executable, str(script)])
    try:
        bundle = cc.worktree_status_bundle("example project", "wt-selected")
    finally:
        ec.set_engine_command(None)
    assert bundle["facts"]["claims"]["value"]["resources"] == []


def test_engine_environment_preserves_differently_cased_posix_names(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not ec._is_parent_python_variable("virtual_env", windows=False)
    assert not ec._is_parent_python_variable("PythonPath", windows=False)
    assert ec._is_parent_python_variable("VIRTUAL_ENV", windows=False)
    assert ec._is_parent_python_variable("virtual_env", windows=True)


def _current_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload, "ok": True, "contract_version": 1,
        "projection": {
            "schema_version": 1, "quality": "current", "generation": "a" * 32,
            "revision": 1, "observed_at": 123, "roster_count": len(payload["worktrees"]),
            "selected_count": len(payload["worktrees"]),
        },
    }


_ONE_WT = _current_envelope({
    "version": 1,
    "worktrees": [
        {
            "id": "example-cloud1-win-20260813-1200-ab12",
            "repo": "dotfiles",
            "machine": "cloud1",
            "branch": "worktree/x",
            "title": "fix the thing",
            "state": "wip",
            "ahead": 2,
            "behind": 1,
            "dirty": True,
            "status": "active",
            "path": "/w/x",
        }
    ],
})


def test_list_worktrees_parses_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "--project" in cmd and "dotfiles" in cmd
        assert "list" in cmd and "--json" in cmd and "--classify" in cmd and "--current" in cmd
        return _fake_completed(cmd, stdout=json.dumps(_ONE_WT))

    _install_fake(monkeypatch, handler)
    wts = ec.list_worktrees("dotfiles")
    assert len(wts) == 1
    w = wts[0]
    assert w.repo == "dotfiles" and w.machine == "cloud1"
    assert w.state == "wip" and w.ahead == 2 and w.behind == 1 and w.dirty
    assert w.id4 == "ab12"
    assert w.sync_tag == "\u21912\u21931"
    assert w.title == "fix the thing"


def test_title_null_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _current_envelope({"version": 1, "worktrees": [{"id": "aaaa", "title": "null"}]})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(payload)))
    (w,) = ec.list_worktrees("dotfiles")
    assert w.title is None
    assert w.id4 == "aaaa"


@pytest.mark.parametrize("dirty", [0, 1, 4, 1100])
def test_list_worktrees_preserves_current_dirty_count_and_converts_typed_indicator(
    monkeypatch: pytest.MonkeyPatch, dirty: int,
) -> None:
    payload = _current_envelope({
        "worktrees": [{"id": "one", "dirty": dirty, "state": "wip"}],
    })
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(payload)))
    (row,) = ec.list_worktrees("project")
    assert row.dirty is (dirty > 0)
    assert row.raw["dirty"] == dirty
    assert type(row.raw["dirty"]) is int


def test_worktree_model_rejects_negative_dirty_counts() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        ec.worktree_from_dict({"id": "one", "dirty": -1})


def test_engine_absent_raises_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    ec.set_engine_command(None)
    monkeypatch.setattr(ec, "installed_engine_command", lambda: None)
    assert ec.engine_available() is False
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles")
    assert ei.value.install_hint is True


def test_classify_rejection_retries_without(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "--classify" in cmd:
            # An older engine rejects the unknown flag.
            return _fake_completed(cmd, returncode=2, stderr="unrecognized arguments: --classify")
        return _fake_completed(cmd, stdout=json.dumps(_ONE_WT))

    _install_fake(monkeypatch, handler)
    wts = ec.list_worktrees("dotfiles")
    assert len(wts) == 1
    # First attempt carried --classify; the retry dropped it.
    assert any("--classify" in c for c in calls)
    assert any("--classify" not in c for c in calls)
    assert all("--current" in c for c in calls)


def test_error_envelope_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"version": 1, "error": "no such project"})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload))
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles", classify=False)
    assert "no such project" in str(ei.value)


def _legacy_controller_engine(
    tmp_path: Path, stdout: str, *, stderr: str = "", returncode: int = 1,
) -> None:
    script = tmp_path / "legacy engine.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
        "sys.stderr.reconfigure(encoding='utf-8', errors='replace')\n"
        "assert sys.argv[-2:] == ['get', 'update-controller']\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    ec.set_engine_command([sys.executable, str(script)])


def test_legacy_controller_probe_preserves_utf8_controller_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    selected = "tools/\u66f4\u65b0.py"
    _legacy_controller_engine(
        tmp_path, selected + "\n", stderr=f"Selected {selected}\n", returncode=0,
    )
    assert ec.get_value("example", "update-controller") == selected


@pytest.mark.parametrize(("stdout", "stderr"), [
    ("  \u2717 Unknown key: 'update-controller'. Use 'get keys' to list available keys.\n", ""),
    ("\x1b[0;31m\u2717\x1b[0m Unknown key: 'update-controller'. Use 'get keys' to list available keys.\n", ""),
    ("", "Unknown key: 'update-controller'"),
    (json.dumps({"error": "Unknown key: 'update-controller'"}), ""),
])
def test_legacy_controller_probe_never_assumes_no_project_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str, stderr: str,
) -> None:
    from worktree_manager import project_update_controller

    project = tmp_path / "project"
    (project / ".agent-worktrees").mkdir(parents=True)
    (project / ".agent-worktrees" / "config.yaml").write_text(
        "update_controller:\n  windows: tools/update.py\n  linux: tools/update.py\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr(ec, "run_engine_passthrough", lambda *_args, **_kwargs: pytest.fail("unexpected update"))
    _legacy_controller_engine(tmp_path, stdout, stderr=stderr)
    with pytest.raises(ec.EngineFeatureUnavailable, match="cannot determine.*project update controller"):
        project_update_controller.handoff(["--project", "example"])

    authority = SimpleNamespace(configured=True, mode="project", project="example")
    with pytest.raises(ValueError, match="selected project update controller is unavailable"):
        project_update_controller.handoff(["--project", "example"], authority=authority)


def test_legacy_controller_probe_without_a_project_can_fall_back(tmp_path: Path) -> None:
    from worktree_manager import project_update_controller

    _legacy_controller_engine(
        tmp_path,
        "  \u2717 Cannot load config: No active project could be resolved. agent-worktrees discovers "
        "its context from the current directory (like git); run from inside a managed repo or "
        "worktree, or pass --project <name>.\n",
    )
    assert project_update_controller.handoff([]) is None
    with pytest.raises(ec.EngineError, match="no output"):
        project_update_controller.handoff(["--project", "example"])


@pytest.mark.parametrize(("stdout", "stderr", "returncode"), [
    ("unrelated output", "Could not resolve a project for 'get'.", 1),
    ("", "Could not resolve a project for 'get'. Additional failure.", 1),
    (json.dumps({"error": "No active project could be resolved. Additional failure."}), "", 1),
    ("", "Could not resolve a project for 'get'.", 2),
], ids=["mixed-channels", "extended-stderr", "extended-json", "wrong-exit"])
def test_legacy_controller_probe_rejects_unconfirmed_no_project(
    tmp_path: Path, stdout: str, stderr: str, returncode: int,
) -> None:
    from worktree_manager import project_update_controller

    _legacy_controller_engine(tmp_path, stdout, stderr=stderr, returncode=returncode)
    with pytest.raises(ec.EngineError):
        project_update_controller.handoff([])


@pytest.mark.parametrize(("returncode", "extra", "stderr", "expected"), [
    (1, "unrelated output\n", "", "no output"),
    (1, "", "permission denied", "permission denied"),
    (2, "", "", "no output"),
])
def test_legacy_controller_probe_does_not_hide_other_failures(
    tmp_path: Path, returncode: int, extra: str, stderr: str, expected: str,
) -> None:
    from worktree_manager import project_update_controller

    _legacy_controller_engine(
        tmp_path,
        "  \u2717 Unknown key: 'update-controller'. Use 'get keys' to list available keys.\n" + extra,
        stderr=stderr, returncode=returncode,
    )
    with pytest.raises(ec.EngineError, match=expected) as failure:
        project_update_controller.handoff(["--project", "example"])
    assert "unrelated output" not in str(failure.value)


def test_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout="not json"))
    with pytest.raises(ec.EngineError):
        ec.list_worktrees("dotfiles", classify=False)


def test_timeout_raises_engine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    ec.set_engine_command(None)
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda: ["/fake/agent-worktrees"]
    )

    def boom(cmd: list[str], **kw: Any) -> NoReturn:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles", classify=False)
    assert "timed out" in str(ei.value)


def test_project_passthrough_uses_resolved_installer_binstub(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(cmd=cmd, kwargs=kwargs)
        return _fake_completed(cmd, returncode=7)

    monkeypatch.setattr(
        ec, "project_binstub_command", lambda project: Path("/fake/demo")
    )
    monkeypatch.setattr(
        subprocess, "run", run,
    )

    assert ec.run_project_passthrough("demo", ["--worktree-id", "wt-1"]) == 7
    expected = [str(Path("/fake/demo")), "--worktree-id", "wt-1"]
    if os.name == "nt":
        expected = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(expected),
        ]
    assert seen["cmd"] == expected


def test_empty_worktrees_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps(_current_envelope({"version": 1, "worktrees": []}))))
    assert ec.list_worktrees("dotfiles") == []


def test_list_worktree_rows_preserves_picker_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _current_envelope({
        "version": 1,
        "worktrees": [{
            "id": "wt-ab12",
            "session_bridge_live": True,
            "live_intent": "Reviewing",
        }],
    })
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps(payload)))

    rows = ec.list_worktree_rows(
        "dotfiles", classify=False, mux_details=True, cache_only=True)

    assert rows == [{**payload["worktrees"][0], "_projection": payload["projection"], "_availability": None}]


def test_list_worktree_rows_uses_supplied_cancellable_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def runner(cmd: list[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return _fake_completed(
            cmd, stdout=json.dumps(_current_envelope({"version": 1, "worktrees": []})))

    _install_fake(
        monkeypatch,
        lambda cmd, kw: pytest.fail("subprocess.run must not be used"),
    )

    assert ec.list_worktree_rows("dotfiles", runner=runner) == []
    assert seen["cmd"][:1] == ["/fake/agent-worktrees"]
    assert seen["timeout"] == ec._DEFAULT_TIMEOUT


def test_current_worktree_status_uses_status_segment_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"version": 1, "id": "wt-ab12", "state": "wip", "closure": None}

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "status-segment" in cmd
        assert "--json" in cmd
        assert "--path" in cmd and "/w" in cmd
        assert "--fetch" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(payload))

    _install_fake(monkeypatch, handler)

    result = ec.current_worktree_status(path="/w")

    assert result == payload


def test_current_worktree_status_passes_fetch_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "--fetch" in cmd
        return _fake_completed(cmd, stdout=json.dumps({"version": 1, "id": None}))

    _install_fake(monkeypatch, handler)

    ec.current_worktree_status(path="/w", fetch=True)


def test_find_worktree_for_path_matches_exact_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wt_dir = tmp_path / "wt-ab12"
    wt_dir.mkdir()
    payload = _current_envelope({
        "version": 1,
        "worktrees": [{"id": "wt-ab12", "path": str(wt_dir)}],
    })

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "--cache-only" in cmd
        assert "--classify" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(payload))

    _install_fake(monkeypatch, handler)

    row = ec.find_worktree_for_path(str(wt_dir))

    assert row == {**payload["worktrees"][0], "_projection": payload["projection"], "_availability": None}


def test_find_worktree_for_path_matches_ancestor_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wt_dir = tmp_path / "wt-ab12"
    nested = wt_dir / "sub" / "dir"
    nested.mkdir(parents=True)
    payload = _current_envelope({
        "version": 1,
        "worktrees": [{"id": "wt-ab12", "path": str(wt_dir)}],
    })
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps(payload)))

    row = ec.find_worktree_for_path(str(nested))

    assert row == {**payload["worktrees"][0], "_projection": payload["projection"], "_availability": None}


def test_find_worktree_for_path_returns_none_when_untracked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _current_envelope({"version": 1, "worktrees": [{"id": "wt-ab12", "path": str(tmp_path / "other")}]})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps(payload)))

    assert ec.find_worktree_for_path(str(tmp_path / "unrelated")) is None


def test_repository_identity_uses_scoped_engine_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "account-for" in cmd:
            return _fake_completed(
                cmd,
                stdout=json.dumps({
                    "target": "example/example",
                    "account": "example-user",
                }),
            )
        return _fake_completed(cmd, stdout="secret-token\n")

    _install_fake(monkeypatch, handler)

    assert ec.repository_account("example") == "example-user"
    assert ec.repository_token("example", "example-user") == "secret-token"
    assert calls[1][-7:] == [
        "repos",
        "gh",
        "--",
        "auth",
        "token",
        "--user",
        "example-user",
    ]


def test_execution_leg_set_uses_json_blob_file_and_fencing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        blob_path = Path(cmd[cmd.index("--blob-file") + 1])
        seen["blob_path"] = blob_path
        seen["blob"] = json.loads(blob_path.read_text(encoding="utf-8"))
        seen["cmd"] = cmd
        return _fake_completed(
            cmd,
            stdout=json.dumps({
                "worktree_id": "wt-1",
                "execution_leg": {"binding_revision": 2},
            }),
        )

    _install_fake(monkeypatch, handler)

    result = ec.execution_leg_set(
        "example",
        "wt-1",
        provider="ahp",
        state="active",
        binding_revision=2,
        blob={"session_id": "session-1"},
        if_match_revision=1,
    )

    assert result["execution_leg"]["binding_revision"] == 2
    assert seen["blob"] == {"session_id": "session-1"}
    assert "--if-match-revision" in seen["cmd"]
    assert not seen["blob_path"].exists()


def test_execution_leg_lifecycle_reservation_uses_engine_owned_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _fake_completed(
            cmd,
            stdout=json.dumps({"reservation_token": "token-1"}),
        )

    _install_fake(monkeypatch, handler)

    assert ec.execution_leg_reserve(
        "example",
        "wt-1",
        provider="ahp",
        operation="ensure",
        owner="manager:test",
        owner_pid=123,
        owner_start_time="456",
        lease_seconds=45,
    )["reservation_token"] == "token-1"
    ec.execution_leg_release(
        "example",
        "wt-1",
        reservation_token="token-1",
    )

    assert "--operation" in calls[0]
    assert calls[0][calls[0].index("--operation") + 1] == "ensure"
    assert calls[0][calls[0].index("--reservation-owner") + 1] == "manager:test"
    assert calls[0][calls[0].index("--reservation-owner-pid") + 1] == "123"
    assert calls[0][calls[0].index("--reservation-owner-start-time") + 1] == "456"
    assert calls[0][calls[0].index("--lease-seconds") + 1] == "45"
    assert calls[1][calls[1].index("--reservation-token") + 1] == "token-1"


def test_execution_leg_get_classifies_only_conclusive_unsupported_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(
            cmd,
            returncode=2,
            stderr="invalid choice: 'execution-leg' (choose from 'list', 'resolve')",
        ),
    )
    with pytest.raises(ec.EngineFeatureUnavailable):
        ec.execution_leg_get("example", "wt-1")


def test_execution_leg_get_parse_failure_remains_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(cmd, stdout="not json"),
    )
    with pytest.raises(ec.EngineError) as error:
        ec.execution_leg_get("example", "wt-1")
    assert not isinstance(error.value, ec.EngineFeatureUnavailable)


def test_refresh_worktree_uses_exact_provider_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, list[str]] = {}

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = cmd
        return _fake_completed(
            cmd,
            stdout=json.dumps(_current_envelope({"version": 1, "worktrees": [{"id": "wt-ab12"}]})),
        )

    _install_fake(monkeypatch, handler)
    rows = ec.list_worktree_rows(
        "dotfiles",
        classify=True,
        mux_details=True,
        fresh=True,
        worktree_id="ab12",
        refresh=True,
    )

    assert len(rows) == 1 and rows[0]["id"] == "wt-ab12"
    assert rows[0]["_projection"]["quality"] == "current"
    assert seen["cmd"][-9:] == [
        "list", "--json", "--current", "--classify", "--mux-details", "--fresh",
        "--worktree-id", "ab12", "--refresh",
    ]


def test_refresh_worktree_does_not_backfill_or_list_fleet_for_older_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "--refresh" in cmd:
            return _fake_completed(
                cmd,
                returncode=2,
                stderr="unrecognized arguments: --worktree-id ab12 --refresh",
            )
        if "backfill-sessions" in cmd:
            return _fake_completed(cmd, stdout="Backfilled 1 session")
        return _fake_completed(
            cmd,
            stdout=json.dumps({
                "version": 1,
                "worktrees": [{"id": "wt-ab12"}, {"id": "wt-cd34"}],
            }),
        )

    _install_fake(monkeypatch, handler)
    with pytest.raises(ec.EngineError, match="unrecognized arguments"):
        ec.list_worktree_rows(
            "dotfiles", classify=True, mux_details=True, fresh=True,
            worktree_id="ab12", refresh=True,
        )
    assert len(calls) == 1 and "--current" in calls[0]


def test_list_worktree_sessions_parses_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"version": 1, "sessions": [{"id": "session-1", "is_head": True}]}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps(payload)))

    assert ec.list_worktree_sessions("dotfiles", "wt-ab12") == payload["sessions"]


def test_orphaned_obligations_parses_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "orphaned": [
            {"kind": "codespace", "ref": "cs-1", "source_worktree": "wt-old",
             "abandoned_at": "2026-10-01T00:00:00"},
        ],
        "count": 1,
    }
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _fake_completed(cmd, stdout=json.dumps(payload))

    _install_fake(monkeypatch, handler)
    assert ec.orphaned_obligations("dotfiles") == payload["orphaned"]
    assert any("orphans" in part for cmd in calls for part in cmd)


def test_orphaned_obligations_degrades_to_empty_on_engine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An older engine predating ``claims orphans`` (or any other engine
    failure) must never surface as a Picker crash -- this is a visibility
    nicety, not a required capability (see the function's own docstring)."""
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, returncode=2, stderr="unrecognized arguments: orphans"))
    assert ec.orphaned_obligations("dotfiles") == []


def test_orphaned_obligations_tolerates_a_non_list_or_missing_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps({"count": 0})))
    assert ec.orphaned_obligations("dotfiles") == []


def test_recent_worktree_messages_returns_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "version": 1,
        "session_id": "session-1",
        "messages": [{"role": "assistant", "text": "done"}],
    }
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps(payload)))

    assert ec.recent_worktree_messages(
        "dotfiles", "wt-ab12", limit=5) == payload


def test_inherited_provider_argv_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        ec.ENGINE_ARGV_ENV,
        json.dumps(["/runtime/python", "-m", "agent_worktrees"]),
    )
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    ec.set_engine_command(None)
    assert ec.engine_base_command() == [
        "/runtime/python", "-m", "agent_worktrees"
    ]


def test_explicit_command_override_wins_over_inherited_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        ec.ENGINE_ARGV_ENV,
        json.dumps(["/runtime/python", "-m", "agent_worktrees"]),
    )
    monkeypatch.setenv(ec.ENGINE_CMD_ENV, "/fake/engine")
    ec.set_engine_command(None)
    assert ec.engine_base_command() == ["/fake/engine"]


def test_accept_inherited_provider_removes_child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited = ["/runtime/python", "-m", "agent_worktrees"]
    monkeypatch.setenv(ec.ENGINE_ARGV_ENV, json.dumps(inherited))
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    assert ec.accept_inherited_engine_command() is None
    assert ec.ENGINE_ARGV_ENV not in os.environ
    assert ec.engine_base_command() == inherited


def test_invalid_inherited_provider_argv_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ec.ENGINE_ARGV_ENV, '{"not":"argv"}')
    ec.set_engine_command(None)
    with pytest.raises(ec.EngineError, match=ec.ENGINE_ARGV_ENV):
        ec.engine_base_command()


def test_accept_invalid_inherited_provider_keeps_recovery_commands_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ec.ENGINE_ARGV_ENV, '{"not":"argv"}')
    warning = ec.accept_inherited_engine_command()
    assert ec.ENGINE_ARGV_ENV not in os.environ
    assert warning and ec.ENGINE_ARGV_ENV in warning


def _assert_unsealed_runtime_command(argv: list[str] | None, python: Path, root: Path) -> None:
    assert argv == [str(python), "-B", "-m", "agent_worktrees"]
    assert not (root / "cache").exists()


def test_installed_engine_command_validates_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {
                "plugin": "agent-worktrees",
                "version": "1.2.3",
            },
            "venv": str(slot),
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    _assert_unsealed_runtime_command(ec.installed_engine_command(), python, root)


def test_installed_engine_command_rejects_non_object_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / ".agent-worktrees"
    root.mkdir(parents=True)
    (root / "deploy-manifest.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ec.installed_engine_command() is None


def test_installed_engine_command_allows_manifest_version_skew(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {
                "plugin": "agent-worktrees",
                "version": "1.2.2",
            },
            "venv": str(slot),
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    _assert_unsealed_runtime_command(ec.installed_engine_command(), python, root)


def test_installed_engine_command_falls_back_to_last_known_good(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.2"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text("missing", encoding="utf-8")
    (root / "last-known-good").write_text("1.2.2", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {"plugin": "agent-worktrees", "version": "1.2.3"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    _assert_unsealed_runtime_command(ec.installed_engine_command(), python, root)


def test_installed_engine_command_honors_agent_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {"plugin": "agent-worktrees", "version": "1.2.3"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    _assert_unsealed_runtime_command(ec.installed_engine_command(), python, root)


def test_installed_engine_command_rejects_marker_path_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / ".agent-worktrees"
    versions = root / "versions"
    legitimate = versions / "1.2.3"
    legit_python = (
        legitimate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    legit_python.parent.mkdir(parents=True)
    legit_python.write_text("", encoding="utf-8")
    (legitimate / ".install-complete.json").write_text("{}", encoding="utf-8")

    escaped = root / "outside"
    escaped_python = (
        escaped / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    escaped_python.parent.mkdir(parents=True)
    escaped_python.write_text("", encoding="utf-8")
    (escaped / ".install-complete.json").write_text("{}", encoding="utf-8")

    escape = "..\\outside" if os.name == "nt" else "../outside"
    (root / "current-version").write_text(escape, encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {"plugin": "agent-worktrees", "version": "1.2.3"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    _assert_unsealed_runtime_command(ec.installed_engine_command(), legit_python, root)


def test_installed_engine_command_prefers_versions_over_stray_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / ".agent-worktrees"
    versions = root / "versions"
    legitimate = versions / "1.2.3"
    legit_python = (
        legitimate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    legit_python.parent.mkdir(parents=True)
    legit_python.write_text("", encoding="utf-8")
    (legitimate / ".install-complete.json").write_text("{}", encoding="utf-8")

    stray = versions / "zzz"
    stray_python = stray / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    stray_python.parent.mkdir(parents=True)
    stray_python.write_text("", encoding="utf-8")
    (stray / ".install-complete.json").write_text("{}", encoding="utf-8")

    (root / "current-version").write_text("missing", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {"plugin": "agent-worktrees", "version": "1.2.3"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    _assert_unsealed_runtime_command(ec.installed_engine_command(), legit_python, root)


def test_run_json_can_preserve_structured_nonzero_result(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"ok": False, "reason": "in use"}
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(
            cmd, returncode=1, stdout=json.dumps(payload)
        ),
    )
    assert ec.run_json(
        "dotfiles", ["restart", "wt-1", "--json"], allow_nonzero=True
    ) == payload


def test_run_json_nonzero_without_envelope_preserves_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(
            cmd, returncode=2, stderr="unrecognized arguments: --future-flag"
        ),
    )
    with pytest.raises(ec.EngineError, match="--future-flag"):
        ec.run_json(
            "dotfiles",
            ["cleanup", "--future-flag", "--json"],
            allow_nonzero=True,
        )


def test_captured_engine_call_is_tui_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        seen.update(kw)
        return _fake_completed(cmd, stdout=json.dumps(_current_envelope({"version": 1, "worktrees": []})))

    _install_fake(monkeypatch, handler)
    ec.list_worktrees("dotfiles")
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["env"]["PYTHONSAFEPATH"] == "1"
    if os.name == "nt":
        assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW")


# ── resolve_launch_plan (slice 3) ─────────────────────────────────────────────

_RESUME_PLAN = {
    "action": "exec",
    "work_dir": "/w/x",
    "status_path": "/w/x",
    "cmd": ["copilot", "--resume=sess123"],
    "env": {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS": "/home/u/.dotfiles"},
    "worktree_id": "m-win-1200-ab12",
    "post_exit": True,
    "no_mux": True,
}


def test_resolve_resume_parses_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "resolve" in cmd and "--json" in cmd
        assert "--worktree-id" in cmd and "m-win-1200-ab12" in cmd
        assert "--new" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="m-win-1200-ab12")
    assert plan.is_exec and plan.no_mux is True
    assert plan.cmd == ["copilot", "--resume=sess123"]
    assert plan.work_dir == "/w/x" and plan.worktree_id == "m-win-1200-ab12"
    assert plan.post_exit is True


def test_resolve_retained_hint_keeps_selected_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        assert cmd[0] == "/selected/engine"
        assert kw["env"]["AGENT_WORKTREES_RETAINED_SEED_PLANS"] == "1"
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    ec.set_engine_command(["/selected/engine"])
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda *a, **k: pytest.fail("selected engine was bypassed"),
    )
    assert ec.resolve_launch_plan("demo", worktree_id="wt-a").is_exec
    assert len(seen) == 1


def test_preserved_selector_rollback_refuses_before_effects(tmp_path: Path) -> None:
    engine = tmp_path / "legacy_engine.py"
    effect = tmp_path / "effect.txt"
    engine.write_text(
        "import argparse,json\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser()\np.add_argument('--project')\n"
        "p.add_argument('verb',choices=['resolve'])\n"
        "p.add_argument('--json',action='store_true')\np.add_argument('--worktree-id')\n"
        f"p.parse_args()\nPath({str(effect)!r}).write_text('executed')\n"
        "print(json.dumps({'action':'exec','cmd':['copilot']}))\n",
        encoding="utf-8",
    )
    ec.set_engine_command([sys.executable, str(engine)])
    with pytest.raises(ec.EngineError, match="unrecognized arguments: --preserve-seed-id"):
        ec.run_json("demo", [
            "resolve", "--json", "--worktree-id", "wt-a", "--preserve-seed-id", "original-a",
        ])
    assert not effect.exists()


def test_resolve_new_sends_new_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "--new" in cmd and "--worktree-id" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", new=True)
    assert plan.is_exec


def test_resolve_new_with_seed_forwards_seed_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "--new" in cmd
        assert "--seed" in cmd
        assert cmd[cmd.index("--seed") + 1] == "fix the thing"
        assert "--stage-launch-seed" in cmd
        assert "--require-native-seed" in cmd
        return _fake_completed(cmd, stdout=json.dumps({
            **_RESUME_PLAN, "seed_pending": True, "seed_id": "intent-new", "seed_kind": "new",
        }))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", new=True, seed="fix the thing")
    assert plan.is_exec


def test_resolve_without_seed_omits_seed_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "--seed" not in cmd
        assert "--require-native-seed" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    ec.resolve_launch_plan("dotfiles", new=True, seed=None)
    ec.resolve_launch_plan("dotfiles", new=True, seed="")


@pytest.mark.parametrize("new", [False, True])
@pytest.mark.parametrize("target,seed,timeout,expected", [
    ("target", "task", 30, 30),
    ("target", "task", 500, 500),
    ("target", "task", None, None),
    (None, "task", None, None),
    ("target", None, None, None),
    (None, "task", 30, 30),
    ("target", None, 30, 30),
    ("target", "", 30, 30),
    (None, None, 500, 500),
])
def test_remote_packaging_preserves_only_the_callers_explicit_budget(
    new: bool, target: str | None, seed: str | None, timeout: int | None,
    expected: int | None, monkeypatch: pytest.MonkeyPatch, remote_registry: None,
) -> None:
    seen: list[int | None] = []

    def run_json(
        project: str | None, args: list[str], *, timeout: int | None = None,
        stream_progress: bool = False, **kwargs: Any,
    ) -> dict[str, Any]:
        if args[:2] == ["agent-policy", "check"]:
            assert kwargs["allow_nonzero"] is True
            return _hosting_policy()
        assert stream_progress is True
        seen.append(timeout)
        if target:
            return {
                "action": "remote", "ssh_alias": "target", "remote_command": "fixture",
                "remote_terminal_handoff_version": 1,
                "worktree_id": None if new else "wt-a",
            }
        return {
            **_RESUME_PLAN, "seed_pending": bool(seed),
            "seed_id": "intent" if seed else None,
            "seed_kind": ("new" if new else "resume") if seed else None,
        }

    monkeypatch.setattr(ec, "run_json", run_json)
    ec.resolve_launch_plan(
        "example", new=new, worktree_id=None if new else "wt-a",
        target_machine="Example-wsl" if target else None, seed=seed, timeout=timeout,
    )
    assert seen == ([] if target else [expected])


@pytest.mark.parametrize("new", [False, True])
def test_remote_prompt_packages_without_either_engine_or_a_target_receipt(
    monkeypatch: pytest.MonkeyPatch, new: bool, remote_registry: None,
) -> None:
    monkeypatch.setattr(ec, "run_json", lambda *a, **k: pytest.fail("engine called before SSH"))
    plan = ec.resolve_launch_plan(
        "example", new=new, worktree_id=None if new else "wt-a",
        target_machine="Example-wsl", seed="fixture", launch_attempt_id="b" * 32,
    )
    assert plan.action == "remote"
    assert plan.worktree_id == (None if new else "wt-a")
    assert plan.raw.get("launch_attempt_receipt") is None
    assert plan.raw.get("seed_id") is None
    assert "--seed=fixture" in plan.raw["remote_command"]
    assert "--require-" not in plan.raw["remote_command"]


def test_old_engine_never_retries_without_attempt_correlation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    def run_json(project: str | None, args: list[str], **kwargs: Any) -> dict[str, Any]:
        if args[:2] == ["agent-policy", "check"]:
            return _hosting_policy()
        calls.append(args)
        raise ec.EngineError("unrecognized arguments: --launch-attempt-id")
    monkeypatch.setattr(ec, "run_json", run_json)
    with pytest.raises(ec.EngineFeatureUnavailable, match="launch-resolution receipts"):
        ec.resolve_launch_plan("example", new=True,
                               seed="fixture", launch_attempt_id="b" * 32)
    assert len(calls) == 1


def test_resolve_base_sends_base_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        assert "--base" in cmd
        assert "--new" not in cmd and "--worktree-id" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", base=True)
    assert plan.is_exec


def test_resolve_base_skew_retries_non_json_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "--json" in cmd:
            return _fake_completed(
                cmd,
                returncode=2,
                stdout=json.dumps({
                    "version": 1,
                    "error": "--json requires --worktree-id or --new",
                }),
            )
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", base=True)
    assert plan.is_exec
    assert any("--json" in call for call in calls)
    assert any("--base" in call and "--json" not in call for call in calls)


def test_resolve_remote_sends_environment_and_action(
    monkeypatch: pytest.MonkeyPatch, remote_registry: None,
) -> None:
    monkeypatch.setattr(ec, "run_json", lambda *a, **k: pytest.fail("engine called"))
    plan = ec.resolve_launch_plan(
        "example",
        worktree_id="wt-1",
        bare_resume=True,
        target_machine="Example",
        target_environment="WSL",
        target_no_mux=True,
    )
    assert plan.action == "remote"
    assert plan.raw["ssh_alias"] == "guest profile"
    import shlex
    assert shlex.split(shlex.split(plan.raw["remote_command"])[-1]) == [
        "example", "--worktree-id", "wt-1", "--bare-resume", "--no-mux",
    ]


@pytest.fixture
def remote_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = {
        "Example": MachineEntry(
            key="Example", display_name="Example Windows", execution_platform="windows",
            physical_host="Example", ssh_environments=[SSHEnvironment("windows", "example-win")],
        ),
        "Example-wsl": MachineEntry(
            key="Example-wsl", display_name="Example guest", execution_platform="wsl",
            physical_host="Example", ssh_environments=[SSHEnvironment("wsl", "guest profile")],
        ),
        "opaque-wsl": MachineEntry(
            key="opaque-wsl", display_name="Opaque Windows", execution_platform="windows",
            physical_host="Opaque", ssh_environments=[SSHEnvironment("windows", "opaque-native")],
        ),
        "native": MachineEntry(
            key="native", display_name="Native Windows", execution_platform="windows",
            physical_host="NativeHost",
            ssh_environments=[SSHEnvironment("windows", "Native profile (WSL)")],
        ),
    }

    def selected(project: str) -> dict[str, MachineEntry]:
        assert project == "example"
        return entries

    monkeypatch.setattr(machine_roster, "selected_entries", selected)


@pytest.mark.parametrize("machine,environment,alias", [
    ("Example", "WSL", "guest profile"),
    ("Example-wsl", None, "guest profile"),
    ("Example-wsl", "WSL", "guest profile"),
    ("guest profile", "WSL", "guest profile"),
    ("opaque-wsl", None, "opaque-native"),
    ("opaque-wsl", "Windows", "opaque-native"),
    ("Native profile (WSL)", None, "Native profile (WSL)"),
    ("Native profile (WSL)", "Windows", "Native profile (WSL)"),
])
def test_remote_destination_preserves_registered_selector_and_only_distinct_environment(
    remote_registry: None, monkeypatch: pytest.MonkeyPatch, machine: str,
    environment: str | None, alias: str,
) -> None:
    monkeypatch.setattr(ec, "run_json", lambda *a, **k: pytest.fail("engine called"))
    plan = ec.resolve_launch_plan(
        "example", new=True, target_machine=machine, target_environment=environment,
    )
    assert plan.raw["ssh_alias"] == alias


@pytest.mark.parametrize("machine,environment", [
    ("guest profile", "Windows"),
    ("opaque-wsl", "WSL"),
    ("Native profile (WSL)", "WSL"),
])
def test_remote_destination_refuses_conflicting_registered_environment_before_engine(
    remote_registry: None, monkeypatch: pytest.MonkeyPatch, machine: str, environment: str,
) -> None:
    monkeypatch.setattr(
        ec, "run_json", lambda *args, **kwargs: pytest.fail("conflicting destination reached the engine"),
    )
    with pytest.raises(ec.EngineError, match="conflict"):
        ec.resolve_launch_plan(
            "example", new=True, target_machine=machine, target_environment=environment,
        )


def test_remote_destination_without_registry_refuses_without_engine_or_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(project: str) -> NoReturn:
        raise FileNotFoundError("source registry unavailable")

    monkeypatch.setattr(machine_roster, "selected_entries", unavailable)
    monkeypatch.setattr(ec, "run_json", lambda *a, **k: pytest.fail("engine called"))
    with pytest.raises(ec.EngineError, match="registry unavailable"):
        ec.resolve_launch_plan(
            "example", new=True, target_machine="opaque-wsl", target_environment="Windows",
        )


def test_remote_handoff_does_not_invoke_incompatible_local_engine(
    monkeypatch: pytest.MonkeyPatch, remote_registry: None,
) -> None:
    def request(project: str | None, args: list[str], **kwargs: Any) -> NoReturn:
        raise ec.EngineError("unrecognized arguments: --require-terminal-handoff")
    monkeypatch.setattr(ec, "run_json", request)
    assert ec.resolve_launch_plan("example", new=True, target_machine="Example").action == "remote"


def test_resolve_remote_real_error_is_not_misclassified(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"version": 1, "error": "no such worktree"})
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload),
    )
    with pytest.raises(ec.EngineError, match="no such worktree") as error:
        ec.resolve_launch_plan(
            "dotfiles",
            worktree_id="missing",
        )
    assert not isinstance(error.value, ec.EngineFeatureUnavailable)


def test_resolve_remote_base_never_resolves_locally(
    monkeypatch: pytest.MonkeyPatch, remote_registry: None,
) -> None:
    monkeypatch.setattr(ec, "run_json", lambda *a, **k: pytest.fail("engine called"))
    plan = ec.resolve_launch_plan(
        "example", base=True, target_machine="Example-wsl",
    )
    assert "--base" in plan.raw["remote_command"]


def test_resolve_requires_a_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles")


def test_resolve_worktree_and_new_are_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles", worktree_id="x", new=True)


def test_resolve_base_and_new_are_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles", base=True, new=True)


def test_resolve_none_action(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"action": "none", "exit_code": 0}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(payload)))
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="x")
    assert plan.action == "none" and not plan.is_exec and plan.exit_code == 0


def test_resolve_unwraps_nested_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    nested = {"worktree": {"id": "x"}, "launch": _RESUME_PLAN}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(nested)))
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="m-win-1200-ab12")
    assert plan.cmd == ["copilot", "--resume=sess123"]


def test_resolve_bare_resume_skew_retries_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "--bare-resume" in cmd:
            return _fake_completed(cmd, returncode=2,
                                   stderr="unrecognized arguments: --bare-resume")
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="x", bare_resume=True)
    assert plan.is_exec
    assert any("--bare-resume" in c for c in calls)
    assert any("--bare-resume" not in c for c in calls)


def test_resolve_bare_resume_retry_preserves_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "--bare-resume" in cmd:
            return _fake_completed(cmd, returncode=2,
                                   stderr="unrecognized arguments: --bare-resume")
        return _fake_completed(cmd, stdout=json.dumps({
            **_RESUME_PLAN, "seed_pending": True, "seed_id": "intent-resume", "seed_kind": "resume",
        }))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan(
        "dotfiles", worktree_id="x", bare_resume=True, seed="fix the thing")
    assert plan.is_exec
    # Both the original (rejected) attempt AND the degraded retry must carry
    # --seed -- the retry rebuilds its own argv from the original kwargs, so
    # a seed dropped from that rebuild would silently vanish on exactly the
    # path meant to gracefully degrade one unsupported flag, not all of them.
    assert all("--seed" in c and "fix the thing" in c for c in calls)


def test_resolve_bare_resume_plus_seed_rejection_is_not_treated_as_skew(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine's own deliberate ``--seed is not supported together with
    --bare-resume`` rejection mentions the literal substring
    ``--bare-resume`` in its error text, same as a genuine version-skew
    "unrecognized arguments" error would -- but it is NOT an
    unsupported-flag signal, and must not trigger the compatibility-fallback
    retry. Retrying would silently convert this rejected combination into
    an ordinary seeded (non-bare) resume instead of surfacing the engine's
    real error, exactly the semantic the rejection exists to prevent."""
    calls: list[list[str]] = []

    def handler(cmd: list[str], kw: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return _fake_completed(
            cmd, returncode=2,
            stderr="--seed is not supported together with --bare-resume.",
        )

    _install_fake(monkeypatch, handler)
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan(
            "dotfiles", worktree_id="x", bare_resume=True, seed="do the thing")
    # Only the original attempt ran -- no silent retry without --bare-resume.
    assert len(calls) == 1


def test_importing_engine_execution_leg_directly_before_engine_client_works() -> None:
    """A genuine cold-import-order regression test for the lazy
    `__getattr__` re-export: every OTHER test in this suite (including this
    file's own `from worktree_manager import engine_client` at module
    scope) already imports `engine_client` first, so none of them would
    catch a regression back to a top-level `from .engine_execution_leg
    import ...` in `engine_client.py` -- that shape only deadlocks when
    `engine_execution_leg` is the FIRST of the two modules actually
    imported. A fresh subprocess is the only way to force that cold order;
    an in-process import (even via `importlib.reload`) would still see
    `engine_client` already fully initialized from this file's own import
    at the top."""
    script = (
        "import worktree_manager.engine_execution_leg as eel\n"
        "from worktree_manager import engine_client\n"
        "assert engine_client.execution_leg_get is eel.execution_leg_get\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_resolve_error_envelope_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"version": 1, "error": "no such worktree"})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload))
    with pytest.raises(ec.EngineError) as ei:
        ec.resolve_launch_plan("dotfiles", worktree_id="nope")
    assert "no such worktree" in str(ei.value)
