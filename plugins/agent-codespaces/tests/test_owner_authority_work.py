"""Evidence-only production-resolution accounting; no functional authority fix.

All external process/keyring/installation boundaries are synthetic and counted.
The actual candidate/config/provider/token resolution functions remain in use.
These are intercepted boundary requests, not native CPU or opaque gh HTTP counts.
Passing this recorder verifies accounting, not performance acceptance. Partial
observations retain their error and completion counts and never qualify savings.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import ssh_manager
from agent_procutil import no_window_kwargs

from agent_codespaces import account_binding, account_candidates, codespace_config, config, credential_store, gh_account
from agent_codespaces import connection_owner as owner
from agent_codespaces import owner_inventory as inv
from agent_codespaces import picker_auth, session_forwards as sf, worktrees


BASELINE = "35872e7ad67e1c0ef182a1f4401a49af69c4afb6"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def authority_fixture(monkeypatch, tmp_path):
    raw_run = subprocess.run
    source = raw_run(
        ["git", "-C", str(REPO), "show",
         BASELINE + ":plugins/agent-codespaces/src/agent_codespaces/session_forwards.py"],
        capture_output=True, text=True, check=True, timeout=10, **no_window_kwargs(),
    ).stdout
    spec = importlib.util.spec_from_loader("agent_codespaces._authority_baseline", loader=None)
    baseline = importlib.util.module_from_spec(spec)
    exec(compile(source, "<pinned-authority-baseline>", "exec"), baseline.__dict__)
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "codespaces"
    root.mkdir()
    for name in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "RUNTIME_DIR", root)
    monkeypatch.setattr(config, "ADOPTED_REPOS_FILE", root / "adopted.yaml")
    monkeypatch.setattr(picker_auth, "RUNTIME_DIR", root)
    monkeypatch.setattr(account_binding, "BINDINGS_FILE", root / "bindings.json")
    monkeypatch.setattr(account_binding, "_LOCK_FILE", root / "bindings.lock")
    monkeypatch.setattr(account_binding, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(codespace_config, "SSH_CONFIG_DIR", root / "ssh")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    holds = {}
    lock = threading.Lock()
    monkeypatch.setattr(owner, "_owner_lock", lambda: lock)
    monkeypatch.setattr(owner, "_read_holds", lambda: copy.deepcopy(holds))

    def write_holds(value):
        holds.clear()
        holds.update(copy.deepcopy(value))

    monkeypatch.setattr(owner, "_write_holds", write_holds)
    state = {
        "count": 0, "accounts": ["a", "b", "c"], "credential": 1,
        "namespace": 1, "error": False, "effect": None,
        "partial_account": None, "limit_account": None, "exec_effect": None,
        "ssh_failures": 0,
    }
    counts = Counter()
    counter_lock = threading.Lock()
    unknown = []

    def count(key):
        with counter_lock:
            counts[key] += 1

    def observe_call(module, name, category):
        original = getattr(module, name)

        def invoke(*args, **kwargs):
            count(category)
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, invoke)

    for module, name, category in (
        (account_candidates, "candidates", "resolution.candidates"),
        (config, "load_merged_config", "resolution.config.load"),
        (config, "scan_config_providers", "resolution.config.scan"),
        (picker_auth, "authorized_configs", "resolution.provider.selection"),
        (account_binding, "bound_accounts_or_raise", "resolution.bindings.read"),
    ):
        observe_call(module, name, category)

    def response(stdout="", code=0):
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="synthetic error" if code else "")

    def run(argv, **kwargs):
        args = list(map(str, argv))
        if args[0] == "fixture-peer":
            if args[1:] == ["repos", "account", "list", "--json", "--with-resolutions"]:
                count("process.discovery.account_inventory")
                if state["error"]:
                    return response(code=1)
                return response(json.dumps({
                    "account_map": {name: name for name in state["accounts"]},
                    "resolution_version": 1, "repository_accounts": {}, "owner_accounts": {},
                }))
        elif args[:3] == ["gh", "auth", "status"]:
            count("process.discovery.active_account")
            return response(json.dumps({"hosts": {"github.com": [{"login": "a", "active": True}]}}))
        elif args[:3] == ["gh", "auth", "token"]:
            count("process.auth.token")
            return response(f"synthetic-{args[-1]}-{state['credential']}")
        elif args[:3] == ["gh", "codespace", "list"]:
            count("process.inventory.list")
            count("inventory.pages")
            account = kwargs["env"]["GH_TOKEN"].split("-")[1]
            if account == state["partial_account"]:
                return response(code=1)
            rows = [
                {"name": f"cs-{i}", "state": "Available"}
                for i in range(state["count"]) if "abc"[i % 3] == account
            ]
            if account == state["limit_account"]:
                rows = [{"name": f"cs-{i}", "state": "Available"} for i in range(50)]
            return response(json.dumps(rows))
        elif args[:4] == ["gh", "codespace", "ssh", "--config"]:
            count("process.ssh.config")
            if state["effect"]:
                effect, state["effect"] = state["effect"], None
                effect()
            return response("Host synthetic\n  HostName synthetic\n")
        elif args[0] == "fixture-ssh":
            batch = "ACMUX1" in args[-1]
            count("process.ssh.exec")
            count("mux.batch" if batch else "mux.single" if "has-session" in args[-1] else "bridge.probe")
            if state["exec_effect"]:
                effect, state["exec_effect"] = state["exec_effect"], None
                effect()
            if state["ssh_failures"]:
                state["ssh_failures"] -= 1
                count("transport.failed_attempts")
                return response(code=255)
            return response(
                "".join(f"ACMUX1\t{i}\tpresent\n" for i in range(4)) + "ACMUX1\tDONE\t4\n"
                if batch else ""
            )
        unknown.append(args[:4])
        raise AssertionError("Unclassified synthetic subprocess boundary")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(gh_account.shutil, "which", lambda name: {
        "gh": "gh", "agent-worktrees": "fixture-peer",
    }.get(name))

    def keyring_read(service, login):
        count("api.credential_store.get")
        if state["error"]:
            raise credential_store.keyring.errors.KeyringError("synthetic provider failure")
        return f"synthetic-{login}-{state['credential']}"

    monkeypatch.setattr(credential_store.keyring, "get_password", keyring_read)

    def validate_owner(*args):
        count("api.installation.validate")
        return {
            "cellRoot": str(root / "cell"), "pluginRoot": str(root),
            "generation": state["namespace"],
        }

    monkeypatch.setattr(worktrees, "validate_owner", validate_owner)
    monkeypatch.setattr(worktrees, "launch_prefix", lambda *a: ["fixture-peer"])
    (root / "cell" / "plugins" / "agent-worktrees").mkdir(parents=True)

    class Manager:
        async def ensure_connected(self, name, source, forwards):
            count("manager.open")
            await inv.joined_thread(source.get_ssh_config)

        async def exec_command(self, name, command, timeout=None):
            result = run(["fixture-ssh", name, command])
            return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout)

        async def disconnect(self, name):
            count("manager.close")

    monkeypatch.setattr(ssh_manager, "ConnectionManager", Manager)
    heartbeat = owner.heartbeat

    def renew(*args, **kwargs):
        count("owner.heartbeat")
        return heartbeat(*args, **kwargs)

    monkeypatch.setattr(sf, "heartbeat", renew)
    monkeypatch.setattr(baseline, "heartbeat", renew)

    def observed_scope():
        count("authority.scope_capture")
        try:
            return inv.capture_scope()
        except Exception as error:
            count("authority.scope_error")
            reason = (
                "binding-lock-unavailable"
                if "Could not acquire account binding lock" in str(error)
                else type(error).__name__
            )
            count("authority.error." + reason)
            raise

    def production_probe():
        from agent_codespaces.owner_mux_probe import RemoteMuxProbe

        return RemoteMuxProbe(
            scope_reader=observed_scope,
            inventory_reader=lambda wave: inv.collect_inventory(wave, scope_reader=observed_scope),
        )

    def prepare(count, provider=False, explicit=False):
        state["count"] = count
        counts.clear()
        holds.clear()
        gh_account.clear_caches()
        bindings = {
            f"cs-{i}": {"codespace": f"cs-{i}", "account": "abc"[i % 3], "bound_at": 1}
            for i in range(count)
        }
        account_binding.BINDINGS_FILE.write_text(json.dumps(bindings), encoding="utf-8")
        if provider:
            for login in "abc":
                auth = root / "github-auth" / login
                auth.mkdir(parents=True, exist_ok=True)
                (auth / "accepted-login").write_text(login, encoding="utf-8")
                (auth / "store-kind").write_text("keyring", encoding="utf-8")
        if explicit:
            monkeypatch.setenv("AGENT_CODESPACES_HOME", str(root))
            monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", json.dumps({"cellRoot": str(root / "cell")}))
        for i in range(count):
            for j in range(4):
                owner.hold(f"cs-{i}", f"t-{j}", mux_session=f"mux-{j}", confirmed=True)

    yield SimpleNamespace(
        state=state, counts=counts, unknown=unknown, prepare=prepare,
        baseline=baseline, root=root, monkeypatch=monkeypatch,
        production_probe=production_probe,
    )
    gh_account.clear_caches()


@pytest.mark.parametrize("count", [0, 1, 10])
@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("implementation", ["baseline", "draft"])
async def test_record_resolution_boundary_counts(authority_fixture, count, provider, implementation):
    f = authority_fixture
    f.prepare(count, provider)
    module = f.baseline if implementation == "baseline" else sf
    now = [1000]
    forwards = module.SessionForwards(
        lambda *a: None,
        f.production_probe() if implementation == "draft" else module.make_remote_mux_probe(),
        clock=lambda: now[0],
    )
    await forwards.probe(owner.list_holds())
    first = dict(f.counts)
    await forwards.probe(owner.list_holds())
    assert dict(f.counts) == first
    assert not f.unknown
    expected_lists = 3 * count if implementation == "baseline" else 3 if count else 0
    expected_execs = 4 * count if implementation == "baseline" else count
    assert f.counts["manager.open"] == f.counts["manager.close"]
    if implementation == "baseline" or not f.counts["authority.scope_error"]:
        assert f.counts["process.inventory.list"] == expected_lists
        assert f.counts["process.ssh.exec"] == expected_execs
        assert f.counts["manager.open"] == count
        assert f.counts["owner.heartbeat"] == count * 4
    else:
        assert f.counts["process.inventory.list"] <= expected_lists
        assert f.counts["process.ssh.exec"] <= expected_execs
        assert f.counts["owner.heartbeat"] <= count * 4
    now[0] += 121
    await forwards.probe(owner.list_holds())
    warm = dict(f.counts - Counter(first))
    for phase, receipt in (("cold-due", first), ("warm-due", warm)):
        print("AUTHORITY_COUNTERS " + json.dumps({
            "implementation": implementation, "phase": phase, "venues": count, "accounts": 3, "muxes": 4,
            "credential_backend": "provider-keyring" if provider else "gh-cli",
            "counts": receipt,
            "process_boundary_requests": sum(v for k, v in receipt.items() if k.startswith("process.")),
            "native_processes": "not-executed", "gh_internal_http": "opaque-not-measured",
            "retries": 0, "source_baseline": BASELINE,
            "enabled_consumers": ["mux"],
            "expected_tenant_observations": count * 4,
            "completed_tenant_observations": receipt.get("owner.heartbeat", 0),
            "observation_outcome": (
                "complete" if receipt.get("owner.heartbeat", 0) == count * 4
                and not receipt.get("authority.scope_error") else "partial"
            ),
        }, sort_keys=True))


@pytest.mark.parametrize("change", ["account", "namespace", "credential", "config", "provider-error"])
async def test_real_resolution_change_during_wave_is_unknown(authority_fixture, change):
    f = authority_fixture
    f.prepare(1, provider=change == "provider-error", explicit=change == "namespace")
    first = owner.get_hold("cs-0")

    def mutate():
        if change == "account":
            f.state["accounts"].append("d")
        elif change == "namespace":
            f.state["namespace"] += 1
        elif change == "credential":
            f.state["credential"] += 1
        elif change == "config":
            target = f.root / "config.d"
            target.mkdir()
            (target / "fixture.yaml").write_text("defaults:\n  machine_type: changed\n", encoding="utf-8")
        else:
            f.state["error"] = True

    f.state["effect"] = mutate
    await sf.SessionForwards(lambda *a: None, sf.make_remote_mux_probe()).probe([first])
    assert f.counts["process.ssh.config"] == 1
    assert f.counts["process.ssh.exec"] == 0
    assert owner.get_hold("cs-0").tenants == first.tenants
    assert not f.unknown


@pytest.mark.parametrize("implementation", ["baseline", "draft"])
@pytest.mark.parametrize("provider", [False, True])
async def test_bridge_and_mux_full_resolution_boundary_counts(authority_fixture, implementation, provider):
    f = authority_fixture
    f.prepare(10, provider)
    module = f.baseline if implementation == "baseline" else sf
    probe = f.production_probe() if implementation == "draft" else module.make_remote_mux_probe()
    forwards = module.SessionForwards(
        lambda *a: None, probe, bridge_probe=module.make_remote_bridge_probe(),
    )
    for hold in owner.list_holds():
        forwards._channels[hold.codespace] = (12345, SimpleNamespace(is_alive=True))
    await forwards.probe(owner.list_holds())
    assert not f.unknown
    assert f.counts["process.inventory.list"] == (30 if implementation == "baseline" else 3)
    assert f.counts["manager.open"] == f.counts["manager.close"]
    complete = f.counts["owner.heartbeat"] == 40 and not f.counts["authority.scope_error"]
    if complete:
        assert f.counts["bridge.probe"] == 10
        assert f.counts["process.ssh.exec"] == (50 if implementation == "baseline" else 20)
    print("AUTHORITY_COUNTERS " + json.dumps({
        "implementation": implementation, "phase": "cold-due", "venues": 10, "accounts": 3, "muxes": 4,
        "enabled_consumers": ["mux", "bridge"],
        "credential_backend": "provider-keyring" if provider else "gh-cli",
        "counts": dict(f.counts),
        "process_boundary_requests": sum(v for k, v in f.counts.items() if k.startswith("process.")),
        "native_processes": "not-executed", "gh_internal_http": "opaque-not-measured",
        "retries": 0, "source_baseline": BASELINE,
        "expected_tenant_observations": 40, "completed_tenant_observations": f.counts["owner.heartbeat"],
        "observation_outcome": "complete" if complete else "partial",
    }, sort_keys=True))


@pytest.mark.parametrize("kind", ["partial", "limit"])
async def test_unknown_inventory_is_not_reused_next_due_wave(authority_fixture, kind):
    f = authority_fixture
    f.prepare(1)
    f.state["partial_account" if kind == "partial" else "limit_account"] = "a"
    now = [1000]
    forwards = sf.SessionForwards(lambda *a: None, sf.make_remote_mux_probe(), clock=lambda: now[0])
    await forwards.probe(owner.list_holds())
    assert f.counts["process.inventory.list"] == 3
    assert f.counts["manager.open"] == 0
    f.state["partial_account"] = f.state["limit_account"] = None
    now[0] += 121
    await forwards.probe(owner.list_holds())
    assert f.counts["process.inventory.list"] == 6
    assert f.counts["process.ssh.exec"] == 1
    assert not f.unknown


async def test_expiry_with_real_authority_resolution(authority_fixture):
    from agent_codespaces.owner_mux_probe import RemoteMuxProbe

    f = authority_fixture
    f.prepare(1)
    offset = [0]
    first = owner.get_hold("cs-0")
    f.state["effect"] = lambda: offset.__setitem__(0, 100)
    probe = RemoteMuxProbe(clock=lambda: time.monotonic() + offset[0])
    await sf.SessionForwards(lambda *a: None, probe).probe([first])
    assert f.counts["process.ssh.exec"] == 0
    assert owner.get_hold("cs-0").tenants == first.tenants
    assert not f.unknown


async def test_stale_heartbeat_with_real_authority_resolution(authority_fixture):
    f = authority_fixture
    f.prepare(1)
    first = owner.get_hold("cs-0")
    replaced = []

    def replace_tenant():
        replaced.append(owner.hold("cs-0", "t-0", mux_session="mux-0", confirmed=True, fresh=True))

    f.state["exec_effect"] = replace_tenant
    await sf.SessionForwards(lambda *a: None, sf.make_remote_mux_probe()).probe([first])
    current = owner.get_hold("cs-0")
    assert current.sessions["t-0"]["generation"] == replaced[0].sessions["t-0"]["generation"]
    assert current.tenants["t-0"] == replaced[0].tenants["t-0"]
    assert not f.unknown


async def test_cancel_real_resolution_wave_drains_owned_provider(authority_fixture):
    f = authority_fixture
    f.prepare(1)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def wait():
        entered.set()
        assert release.wait(5)
        finished.set()

    f.state["effect"] = wait
    task = asyncio.create_task(sf.SessionForwards(
        lambda *a: None, sf.make_remote_mux_probe(),
    ).probe(owner.list_holds()))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert f.counts["process.ssh.exec"] == 0
    assert not f.unknown


async def test_mux_retry_is_counted_at_real_execution_boundary(authority_fixture):
    f = authority_fixture
    f.prepare(1)
    f.state["ssh_failures"] = 1
    await sf.SessionForwards(lambda *a: None, sf.make_remote_mux_probe()).probe(owner.list_holds())
    assert f.counts["process.inventory.list"] == 3
    assert f.counts["process.ssh.exec"] == 2
    assert f.counts["transport.failed_attempts"] == 1
    assert f.counts["manager.open"] == f.counts["manager.close"] == 1
    assert not f.unknown
