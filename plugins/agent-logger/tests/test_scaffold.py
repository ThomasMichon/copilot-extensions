"""Smoke tests for the agent-logger scaffold."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent_logger import __version__
from agent_logger.__main__ import main as cli_main
from agent_logger.config import (
    DEFAULTS,
    RepositoryConfigError,
    find_repo_config,
    load_config,
)
from agent_logger.repo_trust import _normalize_git_remote
from agent_logger.segmenter import prepare_log
from agent_logger.segmenter.platform import detect_machine, sanitize_path_component

from .conftest import init_git_repo as _init_git_repo


def test_version_matches_build_info() -> None:
    """``_build_info.__version__`` must track ``pyproject.toml`` (version triplet)."""
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match, "version not found in pyproject.toml"
    assert __version__ == match.group(1)


def test_runtime_is_reconciled_on_every_machine() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
    install_ps1 = (plugin_root / "scripts" / "install.ps1").read_text(
        encoding="utf-8"
    )
    install_sh = (plugin_root / "scripts" / "install.sh").read_text(
        encoding="utf-8"
    )

    assert manifest["runtimeScope"] == "universal"
    assert "[System.IO.FileShare]::None" in install_ps1
    assert "Timed out waiting for the agent-logger install lock" in install_ps1
    assert "$lockContended" in install_ps1
    assert "$script:SkipPackageInstall" in install_ps1
    assert "$script:SkipStamp" in install_ps1
    assert "Refusing stale agent-logger" in install_ps1
    assert "'stamped-version' = $SrcVersion" in install_ps1
    assert "Move-Item -LiteralPath $tmp -Destination $path -Force" in install_ps1
    assert (
        "$Action -in @('install', 'update', 'provision', 'stamp')" in install_ps1
    )
    assert "$lockContended -and $Action" not in install_ps1
    assert "ConvertTo-AgentLoggerVersionKey" in install_ps1
    assert ".install-complete.json" in install_ps1
    assert 'flock -w 300 8' in install_sh
    assert "__install_lock_contended" in install_sh
    assert "SKIP_PACKAGE_INSTALL" in install_sh
    assert "__version_cmp" in install_sh
    assert "SKIP_STAMP" in install_sh
    assert "printf '%s' -1" in install_sh
    assert "refusing stale agent-logger" in install_sh
    assert "left_major" in install_sh
    assert 'elif [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]' in install_sh
    assert 'mv -f "$version_tmp" "${INSTALL_DIR}/stamped-version"' in install_sh
    assert '"$ACTION" =~ ^(install|update|provision|stamp)$' in install_sh
    assert (
        '"$__install_lock_contended" = 1 && "$ACTION" =~ ^(install|update|provision)'
        not in install_sh
    )
    assert 'VENV="$INSTALL_DIR/versions/$__current"' in install_sh
    assert (
        'if [[ "$SKIP_PACKAGE_INSTALL" = 0 ]]; then _write_deploy_manifest; fi'
        in install_sh
    )
    assert ".install-complete.json" in install_sh


def test_log_writer_is_a_read_only_renderer() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    agent = (plugin_root / "agents" / "session-log-writer.agent.md").read_text(
        encoding="utf-8"
    )
    log_skill = (plugin_root / "skills" / "log-session" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    backlog_skill = (
        plugin_root / "skills" / "process-backlog" / "SKILL.md"
    ).read_text(encoding="utf-8")
    backlog_manifest = (
        plugin_root / "skills" / "process-backlog" / "references" / "manifest.json"
    ).read_text(encoding="utf-8")

    assert "Remain read-only." in agent
    assert "agent-logger:artifact" in agent
    assert "action: create|append" in agent
    assert "boundary: <16 lowercase hex characters>" in agent
    assert "base_sha256: <64 lowercase hex characters; append only>" in agent
    assert '"status": "rendered"' in agent
    assert '"target_log_path": "<prep.log_path>"' in log_skill
    assert "`create` -- the path must equal `target_log_path`" in log_skill
    assert "`append` -- the target must exist" in log_skill
    assert "Recompute its SHA-256 immediately before" in log_skill
    assert '"return": "json"' in backlog_skill
    assert json.loads(backlog_manifest)["return"] == "json"
    assert "Reject non-`.md` targets" in backlog_skill
    assert "recompute SHA-256" in backlog_skill


def test_config_defaults_and_home(tmp_path: Path) -> None:
    cfg = load_config(home=tmp_path)
    assert cfg.home == tmp_path
    # Unset paths resolve under the home dir.
    assert cfg.store_dir == tmp_path / "session-digests"
    assert cfg.sync_path == tmp_path / "sessions"
    assert cfg.sync_target == "local"
    assert cfg.voice_pack == "none"
    assert cfg.note_marker == DEFAULTS["log"]["note_marker"]


def test_config_user_override(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "sync:\n  target: onedrive\nlog:\n  voice_pack: custom\n",
        encoding="utf-8",
    )
    cfg = load_config(home=tmp_path)
    assert cfg.sync_target == "onedrive"
    assert cfg.voice_pack == "custom"


def test_repo_config_overrides_log_layout_only(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "sync:\n  target: onedrive\nlog:\n  path_template: global/{title}.md\n",
        encoding="utf-8",
    )

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "log:",
                "  root: .",
                "  path_template: logs/{year}/{month}.{day} {title}.md",
                "  template: |",
                "    # {title}",
                "",
                "    **Date:** {date}",
                "  narration_style: Use brief section introductions.",
                "  exemplars:",
                "    - docs/example.md",
                "  closing_remark: End with one concise takeaway.",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo / "src")

    cfg = load_config(home=home)

    assert cfg.repo_config_path == repo / ".agent-logger.yaml"
    assert find_repo_config() == repo / ".agent-logger.yaml"
    assert cfg.sync_target == "onedrive"
    assert cfg.log_root == repo
    assert cfg.log_path_template == "logs/{year}/{month}.{day} {title}.md"
    assert cfg.log_template is not None
    assert "**Date:** {date}" in cfg.log_template
    assert cfg.narration_style == "Use brief section introductions."
    assert cfg.exemplars == ["docs/example.md"]
    assert cfg.closing_remark == "End with one concise takeaway."


def test_repo_config_sync_local_path_overrides_machine_local(
    tmp_path: Path, monkeypatch
) -> None:
    """schema v3's one deliberate relaxation: ``sync.local_path`` is honored
    from repo-local config -- the canonical facility-wide sync destination,
    the same absolute value for every machine, closing the exact drift that
    motivated it (a machine with no local sync config at all, or one with a
    stale/wrong path)."""
    home = tmp_path / "home"
    home.mkdir()
    # No config.yaml at all here -- exactly the "never configured" case this
    # field exists to fix.

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 3\nsync:\n  local_path: /mnt/nas/Lake/Copilot/sessions\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    cfg = load_config(home=home)

    assert cfg.sync_path == Path("/mnt/nas/Lake/Copilot/sessions")
    # Everything else stays machine-local/default -- only the path moved.
    assert cfg.sync_target == "local"


def test_repo_config_sync_local_path_does_not_affect_other_sync_fields(
    tmp_path: Path, monkeypatch
) -> None:
    """The relaxation is scoped to exactly one field -- machine identity,
    the active target choice, and everything else stay machine-local."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "sync:\n  target: onedrive\nmachine:\n  name: my-machine\n",
        encoding="utf-8",
    )

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 3\nsync:\n  local_path: /mnt/nas/Lake/Copilot/sessions\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    cfg = load_config(home=home)

    assert cfg.sync_target == "onedrive"  # untouched by the repo config
    assert cfg.sync_path == Path("/mnt/nas/Lake/Copilot/sessions")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("schema_version: 0\nlog: {}\n", "schema_version must be >= 1"),
        ("schema_version: nope\nlog: {}\n", "schema_version must be an integer"),
        ("schema_version: 1\nlog:\n  root: ../logs\n", "must not escape"),
        (
            'schema_version: 1\nlog:\n  path_template: "{repository}/{title}.md"\n',
            r"unsupported placeholder \{repository\}",
        ),
        (
            "schema_version: 1\nlog:\n  template: ['not markdown']\n",
            "log.template must be a non-empty string",
        ),
        (
            "schema_version: 1\nlog:\n  voice_pack: surprise\n",
            r"unsupported field\(s\): voice_pack",
        ),
        (
            "schema_version: 1\nlog:\n  closing_remark: [not, text]\n",
            "log.closing_remark must be null or a non-empty string",
        ),
        (
            "schema_version: 1\nlog:\n  exemplars: [valid, '']\n",
            "log.exemplars must be null",
        ),
        (
            "schema_version: 1\nsync:\n  target: ssh\nlog: {}\n",
            r"sync contains unsupported field\(s\): target",
        ),
        (
            "schema_version: 1\nsync:\n  local_path: relative/path\nlog: {}\n",
            "sync.local_path must be an absolute path",
        ),
        (
            "schema_version: 1\nsync:\n  local_path: /\nlog: {}\n",
            "must not be a bare filesystem root",
        ),
        (
            "schema_version: 1\nsync:\n  local_path: '~/nas'\nlog: {}\n",
            "must not use '~'",
        ),
        (
            "schema_version: 1\nsync:\n  local_path: 'C:\\nas\\sessions'\nlog: {}\n",
            "sync.local_path must be an absolute path",
        ),
    ],
)
def test_repo_config_validation_errors(
    tmp_path: Path, monkeypatch, body: str, message: str
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(body, encoding="utf-8")
    monkeypatch.chdir(repo)

    with pytest.raises(RepositoryConfigError, match=message):
        load_config(home=tmp_path / "home")


def test_repo_config_forward_compatible_future_schema(
    tmp_path: Path, monkeypatch
) -> None:
    """A repo config from a NEWER schema than this build supports is read
    tolerantly: unknown top-level and unknown log fields are ignored (rolling
    updates), while known log fields are still honored."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "schema_version: 99",
                "future_top_block:",
                "  anything: 1",
                "log:",
                "  note_marker: 'NOTE:'",
                "  future_log_field: whatever",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    cfg = load_config(home=tmp_path / "home")
    assert cfg.repo_config_path == repo / ".agent-logger.yaml"
    assert cfg.note_marker == "NOTE:"  # known field honored despite unknowns


def test_non_logging_config_ignores_invalid_repo_file(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 1\nlog:\n  root: ../outside\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    cfg = load_config(home=tmp_path / "home", include_repo=False)

    assert cfg.repo_config_path is None
    assert cfg.sync_target == "local"


def test_missing_explicit_repo_config_is_an_error(
    tmp_path: Path, monkeypatch
) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("AGENT_LOGGER_REPO_CONFIG", str(missing))

    with pytest.raises(RepositoryConfigError, match="does not name a file"):
        load_config(home=tmp_path / "home")


@pytest.mark.no_autotrust
def test_explicit_repo_config_env_still_requires_trust(
    tmp_path: Path, monkeypatch
) -> None:
    """AGENT_LOGGER_REPO_CONFIG names a *file*, not a trust decision -- an
    untrusted (unregistered) checkout must not be able to bypass the gate
    just because something points this variable at its own repo-local
    file."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    config_file = repo / ".agent-logger.yaml"
    config_file.write_text("log:\n  path_template: logs/{title}.md\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(tmp_path / "no-such-registry.yaml"))
    monkeypatch.setenv("AGENT_LOGGER_REPO_CONFIG", str(config_file))

    assert find_repo_config() is None


@pytest.mark.no_autotrust
def test_repo_config_rejects_symlinked_candidate(tmp_path: Path, monkeypatch) -> None:
    """A committed/local symlink standing in for .agent-logger.yaml must be
    rejected outright -- discovery must never follow a link out of the
    checkout to read arbitrary machine-local YAML."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    outside_target = tmp_path / "outside.yaml"
    outside_target.write_text("log:\n  path_template: logs/{title}.md\n", encoding="utf-8")
    (repo / ".agent-logger.yaml").symlink_to(outside_target)

    registry = tmp_path / "repos.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    monkeypatch.chdir(repo)

    assert find_repo_config() is None


@pytest.mark.no_autotrust
def test_repo_config_ignored_on_conflicting_registered_default_branches(
    tmp_path: Path, monkeypatch
) -> None:
    """A checkout with two local remotes that each match a DIFFERENT
    registered project, with different default branches, has no single
    resolvable trust decision -- must fail safe to untrusted rather than
    picking whichever remote git happens to enumerate first."""
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        remote="https://example.test/example-owner/demo.git",
        branch="main",
        remote_name="origin",
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "upstream",
            "https://example.test/example-owner/other.git",
        ],
        check=True,
    )
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n", encoding="utf-8"
    )

    registry = tmp_path / "repos.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    },
                    "other": {
                        "remote": "https://example.test/example-owner/other.git",
                        "default_branch": "develop",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    monkeypatch.chdir(repo)

    assert find_repo_config() is None


@pytest.mark.no_autotrust
def test_repo_config_ignores_global_git_config_remote(
    tmp_path: Path, monkeypatch
) -> None:
    """The remote probe must be scoped to the checkout's LOCAL config
    (--local) -- a global/system git config entry matching a registered
    project must not let a checkout with no local remote at all pass the
    gate."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote=None, branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n", encoding="utf-8"
    )

    registry = tmp_path / "repos.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))

    # Sandbox a fake "global" git config (never touches the real one) that
    # declares the same URL as a remote -- git config --local must ignore it.
    fake_global = tmp_path / "fake-gitconfig"
    subprocess.run(
        [
            "git",
            "config",
            "--file",
            str(fake_global),
            "remote.origin.url",
            "https://example.test/example-owner/demo.git",
        ],
        check=True,
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_global))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.chdir(repo)

    assert find_repo_config() is None


def test_config_cli_reports_repository_validation_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 1\nlog:\n  root: ../outside\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))

    assert cli_main(["config"]) == 2
    assert "invalid repository configuration" in capsys.readouterr().err


def test_repo_config_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_REPO_CONFIG", "0")

    cfg = load_config(home=tmp_path / "home")

    assert cfg.repo_config_path is None
    assert cfg.log_path_template == DEFAULTS["log"]["path_template"]


@pytest.mark.no_autotrust
def test_repo_config_honored_when_registered_and_on_default_branch(
    tmp_path: Path, monkeypatch
) -> None:
    """The real trust gate: a genuine git checkout whose remote matches a
    registered project, on that project's registered default branch, gets
    its repo-local config honored end to end."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )

    registry = tmp_path / "repos.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "git@example.test:example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    monkeypatch.chdir(repo)

    assert find_repo_config() == repo / ".agent-logger.yaml"
    cfg = load_config(home=tmp_path / "home")
    assert cfg.log_path_template == "logs/{title}.md"


@pytest.mark.no_autotrust
def test_repo_config_honored_via_agent_home_registry(
    tmp_path: Path, monkeypatch
) -> None:
    """Without an explicit AGENT_WORKTREES_REPOS_YAML override, the trust
    gate must still find the registry under $AGENT_HOME -- mirroring
    agent-worktrees' own legacy registry-root fallback -- rather than only
    ever looking under the bare ~/.agent-worktrees default."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )

    agent_home = tmp_path / "custom-agent-home"
    registry_dir = agent_home / ".agent-worktrees"
    registry_dir.mkdir(parents=True)
    (registry_dir / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AGENT_WORKTREES_REPOS_YAML", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(agent_home))
    monkeypatch.chdir(repo)

    assert find_repo_config() == repo / ".agent-logger.yaml"


@pytest.mark.no_autotrust
def test_repo_config_honored_with_non_origin_remote_name(
    tmp_path: Path, monkeypatch
) -> None:
    """A registered checkout using a non-``origin`` local remote name (e.g.
    ``upstream``) must still be trusted -- the gate matches by URL across
    ALL local remotes, not a hardcoded ``origin``."""
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        remote="https://example.test/example-owner/demo.git",
        branch="main",
        remote_name="upstream",
    )
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )

    registry = tmp_path / "repos.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    monkeypatch.chdir(repo)

    assert find_repo_config() == repo / ".agent-logger.yaml"


@pytest.mark.no_autotrust
def test_repo_config_ignored_when_repo_not_registered(
    tmp_path: Path, monkeypatch
) -> None:
    """A real, matching-remote checkout that simply isn't in the registry
    (or the registry doesn't exist) gets its repo-local config ignored --
    never an error, just treated as absent."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(tmp_path / "no-such-registry.yaml"))
    monkeypatch.chdir(repo)

    assert find_repo_config() is None
    cfg = load_config(home=tmp_path / "home")
    assert cfg.repo_config_path is None
    assert cfg.log_path_template == DEFAULTS["log"]["path_template"]


@pytest.mark.no_autotrust
def test_repo_config_ignored_when_not_on_default_branch(
    tmp_path: Path, monkeypatch
) -> None:
    """A registered repo checked out on a feature/PR branch (not the
    registered default branch) does not get its repo-local config honored --
    an unreviewed branch must not be able to redirect facility behavior."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="feature-x")
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )

    registry = tmp_path / "repos.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    monkeypatch.chdir(repo)

    assert find_repo_config() is None


@pytest.mark.no_autotrust
def test_repo_config_ignored_when_no_git_remote(tmp_path: Path, monkeypatch) -> None:
    """A real git checkout with no ``origin`` remote at all (e.g. a bare
    local scratch clone) can never be a registered project, so its
    repo-local config is ignored."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote=None, branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    assert find_repo_config() is None


@pytest.mark.no_autotrust
def test_repo_config_trust_override_env_bypasses_gate(
    tmp_path: Path, monkeypatch
) -> None:
    """``AGENT_LOGGER_TRUST_REPO_CONFIG=<path>`` is a machine-local escape
    hatch for an operator who has independently confirmed one specific
    checkout is safe -- it must never be settable by the repo itself, only
    by the local environment, and it is scoped to that exact resolved path
    (never a global boolean, which would also trust every OTHER unregistered
    checkout probed in the same process)."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, remote=None, branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_TRUST_REPO_CONFIG", str(repo.resolve()))

    assert find_repo_config() == repo / ".agent-logger.yaml"


@pytest.mark.no_autotrust
def test_repo_config_trust_override_env_does_not_bypass_other_checkouts(
    tmp_path: Path, monkeypatch
) -> None:
    """Trusting one checkout via the override must not trust a DIFFERENT
    unregistered checkout probed in the same process."""
    trusted = tmp_path / "trusted"
    _init_git_repo(trusted, remote=None, branch="main")
    other = tmp_path / "other"
    _init_git_repo(other, remote=None, branch="main")
    (other / ".agent-logger.yaml").write_text(
        "log:\n  path_template: logs/{title}.md\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_LOGGER_TRUST_REPO_CONFIG", str(trusted.resolve()))

    assert find_repo_config(other) is None


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            "https://example.test/example-owner/demo.git",
            "git@example.test:example-owner/demo.git",
        ),
        (
            "https://example.test/example-owner/demo",
            "ssh://git@example.test/example-owner/demo.git",
        ),
        (
            # Host case is folded; path case here is identical so this
            # doesn't (yet) exercise path-case handling -- see the
            # dedicated case-sensitivity tests below for that.
            "https://EXAMPLE.test/example-owner/demo.git",
            "https://example.test/example-owner/demo",
        ),
    ],
)
def test_normalize_git_remote_matches_equivalent_forms(a: str, b: str) -> None:
    assert _normalize_git_remote(a) == _normalize_git_remote(b)


def test_normalize_git_remote_distinguishes_different_repos() -> None:
    assert _normalize_git_remote(
        "https://example.test/example-owner/demo.git"
    ) != _normalize_git_remote("https://example.test/example-owner/other.git")


def test_normalize_git_remote_path_case_sensitive_on_non_github_host() -> None:
    """A self-hosted Git server (e.g. Gitea) can be case-sensitive on its
    filesystem, so path case is preserved -- not folded -- for any host
    other than github.com."""
    assert _normalize_git_remote(
        "https://example.test/Example-Owner/Demo.git"
    ) != _normalize_git_remote("https://example.test/example-owner/demo.git")


def test_normalize_git_remote_path_case_insensitive_on_github() -> None:
    """github.com is known case-insensitive for owner/repo, so (mirroring
    agent_worktrees.project_state's own identity resolution) a github.com
    remote's path case is folded too."""
    assert _normalize_git_remote(
        "https://github.com/Example-Owner/Demo.git"
    ) == _normalize_git_remote("https://github.com/example-owner/demo")


def test_organization_cli_reports_manifest_ready_config(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "log:",
                "  root: records",
                "  closing_remark: End with a short summary.",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))

    assert cli_main(["organization"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["repository_root"] == str(repo)
    assert result["config_path"] == str(repo / ".agent-logger.yaml")
    assert result["manifest"]["output_root"] == str(repo / "records")
    assert result["manifest"]["closing_remark"] == "End with a short summary."


def test_organization_defaults_to_repo_logs(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))

    assert cli_main(["organization"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["manifest"]["output_root"] == str(repo / "logs")
    assert result["manifest"]["closing_remark"] is None


def test_origin_backfill_corpus_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    """The `origin backfill-corpus` CLI tags a multi-machine corpus (Phase 4)."""
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))
    corpus = tmp_path / "nas"
    sess = corpus / "book2" / "session-state" / "s1"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (sess / "workspace.yaml").write_text(
        "git_root: /home/u/src/test-chamber\n", encoding="utf-8"
    )

    rc = cli_main(
        ["origin", "backfill-corpus", "--root", str(corpus),
         "--harness-repo", "test-chamber"]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "corpus"
    assert result["total"] == 1
    assert result["marked"] == 1
    stamped = json.loads((sess / "origin.json").read_text(encoding="utf-8"))
    assert stamped["source_repo"] == "test-chamber"
    assert stamped["machine"] == "book2"


def test_prepare_log_reports_repo_organization(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".agent-logger.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "log:",
                "  root: .",
                '  path_template: "logs/{year}/{month}.{day} {title}.md"',
                "  template: |",
                "    ## Summary",
                "",
                "    ## Follow-up",
                "  narration_style: Use short asides.",
                "  closing_remark: Close with one sentence.",
            ]
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    session = home / ".copilot" / "session-state" / "session-id"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text(
        '{"timestamp": "2026-07-22T18:00:00Z"}\n', encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(home / ".agent-logger"))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(
        sys,
        "argv",
        ["prepare-session-log", "--json", "--session", "session-id", "--title", "Test"],
    )

    prepare_log.main()

    result = json.loads(capsys.readouterr().out)
    assert result["output_root"] == str(repo)
    assert result["log_path_template"] == "logs/{year}/{month}.{day} {title}.md"
    assert result["log_template"] == "## Summary\n\n## Follow-up"
    assert result["narration_style"] == "Use short asides."
    assert result["closing_remark"] == "Close with one sentence."
    assert Path(result["log_path"]).parent == repo / "logs" / result["date"][:4]


def test_detect_machine_nonempty() -> None:
    assert detect_machine()


def test_sanitize_path_component_ntfs() -> None:
    assert sanitize_path_component('a:b/c"d') == "a -b-c'd"
    assert sanitize_path_component("   ") == "Untitled"
    # Reserved device name gets prefixed.
    assert sanitize_path_component("CON").startswith("_")
