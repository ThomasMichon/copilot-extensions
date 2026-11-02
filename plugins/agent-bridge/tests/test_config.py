"""Tests for config management commands (adopt, remove, validate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_bridge.config import (
    adopt_topology,
    default_db_path,
    config_dir,
    load_config,
    load_repo_bridge_config,
    remove_topology,
    save_config,
    validate_config,
)
from agent_bridge.models import ServiceConfig, TopologyProfile
from agent_bridge.session_modes import resolve_session_mode, write_session_mode


@pytest.fixture()
def config_home(tmp_path, monkeypatch):
    """Point agent-bridge config dir to a temp directory."""
    config_dir = tmp_path / ".agent-bridge"
    config_dir.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.fixture()
def fake_repo(tmp_path):
    """Create a fake repo with machines.yaml and acp-agents.json."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "machines.yaml").write_text(yaml.dump({"machines": {"test": {}}}))
    agents_dir = repo / "tools" / "mcp"
    agents_dir.mkdir(parents=True)
    (agents_dir / "acp-agents.json").write_text(json.dumps({"test-agent": {}}))
    return repo


class TestSessionModes:
    @staticmethod
    def policy(root, modes, *, legacy=False, **extra):
        relative = ".agent-bridge" if legacy else ".copilot-extensions/agent-bridge"
        path = root / relative / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"session_modes": modes, **extra}), encoding="utf-8")
        return path

    def test_fresh_default_and_adopter_fallback(self, config_home):
        assert resolve_session_mode() == {"mode": "acp", "source": "default"}
        assert resolve_session_mode(default="cli")["mode"] == "cli"

    @pytest.mark.parametrize(
        "repo,venue,mode,scope",
        [
            (None, None, "cli", "default"),
            (None, "ssh", "acp", "venues.ssh"),
            ("example", None, "acp", "repos.example.default"),
            ("example", "ssh", "cli", "repos.example.venues.ssh"),
            ("Example", "ssh", "acp", "venues.ssh"),
        ],
    )
    def test_scope_priority_and_exact_case(self, tmp_path, config_home, repo, venue, mode, scope):
        path = self.policy(tmp_path, {
            "default": "cli", "venues": {"ssh": "acp"},
            "repos": {"example": {"default": "acp", "venues": {"ssh": "cli"}}},
        })
        assert resolve_session_mode(operator_repo=tmp_path, repo=repo, venue=venue) == {
            "mode": mode, "source": f"{path}#session_modes.{scope}",
        }

    def test_owner_priority_over_specificity_and_fallback(self, tmp_path, config_home):
        operator = tmp_path / "operator"
        operator.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        self.policy(operator, {"default": "cli"})
        self.policy(target, {"venues": {"local": "acp"}})
        save_config(ServiceConfig(session_modes={"default": "acp"}))
        assert resolve_session_mode(
            operator_repo=operator, repo_root=target, venue="local", default="acp",
        )["mode"] == "cli"
        assert resolve_session_mode(repo_root=target, venue="local", default="cli")["mode"] == "acp"
        assert resolve_session_mode(default="cli")["mode"] == "acp"

    @pytest.mark.parametrize("policy", [
        {"default": "other"}, {"venues": {"unknown": "cli"}},
        {"repos": {"": {"default": "cli"}}}, {"unknown": "cli"}, None, [],
    ])
    def test_invalid_operator_policy_fails(self, tmp_path, config_home, policy):
        self.policy(tmp_path, policy)
        with pytest.raises(ValueError, match="refusing authority fallback"):
            resolve_session_mode(operator_repo=tmp_path)

    @pytest.mark.parametrize("content", ["session_modes:\n  default: invalid\n", "port: invalid\n", "[]"])
    def test_invalid_service_policy_never_falls_back(self, tmp_path, config_home, content):
        (config_home / "config.yaml").write_text(content)
        self.policy(tmp_path, {"default": "cli"})
        with pytest.raises(ValueError, match="refusing preference fallback"):
            resolve_session_mode(operator_repo=tmp_path)

    def test_unreadable_present_policy_fails(self, tmp_path, config_home, monkeypatch):
        path = self.policy(tmp_path, {"default": "cli"})
        original = Path.read_text

        def denied(candidate, *args, **kwargs):
            if candidate == path:
                raise PermissionError("test denial")
            return original(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
        with pytest.raises(ValueError, match="refusing authority fallback"):
            resolve_session_mode(operator_repo=tmp_path)

    def test_set_exact_scope_preserves_config_and_unsets(self, tmp_path, config_home):
        path = self.policy(tmp_path, {"default": "cli", "venues": {"local": "acp"}},
                           custom={"preserved": [1, 2]})
        written = write_session_mode(operator_repo=tmp_path, repo="example", venue="ssh", mode="cli")
        assert written == {
            "mode": "cli", "source": f"{path}#session_modes.repos.example.venues.ssh",
        }
        assert yaml.safe_load(path.read_text()) == {
            "custom": {"preserved": [1, 2]},
            "session_modes": {
                "default": "cli", "venues": {"local": "acp"},
                "repos": {"example": {"venues": {"ssh": "cli"}}},
            },
        }
        assert resolve_session_mode(operator_repo=tmp_path, repo="example", venue="ssh") == written
        write_session_mode(operator_repo=tmp_path, repo="example", venue="ssh", unset=True)
        assert resolve_session_mode(operator_repo=tmp_path, repo="example", venue="ssh")["source"].endswith(
            "#session_modes.default"
        )

    def test_machine_set_survives_save_and_adopt(self, config_home, fake_repo):
        write_session_mode(machine_local=True, venue="codespace", mode="cli")
        adopt_topology("example", str(fake_repo))
        assert load_config().session_modes.venues == {"codespace": "cli"}
        assert resolve_session_mode(venue="codespace")["mode"] == "cli"

    def test_repo_model_roundtrip(self, tmp_path, config_home):
        self.policy(tmp_path, {"default": "cli", "repos": {"example": {"venues": {"ssh": "acp"}}}})
        loaded = load_repo_bridge_config(tmp_path)
        assert loaded.session_modes.model_dump(exclude_none=True)["repos"]["example"]["venues"] == {"ssh": "acp"}

    def test_canonical_legacy_and_overlay_authority(self, tmp_path, config_home, monkeypatch):
        legacy = self.policy(tmp_path, {"default": "cli"}, legacy=True)
        write_session_mode(operator_repo=tmp_path, venue="local", mode="acp")
        assert "local" in yaml.safe_load(legacy.read_text())["session_modes"]["venues"]
        canonical = self.policy(tmp_path, {"default": "acp"})
        assert resolve_session_mode(operator_repo=tmp_path)["source"] == f"{canonical}#session_modes.default"
        overlay = canonical.parent / "marketplaces" / "example" / "config.yaml"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("session_modes:\n  default: cli\n")
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", '{"marketplaceId":"example"}')
        result = resolve_session_mode(operator_repo=tmp_path)
        assert result["mode"] == "cli"
        assert str(overlay) in result["source"]

    def test_write_requires_explicit_destination(self, config_home):
        with pytest.raises(ValueError, match="exactly one"):
            write_session_mode(mode="cli")

    def test_failed_atomic_replace_preserves_original(self, tmp_path, config_home, monkeypatch):
        path = self.policy(tmp_path, {"default": "acp"}, unrelated="preserved")
        before = path.read_bytes()

        def denied(*args):
            raise PermissionError("test denial")

        monkeypatch.setattr("agent_bridge.config_write.os.replace", denied)
        with pytest.raises(PermissionError):
            write_session_mode(operator_repo=tmp_path, mode="cli")
        assert path.read_bytes() == before
        assert not list(path.parent.glob("*.pending"))

    def test_set_does_not_change_aliased_scopes_or_unrelated_values(self, tmp_path, config_home):
        path = self.policy(tmp_path, {})
        path.write_text(
            "shared: &venues\n  ssh: acp\n"
            "session_modes:\n  venues: *venues\n"
            "  repos:\n    example:\n      venues: *venues\n"
        )
        write_session_mode(operator_repo=tmp_path, repo="example", venue="ssh", mode="cli")
        actual = yaml.safe_load(path.read_text())
        assert actual["shared"] == {"ssh": "acp"}
        assert actual["session_modes"]["venues"] == {"ssh": "acp"}
        assert actual["session_modes"]["repos"]["example"]["venues"] == {"ssh": "cli"}

    @pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX permission bits")
    def test_preserves_protected_permissions_and_restricts_new_files(self, tmp_path, config_home):
        import os
        import stat

        path = self.policy(tmp_path, {"default": "acp"}, unrelated="private")
        path.chmod(0o600)
        old_umask = os.umask(0o022)
        try:
            write_session_mode(operator_repo=tmp_path, mode="cli")
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            path.unlink()
            write_session_mode(operator_repo=tmp_path, mode="acp")
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        finally:
            os.umask(old_umask)

    @pytest.mark.parametrize("second_action", ["session-mode", "adopt", "remove", "migrate"])
    def test_overlapping_subprocess_writers_preserve_both_scopes(
        self, tmp_path, config_home, fake_repo, second_action,
    ):
        import subprocess
        import sys

        from agent_procutil import no_window_kwargs

        save_config(ServiceConfig(
            session_modes={"default": "acp"}, topologies={"existing": TopologyProfile()},
            schema_version=1 if second_action == "migrate" else 2,
            port=9280 if second_action == "migrate" else 0,
        ))
        first_script = """
import sys
from pathlib import Path
from agent_bridge import session_modes as modes
from agent_bridge import config_write as writes
replace = writes.os.replace
def held_replace(*args):
    print("ready", flush=True)
    sys.stdin.readline()
    replace(*args)
writes.os.replace = held_replace
modes.write_session_mode(machine_local=True, venue="ssh", mode="cli")
"""
        second_script = """
import sys
from pathlib import Path
from agent_bridge import session_modes as modes
from agent_bridge import config, config_write as writes
sleep = writes.time.sleep
def contended(delay):
    print("waiting", flush=True)
    sleep(delay)
writes.time.sleep = contended
if sys.argv[2] == "session-mode":
    modes.write_session_mode(machine_local=True, venue="container", mode="cli")
elif sys.argv[2] == "adopt":
    config.adopt_topology("example", sys.argv[1])
elif sys.argv[2] == "migrate":
    from agent_bridge.config_migrations import run_migrations
    run_migrations()
else:
    config.remove_topology("existing")
"""
        processes = []
        try:
            first = subprocess.Popen(
                [sys.executable, "-c", first_script, str(fake_repo)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **no_window_kwargs(),
            )
            processes.append(first)
            assert first.stdout.readline().strip() == "ready"
            second = subprocess.Popen(
                [sys.executable, "-c", second_script, str(fake_repo), second_action],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
            )
            processes.append(second)
            assert second.stdout.readline().strip() == "waiting"
            _, error = first.communicate("\n", timeout=10)
            assert first.returncode == 0, error
            _, error = second.communicate(timeout=10)
            assert second.returncode == 0, error
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
        assert resolve_session_mode(venue="ssh")["mode"] == "cli"
        if second_action == "session-mode":
            assert resolve_session_mode(venue="container")["mode"] == "cli"
        elif second_action == "adopt":
            assert "example" in load_config().topologies
        elif second_action == "migrate":
            actual = yaml.safe_load((config_home / "config.yaml").read_text())
            assert actual["schema_version"] == 2 and load_config().port == 0
        else:
            assert "existing" not in load_config().topologies

    def test_stale_native_save_preserves_new_preference(self, config_home):
        save_config(ServiceConfig(port=1234))
        stale = load_config()
        write_session_mode(machine_local=True, mode="cli")
        stale.port = 2345
        save_config(stale)
        assert load_config().port == 2345
        assert resolve_session_mode()["mode"] == "cli"
        save_config(ServiceConfig(port=3456))
        assert resolve_session_mode()["mode"] == "cli"

    def test_conflicting_native_mode_edit_refuses_stale_save(self, config_home):
        write_session_mode(machine_local=True, mode="acp")
        stale = load_config()
        write_session_mode(machine_local=True, mode="cli")
        stale.session_modes.venues["ssh"] = "cli"
        with pytest.raises(ValueError, match="changed concurrently"):
            save_config(stale)
        assert resolve_session_mode()["mode"] == "cli"

    def test_non_ascii_values_survive_platform_default_readers(self, tmp_path, config_home, monkeypatch):
        label = "caf\u00e9 \u65c5\u884c"
        path = self.policy(tmp_path, {"default": "acp"}, default_env={"LABEL": label})
        save_config(ServiceConfig(topologies={"example": TopologyProfile(default_env={"LABEL": label})}))
        original = Path.read_text

        def legacy_locale(candidate, *args, **kwargs):
            if candidate in (path, config_home / "config.yaml") and not args:
                kwargs.setdefault("encoding", "cp1252")
            return original(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", legacy_locale)
        write_session_mode(operator_repo=tmp_path, venue="ssh", mode="cli")
        write_session_mode(machine_local=True, venue="local", mode="cli")
        assert load_repo_bridge_config(tmp_path).default_env["LABEL"] == label
        assert load_config().topologies["example"].default_env["LABEL"] == label
        assert path.read_bytes().isascii()
        assert (config_home / "config.yaml").read_bytes().isascii()

    @pytest.mark.parametrize("kwargs", [{"repo": "../example"}, {"venue": "SSH"}, {"mode": "other"}])
    def test_invalid_write_selectors(self, config_home, kwargs):
        args = {"machine_local": True, "mode": "cli", **kwargs}
        with pytest.raises(ValueError):
            write_session_mode(**args)
        assert not (config_home / "config.yaml").exists()

    def test_actual_offline_command_fresh_set_read_and_error(self, tmp_path, config_home):
        import os
        import subprocess
        import sys

        from agent_procutil import no_window_kwargs

        def command(*args):
            return subprocess.run(
                [sys.executable, "-m", "agent_bridge", "config", "session-mode", *args, "--json"],
                cwd=tmp_path, env=os.environ.copy(), capture_output=True, text=True,
                timeout=30, **no_window_kwargs(),
            )

        fresh = command()
        assert fresh.returncode == 0, fresh.stderr
        assert json.loads(fresh.stdout) == {"mode": "acp", "source": "default"}
        help_result = command("--help")
        assert help_result.returncode == 0, help_result.stderr
        assert "agent-bridge.session-mode/v1" in help_result.stdout
        save_config(ServiceConfig())
        local = command("--machine-local", "--set", "cli")
        assert local.returncode == 0, local.stderr
        assert json.loads(local.stdout)["mode"] == "cli"
        operator = tmp_path / "operator"
        operator.mkdir()
        denied = command("--set", "cli")
        assert denied.returncode == 1
        assert "exactly one" in denied.stderr
        set_result = command("--operator-repo", str(operator), "--venue", "container", "--set", "cli")
        assert set_result.returncode == 0, set_result.stderr
        assert json.loads(set_result.stdout)["written"]["source"].endswith("session_modes.venues.container")
        read_result = command("--operator-repo", str(operator), "--repo", "example", "--venue", "container")
        assert read_result.returncode == 0, read_result.stderr
        assert json.loads(read_result.stdout)["mode"] == "cli"
        self.policy(operator, {"default": "invalid"})
        invalid = command("--operator-repo", str(operator))
        assert invalid.returncode == 1
        assert "refusing authority fallback" in invalid.stderr

    def test_new_request_to_legacy_runtime_reports_unsupported_without_effects(self, tmp_path, config_home):
        import subprocess
        import sys

        from agent_procutil import no_window_kwargs

        # Model the pre-capability selected parser while retaining the actual
        # command tree. A new payload cannot infer support from its own bytes.
        script = """
import argparse
from agent_bridge.__main__ import build_parser
parser = build_parser()
sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
config = sub.choices["config"]
commands = next(a for a in config._actions if isinstance(a, argparse._SubParsersAction))
del commands.choices["session-mode"]
parser.parse_args(["config", "session-mode", "--help"])
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=tmp_path,
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == 2
        assert "invalid choice" in result.stderr and "session-mode" in result.stderr
        assert not (config_home / "config.yaml").exists()
        assert not (tmp_path / ".copilot-extensions").exists()


class TestSaveConfig:
    def test_default_db_path_scopes_to_active_config_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "cell"
        monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(root))
        monkeypatch.delenv("AGENT_BRIDGE_CONFIG_DIR", raising=False)
        assert config_dir() == root
        assert default_db_path() == root / "sessions.db"
        assert ServiceConfig().db_path == str(root / "sessions.db")

    def test_legacy_db_path_is_rewritten_under_scoped_root(self, tmp_path, monkeypatch):
        root = tmp_path / "cell"
        root.mkdir()
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))
        (root / "config.yaml").write_text(
            yaml.dump({"db_path": "~/.agent-bridge/sessions.db", "port": 0})
        )
        assert load_config().db_path == str(root / "sessions.db")

    def test_roundtrip(self, config_home):
        cfg = ServiceConfig(port=9999, bind="0.0.0.0")
        save_config(cfg)
        loaded = load_config()
        assert loaded.port == 9999
        assert loaded.bind == "0.0.0.0"

    def test_credential_relay_default_and_roundtrip(self, config_home):
        # Defaults on (primary daemon owns the relay); the elevated sub-daemon
        # seeds it off so it never re-binds/evicts the primary's relay.
        assert ServiceConfig().enable_credential_relay is True
        save_config(ServiceConfig(enable_credential_relay=False))
        assert load_config().enable_credential_relay is False

    def test_persisted_session_host_toggle_is_ignored(self, config_home):
        # The session_host_enabled toggle was removed (dotfiles#1478): Session
        # Hosts are the only mode. A config file written by an older build that
        # still carries the key must load cleanly (extra=ignore) rather than
        # raise, and the stale key is dropped on the next write.
        (config_home / "config.yaml").write_text(
            yaml.dump({"session_host_enabled": False, "port": 1234})
        )
        loaded = load_config()
        assert loaded.port == 1234
        assert not hasattr(loaded, "session_host_enabled")


class TestMigrateConfig:
    def test_idle_reap_default_armed(self, config_home):
        # The idle-session reaper is armed by default now (#1826 complement to
        # Session Hosts being default-on).
        assert ServiceConfig().idle_reap_ttl_seconds == 600
        assert ServiceConfig().idle_reap_sweep_seconds == 120

    def test_idle_reap_flips_stale_zero_to_600_once(self, config_home):
        from agent_bridge.config import migrate_config

        # A machine still carrying the OLD explicit disabled value...
        save_config(ServiceConfig(idle_reap_ttl_seconds=0))
        migrated = migrate_config(load_config())
        assert migrated.idle_reap_ttl_seconds == 600
        # ...persisted...
        assert load_config().idle_reap_ttl_seconds == 600
        # ...and its own marker is written.
        assert (config_home / ".migrations" / "idle_reap_default_on").exists()

    def test_idle_reap_respects_opt_out_after_marker(self, config_home):
        from agent_bridge.config import migrate_config

        # Marker already present -> a deliberate 0 (opt-out) sticks.
        (config_home / ".migrations").mkdir(parents=True)
        (config_home / ".migrations" / "idle_reap_default_on").write_text("applied\n")
        save_config(ServiceConfig(idle_reap_ttl_seconds=0))
        migrated = migrate_config(load_config())
        assert migrated.idle_reap_ttl_seconds == 0
        assert load_config().idle_reap_ttl_seconds == 0

    def test_idle_reap_idempotent_leaves_custom_untouched(self, config_home):
        from agent_bridge.config import migrate_config

        # A non-zero value (default or custom) is never touched by the migration.
        save_config(ServiceConfig(idle_reap_ttl_seconds=300))
        migrated = migrate_config(load_config())
        assert migrated.idle_reap_ttl_seconds == 300


class TestAdoptTopology:
    def test_auto_discovers_machines_not_agents(self, config_home, fake_repo):
        # machines.yaml is auto-discovered; acp-agents.json is NOT (retired --
        # the roster is derived from topology). An acp-agents.json present in the
        # repo is ignored unless passed explicitly as agents_config.
        cfg = adopt_topology("test-profile", str(fake_repo))
        assert "test-profile" in cfg.topologies
        profile = cfg.topologies["test-profile"]
        assert profile.machines_yaml is not None
        assert "machines.yaml" in profile.machines_yaml
        assert profile.agents_config is None

    def test_persists_to_disk(self, config_home, fake_repo):
        adopt_topology("saved", str(fake_repo))
        loaded = load_config()
        assert "saved" in loaded.topologies

    def test_updates_existing_profile(self, config_home, fake_repo):
        adopt_topology("same", str(fake_repo))
        # Create a second repo with different files
        repo2 = fake_repo.parent / "repo2"
        repo2.mkdir()
        (repo2 / "machines.yaml").write_text(yaml.dump({"machines": {}}))
        adopt_topology("same", str(repo2))
        loaded = load_config()
        assert "repo2" in loaded.topologies["same"].machines_yaml

    def test_explicit_paths(self, config_home, fake_repo):
        machines = str(fake_repo / "machines.yaml")
        agents = str(fake_repo / "tools" / "mcp" / "acp-agents.json")
        cfg = adopt_topology("explicit", str(fake_repo),
                             machines_yaml=machines, agents_config=agents)
        assert cfg.topologies["explicit"].machines_yaml is not None
        assert cfg.topologies["explicit"].agents_config is not None

    def test_no_files_raises(self, config_home, tmp_path, monkeypatch):
        empty = tmp_path / "empty-repo"
        empty.mkdir()
        # No knowledge overlay either.
        monkeypatch.setattr(
            "agent_bridge.config._state_root_machines_yaml", lambda repo: None)
        with pytest.raises(FileNotFoundError, match="No machines.yaml"):
            adopt_topology("fail", str(empty))

    def test_discovers_agent_worktrees_canonical_location(self, config_home, tmp_path):
        # The canonical .agent-worktrees/machines.yaml location (#950) is found.
        repo = tmp_path / "aw-repo"
        (repo / ".agent-worktrees").mkdir(parents=True)
        (repo / ".agent-worktrees" / "machines.yaml").write_text(
            yaml.dump({"machines": {"m": {}}}))
        cfg = adopt_topology("aw", str(repo))
        assert ".agent-worktrees" in cfg.topologies["aw"].machines_yaml

    def test_state_root_overlay_fallback(self, config_home, tmp_path, monkeypatch):
        # A stateless harness with no machines.yaml redirects to the knowledge
        # repo's machines.yaml resolved via the knowledge overlay (E1e, #947).
        harness = tmp_path / "citadel-harness"
        harness.mkdir()
        knowledge = tmp_path / "citadel-knowledge"
        (knowledge / ".agent-worktrees").mkdir(parents=True)
        kmach = knowledge / ".agent-worktrees" / "machines.yaml"
        kmach.write_text(yaml.dump({"machines": {"dev6": {}}}))
        monkeypatch.setattr(
            "agent_bridge.config._state_root_machines_yaml",
            lambda repo: str(kmach) if Path(repo) == harness else None)
        cfg = adopt_topology("stateless", str(harness))
        assert cfg.topologies["stateless"].machines_yaml is not None
        assert "citadel-knowledge" in cfg.topologies["stateless"].machines_yaml

    def test_missing_repo_raises(self, config_home, tmp_path):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            adopt_topology("fail", str(tmp_path / "nope"))

    def test_forward_slash_normalization(self, config_home, fake_repo):
        cfg = adopt_topology("slashes", str(fake_repo))
        profile = cfg.topologies["slashes"]
        if profile.machines_yaml:
            assert "\\" not in profile.machines_yaml

    def test_linked_worktree_persists_anchor_path(
        self, config_home, tmp_path, monkeypatch,
    ):
        import types

        anchor = tmp_path / "repo"
        anchor.mkdir()
        (anchor / "machines.yaml").write_text("machines: {}")
        worktree = tmp_path / "repo.worktrees" / "feature"
        worktree.mkdir(parents=True)
        (worktree / "machines.yaml").write_text("machines: {temporary: {}}")

        monkeypatch.setattr("shutil.which", lambda name: "git")

        def fake_run(args, **kwargs):
            if args[-1] == "--show-toplevel":
                root = worktree if Path(args[2]) == worktree else anchor
                return types.SimpleNamespace(
                    returncode=0, stdout=str(root), stderr="",
                )
            if args[-1] == "--git-common-dir":
                return types.SimpleNamespace(
                    returncode=0, stdout=str(anchor / ".git"), stderr="",
                )
            raise AssertionError(args)

        monkeypatch.setattr("subprocess.run", fake_run)
        cfg = adopt_topology("linked", str(worktree))
        assert cfg.topologies["linked"].machines_yaml == (
            str(anchor / "machines.yaml").replace("\\", "/")
        )

    def test_explicit_worktree_path_is_not_remapped(
        self, config_home, tmp_path, monkeypatch,
    ):
        import types

        anchor = tmp_path / "repo"
        anchor.mkdir()
        (anchor / "machines.yaml").write_text("machines: {}")
        worktree = tmp_path / "repo.worktrees" / "feature"
        worktree.mkdir(parents=True)
        explicit = worktree / "machines.yaml"
        explicit.write_text("machines: {temporary: {}}")

        monkeypatch.setattr("shutil.which", lambda name: "git")

        def fake_run(args, **kwargs):
            if args[-1] == "--show-toplevel":
                root = worktree if Path(args[2]) == worktree else anchor
                return types.SimpleNamespace(
                    returncode=0, stdout=str(root), stderr="",
                )
            if args[-1] == "--git-common-dir":
                return types.SimpleNamespace(
                    returncode=0, stdout=str(anchor / ".git"), stderr="",
                )
            raise AssertionError(args)

        monkeypatch.setattr("subprocess.run", fake_run)
        cfg = adopt_topology(
            "explicit-linked", str(worktree), machines_yaml=str(explicit),
        )
        assert cfg.topologies["explicit-linked"].machines_yaml == (
            str(explicit).replace("\\", "/")
        )

    def test_repo_subdirectory_is_not_canonicalized(
        self, config_home, tmp_path, monkeypatch,
    ):
        import types

        root = tmp_path / "repo"
        nested = root / "config"
        nested.mkdir(parents=True)
        (nested / "machines.yaml").write_text("machines: {nested: {}}")
        (root / "machines.yaml").write_text("machines: {root: {}}")
        monkeypatch.setattr("shutil.which", lambda name: "git")

        def fake_run(args, **kwargs):
            if args[-1] == "--show-toplevel":
                return types.SimpleNamespace(
                    returncode=0, stdout=str(root), stderr="",
                )
            raise AssertionError(args)

        monkeypatch.setattr("subprocess.run", fake_run)
        cfg = adopt_topology("nested", str(nested))
        assert cfg.topologies["nested"].machines_yaml == (
            str(nested / "machines.yaml").replace("\\", "/")
        )


class TestStateRootMachinesYaml:
    """_state_root_machines_yaml -- the E1e knowledge-overlay resolver (#947)."""

    def _fake_which(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "agent-worktrees")

    def _fake_state_root(self, monkeypatch, payload, *, rc=0):
        import types
        self._fake_which(monkeypatch)
        proc = types.SimpleNamespace(returncode=rc, stdout=json.dumps(payload),
                                     stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)

    def test_resolves_knowledge_machines_yaml(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        knowledge = tmp_path / "knowledge"
        (knowledge / ".agent-worktrees").mkdir(parents=True)
        (knowledge / ".agent-worktrees" / "machines.yaml").write_text("machines: {}")
        self._fake_state_root(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True,
        })
        got = _state_root_machines_yaml(tmp_path / "harness")
        assert got is not None and "knowledge" in got

    def test_self_hosted_not_redirected(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        # A self-hosted repo (requires_external False) must NOT be grafted.
        self._fake_state_root(monkeypatch, {
            "state_root": str(tmp_path), "requires_external": False, "bound": True,
        })
        assert _state_root_machines_yaml(tmp_path / "harness") is None

    def test_no_binstub_returns_none(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert _state_root_machines_yaml(tmp_path) is None

    def test_nonzero_exit_returns_none(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        self._fake_state_root(monkeypatch, {"error": "unbound"}, rc=3)
        assert _state_root_machines_yaml(tmp_path) is None


class TestRemoveTopology:
    def test_removes_profile(self, config_home, fake_repo):
        adopt_topology("to-remove", str(fake_repo))
        remove_topology("to-remove")
        loaded = load_config()
        assert "to-remove" not in loaded.topologies

    def test_missing_profile_raises(self, config_home):
        with pytest.raises(KeyError, match="not found"):
            remove_topology("nonexistent")


class TestValidateConfig:
    def test_valid_config(self, config_home, fake_repo):
        adopt_topology("valid", str(fake_repo))
        issues = validate_config()
        assert issues == []

    def test_no_topologies(self, config_home):
        save_config(ServiceConfig())
        issues = validate_config()
        assert any("No topology" in i for i in issues)

    def test_missing_file(self, config_home):
        cfg = ServiceConfig(topologies={
            "bad": TopologyProfile(machines_yaml="/nonexistent/machines.yaml")
        })
        save_config(cfg)
        issues = validate_config()
        assert any("not found" in i for i in issues)


class TestInRepoBridgeConfig:
    """Repo-portable multi-machine system spawn defaults."""

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert load_repo_bridge_config(tmp_path) is None

    def test_loads_default_copilot_args(self, tmp_path: Path):
        cfg_dir = tmp_path / ".copilot-extensions" / "agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"default_copilot_args": ["--model", "some-model"]}),
        )
        cfg = load_repo_bridge_config(tmp_path)
        assert cfg is not None
        assert cfg.default_copilot_args == ["--model", "some-model"]

    def test_loads_default_env(self, tmp_path: Path):
        cfg_dir = tmp_path / ".copilot-extensions" / "agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(yaml.dump({"default_env": {"K": "v"}}))
        cfg = load_repo_bridge_config(tmp_path)
        assert cfg is not None and cfg.default_env == {"K": "v"}

    def test_unknown_keys_ignored(self, tmp_path: Path):
        cfg_dir = tmp_path / ".copilot-extensions" / "agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"default_copilot_args": ["--model", "m"], "future_key": 123}),
        )
        cfg = load_repo_bridge_config(tmp_path)
        assert cfg is not None and cfg.default_copilot_args == ["--model", "m"]

    def test_bad_yaml_refuses_unknown_authority(self, tmp_path: Path):
        cfg_dir = tmp_path / ".copilot-extensions" / "agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text("{ not: valid: yaml:")
        with pytest.raises(ValueError, match="refusing authority fallback"):
            load_repo_bridge_config(tmp_path)

    def test_empty_file_is_defaults(self, tmp_path: Path):
        cfg_dir = tmp_path / ".copilot-extensions" / "agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text("")
        cfg = load_repo_bridge_config(tmp_path)
        assert cfg is not None
        assert cfg.default_copilot_args == []
        assert cfg.default_env == {}

    def test_legacy_path_remains_readable(self, tmp_path: Path):
        cfg_dir = tmp_path / ".agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"default_copilot_args": ["--model", "legacy"]}),
        )

        cfg = load_repo_bridge_config(tmp_path)

        assert cfg is not None
        assert cfg.default_copilot_args == ["--model", "legacy"]

    def test_canonical_path_wins_over_legacy(self, tmp_path: Path):
        legacy_dir = tmp_path / ".agent-bridge"
        legacy_dir.mkdir()
        (legacy_dir / "config.yaml").write_text(
            yaml.dump({"default_copilot_args": ["--model", "legacy"]}),
        )
        cfg_dir = tmp_path / ".copilot-extensions" / "agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"default_copilot_args": ["--model", "canonical"]}),
        )

        cfg = load_repo_bridge_config(tmp_path)

        assert cfg is not None
        assert cfg.default_copilot_args == ["--model", "canonical"]

    def test_marketplace_overlay_merges_over_base(self, tmp_path: Path, monkeypatch):
        cfg_dir = tmp_path / ".copilot-extensions" / "agent-bridge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"default_copilot_args": ["--model", "base"]}),
        )
        overlay = (
            cfg_dir / "marketplaces" / "example-marketplace" / "config.yaml"
        )
        overlay.parent.mkdir(parents=True)
        overlay.write_text(yaml.dump({"default_env": {"K": "v"}}))
        monkeypatch.setenv(
            "COPILOT_EXTENSIONS_CONTEXT",
            '{"marketplaceId":"example-marketplace"}',
        )

        cfg = load_repo_bridge_config(tmp_path)

        assert cfg is not None
        assert cfg.default_copilot_args == ["--model", "base"]
        assert cfg.default_env == {"K": "v"}


class TestAgentRosterCacheIntervalValidation:
    """Phase 3b: `agent_roster_cache_interval` must stay a finite number of
    seconds, at least 1.0 -- `AgentRosterCache.__init__` silently clamps
    anything below 1.0 up to it (`max(1.0, refresh_interval)`), so accepting
    a smaller value here would validate a cadence the cache never actually
    honors. An infinite value would make the cache's freshness deadline and
    watchdog timeout infinite too, so a successful roster could stay
    "fresh" forever and background supervision would never run again."""

    @pytest.mark.parametrize(
        "value", [0, 0.5, -1.0, float("-inf"), float("inf"), float("nan")],
    )
    def test_rejects_below_the_caches_own_floor_and_non_finite_values(self, value):
        with pytest.raises(Exception):
            ServiceConfig(agent_roster_cache_interval=value)

    def test_accepts_a_normal_value_at_or_above_the_floor(self):
        cfg = ServiceConfig(agent_roster_cache_interval=30.0)
        assert cfg.agent_roster_cache_interval == 30.0
        cfg_at_floor = ServiceConfig(agent_roster_cache_interval=1.0)
        assert cfg_at_floor.agent_roster_cache_interval == 1.0

    def test_default_is_unaffected(self):
        cfg = ServiceConfig()
        assert cfg.agent_roster_cache_interval == 12.0
