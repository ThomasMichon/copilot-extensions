"""Focused CodeSpaces adapters; the shared real-process proof lives in dispatch."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_codespaces import __main__ as cli
from agent_codespaces import config, coordination, gh_account, lease, lifecycle, worktrees

REAL_OWNER_REF = coordination.owner_ref
REAL_PREFLIGHT = coordination.preflight


def _hook():
    path = Path(__file__).resolve().parents[1] / "scripts" / "emit_codespace_map.py"
    spec = importlib.util.spec_from_file_location("codespace_map_peer_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess(["same-cell"], code, stdout, stderr)


def test_every_adapter_uses_same_cell_worktrees(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "cell-one")
    monkeypatch.setattr(worktrees, "validate_context", lambda: {})
    monkeypatch.setattr(coordination, "owner_ref", REAL_OWNER_REF)
    monkeypatch.setattr(coordination, "preflight", REAL_PREFLIGHT)
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: pytest.fail("ambient PATH selected"))
    seen = []

    def run(*args, **kwargs):
        seen.append((args, kwargs))
        responses = {
            ("repos", "find", "project"): str(tmp_path),
            ("accounts", "show", "login", "--json"): '{"login_flow":"login command"}',
            ("state-root", "--json"): json.dumps({
                "requires_external": True, "bound": True, "state_root": str(tmp_path),
            }),
            ("get", "owner-ref"): "machine/project/worktree",
            ("get", "worktree-dir"): str(tmp_path),
            ("list", "--json"): json.dumps([{"path": str(tmp_path), "status": "active"}]),
            ("repos", "account-for", "owner/repo"): "login",
            ("repos", "account", "list", "--json"): '{"account_map":{"owner":"login"}}',
            ("--project", "project", "coordination-readiness"): (
                '{"version":1,"ready":true,"code":"ready"}'
            ),
            ("related", "list", "--json", "--require-managed"): "[]",
        }
        return _result(responses[args])

    monkeypatch.setattr(worktrees, "run", run)
    monkeypatch.setattr(config, "repo_has_config", lambda _: True)
    monkeypatch.chdir(tmp_path)
    assert cli._chdir_to_project("project")
    assert cli._account_login_remedy("login") == "run: login command"
    assert config._state_root_config_dir(tmp_path) == tmp_path
    assert REAL_OWNER_REF() == "machine/project/worktree"
    assert REAL_PREFLIGHT("machine/project/worktree").ready
    assert lease.resolve_owner_worktree() == str(tmp_path)
    assert lease.active_worktree_ids() == {str(tmp_path)}
    assert gh_account.account_for_repo("owner/repo") == "login"
    assert gh_account.mapped_accounts() == ("login",)
    assert _hook()._aw("related", "list", "--json", "--require-managed", cwd=str(tmp_path)) == "[]"
    assert len(seen) == 10
    assert seen[2][1]["cwd"] == seen[-1][1]["cwd"] == str(tmp_path)


def test_context_refusal_cannot_become_fallback_or_claim(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: pytest.fail("ambient fallback"))
    monkeypatch.setattr(lease, "_read_leases", lambda: pytest.fail("claim state read"))
    monkeypatch.setattr(lifecycle, "list_codespaces", lambda: pytest.fail("ambient auth"))
    calls = [
        lambda: cli._chdir_to_project("project"),
        lambda: cli._account_login_remedy("login"),
        lambda: config._state_root_config_dir(tmp_path),
        lambda: coordination._run(["lease", "acquire", "codespace", "test"]),
        lambda: REAL_OWNER_REF("explicit/project/worktree"),
        lambda: lease.resolve_owner_worktree("explicit"),
        lease.active_worktree_ids,
        lambda: lease.claim("space", "owner", coordinate=False),
        lambda: gh_account.account_for_repo("owner/repo"),
        lambda: gh_account.account_for_repo(None),
        gh_account.mapped_accounts,
        lambda: gh_account.env_for_repo("owner/repo"),
        lambda: lifecycle.account_for_codespace("space"),
        lambda: _hook()._aw("related", "list"),
    ]
    for call in calls:
        with pytest.raises(worktrees.ContextRefused):
            call()
    assert REAL_PREFLIGHT("machine/project/worktree").rejected
    assert REAL_PREFLIGHT("not-a-project").rejected


def test_namespaced_preflight_distinguishes_refusal_and_compatibility(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "explicit")
    monkeypatch.setattr(worktrees, "validate_context", lambda: {})
    for result in (
        _result(code=126, stderr="peer launch refused"),
        _result(code=1, stderr="context rejected"),
        _result(code=3, stderr="not JSON"),
        _result("{}", code=3),
        _result('{"version":1,"ready":false,"code":"unknown_refusal"}', code=3),
    ):
        monkeypatch.setattr(coordination, "_run", lambda *a, **k: result)
        assert REAL_PREFLIGHT("machine/project/worktree").rejected
    for result in (
        None, _result("{}"),
        _result(code=2, stderr="invalid choice: 'coordination-readiness'"),
    ):
        monkeypatch.setattr(coordination, "_run", lambda *a, **k: result)
        assert REAL_PREFLIGHT("machine/project/worktree").absent


def test_namespaced_account_cache_never_reuses_other_cell(monkeypatch):
    gh_account.clear_caches()
    monkeypatch.setattr(worktrees, "validate_context", lambda: {})

    def run(*args, **kwargs):
        cell = os.environ["COPILOT_EXTENSIONS_CONTEXT"]
        return _result(
            json.dumps({"account_map": {"owner": cell}}) if args[-1] == "--json" else cell
        )

    monkeypatch.setattr(worktrees, "run", run)
    for cell in ("cell-one", "cell-two", "cell-one"):
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", cell)
        assert gh_account.account_for_repo("owner/repo") == cell
        assert gh_account.mapped_accounts() == (cell,)


def test_rejected_peer_preflight_cannot_authorize_l1_claim(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "active-owner")
    monkeypatch.setattr(worktrees, "validate_context", lambda: {})
    monkeypatch.setattr(coordination, "preflight", REAL_PREFLIGHT)
    monkeypatch.setattr(
        coordination, "_run",
        lambda *a, **k: _result(code=126, stderr="peer launch refused"),
    )
    monkeypatch.setattr(lease, "_read_leases", lambda: {})
    monkeypatch.setattr(lease, "_lease_lock", lambda: pytest.fail("L1 mutation"))
    with pytest.raises(lease.CoordinationRejected, match="peer launch refused"):
        lease.claim("space", "owner", holder_ref="machine/project/worktree")


def test_ssh_does_not_downgrade_peer_refusal_to_bookkeeping_warning(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(lifecycle, "account_for_codespace", lambda _: None)
    monkeypatch.setattr(cli, "load_merged_config", lambda: None)
    monkeypatch.setattr("agent_codespaces.relay_launch.effective_relay_port", lambda _: 0)
    monkeypatch.setattr(lease, "resolve_owner_worktree", lambda **kwargs: "owner")
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda *a, **k: pytest.fail("remote connection"))

    def refused():
        raise worktrees.ContextRefused("active peer rejected")

    monkeypatch.setattr(lease, "active_worktree_ids", refused)
    result = cli._cmd_ssh(SimpleNamespace(name="space"))
    assert result == cli._COORDINATION_EXIT
    assert "active peer rejected" in capsys.readouterr().err


def test_account_discovery_propagates_peer_refusal(monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr("agent_codespaces.account_binding.bound_account", lambda _: None)

    def refused():
        raise worktrees.ContextRefused("invalid active peer")

    monkeypatch.setattr(lifecycle, "list_codespaces", refused)
    with pytest.raises(worktrees.ContextRefused, match="invalid active peer"):
        lifecycle.account_for_codespace("space")


def test_packaged_shared_bytes_are_canonical():
    root = Path(__file__).resolve().parents[3]
    assert (root / "libs" / "peer-launch" / "peer_launch.py").read_bytes() == (
        Path(worktrees.__file__).with_name("_peer_launch.py").read_bytes()
    )
    assert (root / "libs" / "installation-context" / "installation_context.py").read_bytes() == (
        Path(worktrees.__file__).with_name("_installation_context.py").read_bytes()
    )


def test_session_writer_uses_source_hook_not_legacy_wrappers(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts" / "write_session_guidance.py"
    spec = importlib.util.spec_from_file_location("peer_guidance_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "explicit")
    monkeypatch.setattr("shutil.which", lambda _: pytest.fail("ambient shell selection"))
    argv = module._codespace_map_argv(tmp_path / "unvalidated", str(tmp_path))
    assert argv[1:4] == ["-I", "-X", "utf8"]
    assert Path(argv[4]) == path.with_name("emit_codespace_map.py")
    assert argv[5:] == ["--cwd", str(tmp_path)]


def test_source_map_reports_explicit_refusal(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "emit_codespace_map.py"
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(tmp_path / "invalid-owner"))
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(script)],
        capture_output=True, encoding="utf-8", timeout=15,
        **worktrees.no_window_kwargs(),
    )
    assert result.returncode == 126
    assert result.stdout == ""
    assert "guidance refused" in result.stderr


@pytest.mark.parametrize("existing", [False, True])
def test_guidance_refusal_preserves_session_files(tmp_path, monkeypatch, existing):
    path = Path(__file__).resolve().parents[1] / "scripts" / "write_session_guidance.py"
    spec = importlib.util.spec_from_file_location("refused_guidance_test", path)
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    home = tmp_path / "profile"
    target = (home / ".copilot" / "session-state" / "example-session"
              / "instructions" / "agent-codespaces" / "session-guidance.instructions.md")
    if existing:
        target.parent.mkdir(parents=True)
        target.write_text("unchanged guidance", encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(tmp_path / "invalid-owner"))
    monkeypatch.setattr(
        writer, "_run_producer",
        lambda *args: pytest.fail("catalog ran before owner validation"),
    )
    with pytest.raises(writer.GuidanceRefused, match="refused"):
        writer.write_session_guidance({"sessionId": "example-session"}, home=home)
    if existing:
        assert target.read_text(encoding="utf-8") == "unchanged guidance"
    else:
        assert not home.exists()


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("command", [cli._cmd_claim, cli._cmd_release_claim, cli._cmd_ssh])
def test_claim_entrypoints_report_dedicated_context_refusal(
    tmp_path, monkeypatch, capsys, command, disabled,
):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(tmp_path / "bad-owner"))
    if disabled:
        monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
    else:
        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("external call"))
    assert command(SimpleNamespace(name="space", codespace="space")) == cli._COORDINATION_EXIT
    assert "context refused" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["account", "owner", "holder"])
def test_early_ssh_setup_keeps_refusal_exit(monkeypatch, capsys, stage):
    monkeypatch.setattr(cli, "validate_context", lambda: None)
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(lifecycle, "account_for_codespace", lambda _: None)
    monkeypatch.setattr(cli, "load_merged_config", lambda: None)
    monkeypatch.setattr("agent_codespaces.relay_launch.effective_relay_port", lambda _: 0)
    monkeypatch.setattr(lease, "resolve_owner_worktree", lambda **k: "owner")
    monkeypatch.setattr(coordination, "owner_ref", lambda **k: "machine/project/worktree")

    def refused(*args, **kwargs):
        raise worktrees.ContextRefused(f"{stage} refused")

    module, name = {
        "account": (lifecycle, "account_for_codespace"),
        "owner": (lease, "resolve_owner_worktree"),
        "holder": (coordination, "owner_ref"),
    }[stage]
    monkeypatch.setattr(module, name, refused)
    assert cli._cmd_ssh(SimpleNamespace(name="space")) == cli._COORDINATION_EXIT
    assert f"{stage} refused" in capsys.readouterr().err


@pytest.mark.parametrize("operation", [lambda: lifecycle.stop_codespace("space"), lifecycle.cleanup_stale])
def test_lifecycle_preserves_listing_context_refusal(monkeypatch, operation):
    def refused():
        raise worktrees.ContextRefused("listing refused")

    monkeypatch.setattr(lifecycle, "list_codespaces", refused)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ambient operation"))
    with pytest.raises(worktrees.ContextRefused, match="listing refused"):
        operation()


def test_map_nonzero_peer_result_is_not_optional_absence(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "explicit")
    monkeypatch.setattr(worktrees, "run", lambda *a, **k: _result(code=1, stderr="peer failed"))
    with pytest.raises(worktrees.ContextRefused, match="peer failed"):
        _hook()._aw("related", "list")


def test_source_guidance_wrapper_preserves_refusal(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if os.name == "nt":
        shell = (Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell"
                 / "v1.0" / "powershell.exe")
        argv = [str(shell), "-NoProfile", "-File", str(scripts / "write-session-guidance.ps1")]
    else:
        argv = ["/bin/bash", str(scripts / "write-session-guidance.sh")]
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    monkeypatch.delenv("AGENT_CODESPACES_HOME", raising=False)
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(tmp_path / "untrusted-payload"))
    result = subprocess.run(
        argv, input='{"sessionId":"example-session"}',
        capture_output=True, encoding="utf-8", timeout=30,
        **worktrees.no_window_kwargs(),
    )
    assert result.returncode == 126, (result.stdout, result.stderr)
    assert "guidance refused" in result.stderr
    assert not (home / ".copilot").exists()
