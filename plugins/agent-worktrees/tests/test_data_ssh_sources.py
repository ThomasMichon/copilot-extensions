"""Unit tests for ``data_ssh._build_sources`` machine/env resolution.

Focus: the local machine never needs an SSH profile of its own (the picker runs
there), and a listed env with no SSH profile is never connected to -- it renders
as a disabled tab instead.
"""
from __future__ import annotations

import types

from agent_worktrees import config as cfg
from agent_worktrees.picker_tui import data_ssh


def _install_roster(monkeypatch, entries, *, machine, local_id):
    """Point ``_build_sources`` at a fabricated roster + local identity."""
    fake_config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/repo"),
        machine=machine,
    )
    monkeypatch.setattr(data_ssh.cfg, "load_config", lambda: fake_config)
    monkeypatch.setattr(
        data_ssh.cfg, "load_machines_yaml", lambda _anchor: entries)
    monkeypatch.setattr(data_ssh, "_local_identity", lambda: local_id)
    monkeypatch.setattr(data_ssh, "_project", lambda: "proj")


def _entry(key, display, envs, *, ssh_ready=True, copilot=True, alias=""):
    return cfg.MachineEntry(
        key=key,
        display_name=display,
        environment="",
        alias=alias,
        ssh_environments=envs,
        ssh_ready=ssh_ready,
        copilot=copilot,
    )


def _by_key(sources):
    return {(s.machine, s.env): s for s in sources}


def test_local_machine_needs_no_ssh_profile(monkeypatch):
    """A current machine with NO ssh environments still gets a local tab."""
    entries = {
        "lambda-core": _entry("lambda-core", "Lambda-Core", [], ssh_ready=False),
    }
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    sources = data_ssh._build_sources()
    assert len(sources) == 1
    local = sources[0]
    assert local.local is True
    assert local.ready is True
    assert local.machine == "Lambda-Core"
    assert local.env == "Win"  # derived from the running platform
    assert local.argv is None
    assert local.alias == ""


def test_local_env_is_local_even_when_machine_not_ssh_ready(monkeypatch):
    """The current machine's native env is local; its other env is a disabled
    tab because the machine is not ssh_ready."""
    envs = [
        cfg.SSHEnvironment(name="windows", alias="lambda-core", shell="pwsh"),
        cfg.SSHEnvironment(name="wsl", alias="lambda-core-wsl", shell="bash"),
    ]
    entries = {"lambda-core": _entry("lambda-core", "Lambda-Core", envs,
                                     ssh_ready=False)}
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    by = _by_key(data_ssh._build_sources())
    assert by[("Lambda-Core", "Win")].local is True
    assert by[("Lambda-Core", "Win")].ready is True
    # WSL of the current machine is not local and the machine is not ready:
    # disabled tab, never contacted.
    wsl = by[("Lambda-Core", "WSL")]
    assert wsl.local is False
    assert wsl.ready is False
    assert wsl.argv is None


def test_env_without_alias_is_disabled_not_connected(monkeypatch):
    """A remote env with no SSH profile (empty alias) becomes a disabled tab
    even when the machine is ssh_ready -- it is never connected to."""
    envs = [cfg.SSHEnvironment(name="linux", alias="", shell="bash")]
    entries = {
        "lambda-core": _entry(
            "lambda-core", "Lambda-Core",
            [cfg.SSHEnvironment(name="windows", alias="lambda-core",
                                shell="pwsh")]),
        "ghost": _entry("ghost", "Ghost", envs, ssh_ready=True),
    }
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    by = _by_key(data_ssh._build_sources())
    ghost = by[("Ghost", "Linux")]
    assert ghost.ready is False
    assert ghost.argv is None
    assert ghost.local is False


def test_ready_remote_env_with_alias_is_connected(monkeypatch):
    """A remote ssh_ready env with a real alias gets an SSH argv (reachable)."""
    entries = {
        "lambda-core": _entry(
            "lambda-core", "Lambda-Core",
            [cfg.SSHEnvironment(name="windows", alias="lambda-core",
                                shell="pwsh")]),
        "wheatley": _entry(
            "wheatley", "Wheatley",
            [cfg.SSHEnvironment(name="linux", alias="wheatley", shell="bash")],
            ssh_ready=True),
    }
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    by = _by_key(data_ssh._build_sources())
    wheatley = by[("Wheatley", "Linux")]
    assert wheatley.ready is True
    assert wheatley.local is False
    assert wheatley.alias == "wheatley"
    assert wheatley.argv and wheatley.argv[0] == "ssh"
    assert "wheatley" in wheatley.argv


def _remote_roster(monkeypatch):
    """A local machine + one ready remote (Wheatley/Linux) for op-argv tests."""
    entries = {
        "lambda-core": _entry(
            "lambda-core", "Lambda-Core",
            [cfg.SSHEnvironment(name="windows", alias="lambda-core",
                                shell="pwsh")]),
        "wheatley": _entry(
            "wheatley", "Wheatley",
            [cfg.SSHEnvironment(name="linux", alias="wheatley", shell="bash")],
            ssh_ready=True),
    }
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))


def test_remote_op_argv_restart_uses_positional_id_and_json(monkeypatch):
    """The remote 'restart' op runs ``<proj> restart <id> --json`` (the CLI
    verb is ``restart`` even though the picker labels it 'Stop'); the id is
    positional, not ``--worktree-id``."""
    _remote_roster(monkeypatch)
    argv = data_ssh.remote_op_argv("Wheatley", "Linux", "restart", "wt-xyz")
    assert argv is not None and argv[0] == "ssh"
    inner = argv[-1]
    assert "proj restart wt-xyz --json" in inner
    assert "--worktree-id" not in inner


def test_remote_op_argv_restart_local_returns_none(monkeypatch):
    """A local target yields no SSH argv (the caller runs it in-process)."""
    _remote_roster(monkeypatch)
    assert data_ssh.remote_op_argv(
        "Lambda-Core", "Win", "restart", "wt-xyz") is None


def test_remote_op_argv_finalize_uses_positional_id_and_json(monkeypatch):
    """The remote 'finalize' op runs ``<proj> finalize <id> --json`` -- the id
    is positional (the ``finalize`` CLI has no ``--worktree-id`` flag)."""
    _remote_roster(monkeypatch)
    argv = data_ssh.remote_op_argv("Wheatley", "Linux", "finalize", "wt-xyz")
    assert argv is not None and argv[0] == "ssh"
    inner = argv[-1]
    assert "proj finalize wt-xyz --json" in inner
    assert "--worktree-id" not in inner


def test_ssh_not_ready_remote_env_is_disabled(monkeypatch):
    """A ssh.ready:false machine's remote env stays a disabled tab."""
    entries = {
        "lambda-core": _entry(
            "lambda-core", "Lambda-Core",
            [cfg.SSHEnvironment(name="windows", alias="lambda-core",
                                shell="pwsh")]),
        "book2": _entry(
            "book2", "tmichon-book2",
            [cfg.SSHEnvironment(name="windows", alias="book2", shell="pwsh")],
            ssh_ready=False),
    }
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    by = _by_key(data_ssh._build_sources())
    book2 = by[("tmichon-book2", "Win")]
    assert book2.ready is False
    assert book2.argv is None
    assert book2.alias == "book2"


def test_copilot_false_machine_is_skipped(monkeypatch):
    entries = {
        "lambda-core": _entry(
            "lambda-core", "Lambda-Core",
            [cfg.SSHEnvironment(name="windows", alias="lambda-core",
                                shell="pwsh")]),
        "nas": _entry(
            "nas", "NAS",
            [cfg.SSHEnvironment(name="linux", alias="nas", shell="bash")],
            copilot=False),
    }
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    by = _by_key(data_ssh._build_sources())
    assert ("NAS", "Linux") not in by


# ── #1421 continuous background poll: LiveLoader.repoll_silent ────────────────

def _ready_loader(monkeypatch, records):
    """A LiveLoader with one ready source seeded with ``records``."""
    src = data_ssh.Source("M", "Win", ["ssh", "m", "list"], ready=True)
    loader = data_ssh.LiveLoader([src])
    with loader._lock:
        loader._state[src.key] = "ready"
        loader._records[src.key] = list(records)
    return loader, src


def _wait(pred, timeout=2.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not pred():
        time.sleep(0.01)
    return pred()


def test_repoll_silent_swaps_records_without_loading_flip(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    calls = {"n": 0}

    def fake_fetch(source, runner=None):
        calls["n"] += 1
        return ["new"]

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    assert loader.repoll_silent() == 1
    assert _wait(lambda: loader.records() == ["new"])
    assert loader.state("M", "Win") == "ready"      # never flipped to loading
    assert calls["n"] == 1
    assert _wait(lambda: not loader._refreshing)     # guard cleared


def test_repoll_silent_keeps_last_good_on_failure(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])

    def boom(source, runner=None):
        raise RuntimeError("ssh down")

    monkeypatch.setattr(data_ssh, "_fetch", boom)
    assert loader.repoll_silent() == 1
    assert _wait(lambda: not loader._refreshing)
    assert loader.records() == ["old"]              # last-good preserved
    assert loader.state("M", "Win") == "ready"


def test_repoll_silent_skips_non_ready_and_cancelled(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    called = {"n": 0}
    monkeypatch.setattr(
        data_ssh, "_fetch",
        lambda source, runner=None: called.__setitem__("n", called["n"] + 1) or ["x"])
    # Not ready -> skipped.
    with loader._lock:
        loader._state[src.key] = "loading"
    assert loader.repoll_silent() == 0
    # Cancelled -> whole pass is a no-op.
    with loader._lock:
        loader._state[src.key] = "ready"
    loader._cancelled.set()
    assert loader.repoll_silent() == 0
    assert called["n"] == 0


# ── #2102 remote-tab PR reconcile: argv flag + LiveLoader.reconcile_remote_prs ─

def test_argv_for_reconcile_includes_flag():
    argv = data_ssh._argv_for("bash", "wheatley", "proj",
                              classify=True, reconcile=True)
    inner = argv[-1]
    assert "--reconcile-prs" in inner
    assert "list --json" in inner


def test_argv_for_without_reconcile_omits_flag():
    argv = data_ssh._argv_for("bash", "wheatley", "proj", classify=True)
    assert "--reconcile-prs" not in argv[-1]


def test_reconcile_remote_prs_runs_reconcile_argv_and_swaps(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    seen = {"argv": None, "n": 0}

    def fake_fetch(source, runner=None, *, argv=None):
        seen["n"] += 1
        seen["argv"] = argv
        return ["reconciled"]

    monkeypatch.setattr(data_ssh, "_fetch", fake_fetch)
    assert loader.reconcile_remote_prs() == 1
    assert _wait(lambda: loader.records() == ["reconciled"])
    assert loader.state("M", "Win") == "ready"              # never flips to loading
    assert seen["argv"] is not None
    assert "--reconcile-prs" in seen["argv"][-1]            # ran the reconcile argv
    assert _wait(lambda: not loader._refreshing)            # guard cleared
    # One-shot per source: a second pass is a no-op.
    assert loader.reconcile_remote_prs() == 0
    assert seen["n"] == 1


def test_reconcile_remote_prs_skips_local(monkeypatch):
    local = data_ssh.Source("M", "Win", None, local=True, ready=True)
    loader = data_ssh.LiveLoader([local])
    with loader._lock:
        loader._state[local.key] = "ready"
    called = {"n": 0}
    monkeypatch.setattr(
        data_ssh, "_fetch",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ["x"])
    assert loader.reconcile_remote_prs() == 0
    assert called["n"] == 0


def test_reconcile_remote_prs_keeps_last_good_on_failure(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])

    def boom(source, runner=None, *, argv=None):
        raise RuntimeError("ssh down")

    monkeypatch.setattr(data_ssh, "_fetch", boom)
    assert loader.reconcile_remote_prs() == 1
    assert _wait(lambda: not loader._refreshing)
    assert loader.records() == ["old"]                     # last-good preserved
    assert loader.state("M", "Win") == "ready"


def test_reconcile_remote_prs_noop_when_cancelled(monkeypatch):
    loader, src = _ready_loader(monkeypatch, ["old"])
    monkeypatch.setattr(
        data_ssh, "_fetch",
        lambda *a, **k: ["x"])
    loader._cancelled.set()
    assert loader.reconcile_remote_prs() == 0


# -- display_name -> registry key resolution (registered-pivot {machine}) ------


def test_machine_key_map_maps_display_names_to_registry_keys(monkeypatch):
    entries = {
        "lambda-core": _entry("lambda-core", "Lambda-Core", []),
        "borealis": _entry("borealis", "Borealis", []),
    }
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    assert data_ssh.machine_key_map() == {
        "Lambda-Core": "lambda-core",
        "Borealis": "borealis",
    }


def test_machine_key_translates_display_to_key(monkeypatch):
    entries = {"lambda-core": _entry("lambda-core", "Lambda-Core", [])}
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    # A tab's display name resolves to the canonical (lowercase) identity that
    # agent-dispatch and the SSH alias expect.
    assert data_ssh.machine_key("Lambda-Core") == "lambda-core"


def test_machine_key_falls_back_to_display_when_unknown(monkeypatch):
    entries = {"lambda-core": _entry("lambda-core", "Lambda-Core", [])}
    _install_roster(
        monkeypatch, entries, machine="lambda-core",
        local_id=("lambda-core", "windows"))

    # An unknown display name (roster gap) degrades to itself, not None.
    assert data_ssh.machine_key("Unlisted") == "Unlisted"
    assert data_ssh.machine_key(None) is None


def test_machine_key_map_empty_on_unreadable_roster(monkeypatch):
    def _boom(_anchor):
        raise FileNotFoundError("no machines.yaml")

    monkeypatch.setattr(data_ssh.cfg, "load_config", lambda: types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/repo"), machine="m"))
    monkeypatch.setattr(data_ssh.cfg, "load_machines_yaml", _boom)
    assert data_ssh.machine_key_map() == {}
    assert data_ssh.machine_key("Anything") == "Anything"
