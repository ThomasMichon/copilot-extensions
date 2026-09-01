"""Tests for trusted registered local marketplace source overrides."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import agent_worktrees.__main__ as main
import pytest
from agent_worktrees import marketplace_overrides as mo


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_repo_settings(repo: Path, data: dict, *, local: bool = False) -> None:
    name = "settings.local.json" if local else "settings.json"
    _write_json(repo / ".github" / "copilot" / name, data)


def _write_marketplace(checkout: Path, name: str) -> Path:
    root = checkout / ".ai"
    _write_json(
        root / ".claude-plugin" / "marketplace.json",
        {"name": name, "plugins": []},
    )
    return root


def _entry(path: Path):
    return SimpleNamespace(local_path=lambda: str(path))


def _overlay(repo: Path) -> dict:
    return json.loads(
        (repo / ".github" / "copilot" / "settings.local.json").read_text(
            encoding="utf-8"
        )
    )


def _remote_definition() -> dict:
    return {"source": {"source": "github", "repo": "example/marketplace"}}


@pytest.fixture
def safe_output(monkeypatch):
    monkeypatch.setattr(
        mo, "_ensure_output_is_local", lambda _repo, *, ensure_ignored: None
    )
    monkeypatch.setattr(
        mo.git_ops,
        "prepare_worktree_base",
        lambda *a, **k: SimpleNamespace(
            start_point="origin/main",
            fetched=True,
            fetch_error=None,
            anchor=SimpleNamespace(reason="up-to-date"),
        ),
    )


def test_anchor_override_preserves_enabled_plugins_and_unrelated_settings(
    tmp_path: Path, monkeypatch, safe_output
):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    repo.mkdir()
    local_root = _write_marketplace(marketplace_checkout, "shared-marketplace")
    _write_repo_settings(
        repo,
        {
            "extraKnownMarketplaces": {
                "shared-marketplace": _remote_definition()
            },
            "enabledPlugins": {"tool@shared-marketplace": True},
        },
    )
    _write_repo_settings(
        repo,
        {
            "theme": "dark",
            "enabledPlugins": {
                "tool@shared-marketplace": True,
                "operator@other": False,
            },
        },
        local=True,
    )
    monkeypatch.setattr(
        mo.repos_mod,
        "find_repo",
        lambda name: (
            _entry(marketplace_checkout) if name == "shared-marketplace" else None
        ),
    )

    summary = mo.reconcile(
        repo,
        refresh_repositories=True,
        fast_forward_repositories=True,
    )
    overlay = _overlay(repo)

    assert summary["marketplaces"] == ["shared-marketplace"]
    assert Path(
        overlay["extraKnownMarketplaces"]["shared-marketplace"]["source"]["path"]
    ) == local_root.resolve()
    assert overlay["extraKnownMarketplaces"]["shared-marketplace"]["source"][
        "source"
    ] == "directory"
    assert overlay["enabledPlugins"] == {
        "tool@shared-marketplace": True,
        "operator@other": False,
    }
    assert overlay["theme"] == "dark"


def test_registered_marketplace_refreshes_once_before_binding(
    tmp_path: Path, monkeypatch, safe_output
):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    repo.mkdir()
    _write_marketplace(marketplace_checkout, "shared-marketplace")
    _write_repo_settings(
        repo,
        {
            "extraKnownMarketplaces": {
                "shared-marketplace": _remote_definition(),
                "shared-marketplace-copy": _remote_definition(),
            }
        },
    )
    entry = SimpleNamespace(
        local_path=lambda: str(marketplace_checkout),
        remote="https://example.com/shared-marketplace.git",
        default_branch="main",
    )
    monkeypatch.setattr(mo.repos_mod, "find_repo", lambda _name: entry)
    monkeypatch.setattr(
        mo.git_ops,
        "resolve_remote_name",
        lambda value, *, cwd: "origin",
    )
    calls = []
    monkeypatch.setattr(
        mo.git_ops,
        "prepare_worktree_base",
        lambda *a, **k: (
            calls.append((a, k))
            or SimpleNamespace(
                start_point="origin/main",
                fetched=True,
                fetch_error=None,
                anchor=SimpleNamespace(reason="updated"),
            )
        ),
    )

    summary = mo.reconcile(
        repo,
        refresh_repositories=True,
        fast_forward_repositories=True,
    )

    assert len(calls) == 1
    assert calls[0][1]["remote"] == "origin"
    assert summary["repository_refresh"][str(marketplace_checkout.resolve())] == {
        "fetched": True,
        "fetch_failed": False,
        "anchor": "updated",
        "start_point": "origin/main",
    }


def test_registered_marketplace_refresh_can_be_disabled(
    tmp_path: Path, monkeypatch, safe_output
):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    repo.mkdir()
    _write_marketplace(marketplace_checkout, "shared-marketplace")
    _write_repo_settings(
        repo,
        {
            "extraKnownMarketplaces": {
                "shared-marketplace": _remote_definition()
            }
        },
    )
    monkeypatch.setattr(
        mo.repos_mod,
        "find_repo",
        lambda _name: _entry(marketplace_checkout),
    )
    monkeypatch.setattr(
        mo.git_ops,
        "prepare_worktree_base",
        lambda *a, **k: pytest.fail("refresh must remain disabled"),
    )

    summary = mo.reconcile(repo, refresh_repositories=False)

    assert summary["repository_refresh"] == {}


def test_anchor_and_linked_worktree_receive_independent_overlays(
    tmp_path: Path, monkeypatch, safe_output
):
    anchor = tmp_path / "consumer"
    worktree = tmp_path / "consumer.worktrees" / "wt-1"
    marketplace_checkout = tmp_path / "marketplace"
    local_root = _write_marketplace(marketplace_checkout, "shared-marketplace")
    for checkout in (anchor, worktree):
        checkout.mkdir(parents=True)
        _write_repo_settings(
            checkout,
            {
                "extraKnownMarketplaces": {
                    "shared-marketplace": _remote_definition()
                }
            },
        )
    monkeypatch.setattr(
        mo.repos_mod, "find_repo", lambda _name: _entry(marketplace_checkout)
    )

    mo.reconcile(anchor)
    mo.reconcile(worktree)

    for checkout in (anchor, worktree):
        path = _overlay(checkout)["extraKnownMarketplaces"][
            "shared-marketplace"
        ]["source"]["path"]
        assert Path(path) == local_root.resolve()


def test_missing_checkout_retires_stale_managed_override(
    tmp_path: Path, monkeypatch, safe_output
):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    repo.mkdir()
    _write_marketplace(marketplace_checkout, "shared-marketplace")
    _write_repo_settings(
        repo,
        {
            "extraKnownMarketplaces": {
                "shared-marketplace": _remote_definition()
            }
        },
    )
    _write_repo_settings(repo, {"editor": "vim"}, local=True)
    entry = _entry(marketplace_checkout)
    monkeypatch.setattr(mo.repos_mod, "find_repo", lambda _name: entry)
    mo.reconcile(repo)

    monkeypatch.setattr(mo.repos_mod, "find_repo", lambda _name: None)
    summary = mo.reconcile(repo)
    overlay = _overlay(repo)

    assert summary["skipped"] == {"shared-marketplace": "unregistered"}
    assert "extraKnownMarketplaces" not in overlay
    assert "_agentWorktreesMarketplaceOverrides" not in overlay
    assert overlay["editor"] == "vim"


def test_manifest_name_mismatch_retires_stale_override(
    tmp_path: Path, monkeypatch, safe_output
):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    repo.mkdir()
    root = _write_marketplace(marketplace_checkout, "shared-marketplace")
    _write_repo_settings(
        repo,
        {
            "extraKnownMarketplaces": {
                "shared-marketplace": _remote_definition()
            }
        },
    )
    monkeypatch.setattr(
        mo.repos_mod, "find_repo", lambda _name: _entry(marketplace_checkout)
    )
    mo.reconcile(repo)
    _write_json(
        root / ".claude-plugin" / "marketplace.json",
        {"name": "different-name", "plugins": []},
    )

    summary = mo.reconcile(repo)

    assert summary["skipped"] == {"shared-marketplace": "name-mismatch"}
    assert not (repo / ".github" / "copilot" / "settings.local.json").exists()


def test_operator_modified_override_is_preserved_and_not_reclaimed(
    tmp_path: Path, monkeypatch, safe_output
):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    operator_checkout = tmp_path / "operator"
    repo.mkdir()
    _write_marketplace(marketplace_checkout, "shared-marketplace")
    operator_root = _write_marketplace(operator_checkout, "shared-marketplace")
    _write_repo_settings(
        repo,
        {
            "extraKnownMarketplaces": {
                "shared-marketplace": _remote_definition()
            }
        },
    )
    monkeypatch.setattr(
        mo.repos_mod, "find_repo", lambda _name: _entry(marketplace_checkout)
    )
    mo.reconcile(repo)
    overlay = _overlay(repo)
    overlay["extraKnownMarketplaces"]["shared-marketplace"]["source"]["path"] = str(
        operator_root.resolve()
    )
    _write_repo_settings(repo, overlay, local=True)

    mo.reconcile(repo)
    overlay = _overlay(repo)

    assert Path(
        overlay["extraKnownMarketplaces"]["shared-marketplace"]["source"]["path"]
    ) == operator_root.resolve()
    assert "_agentWorktreesMarketplaceOverrides" not in overlay


def test_user_global_marketplace_is_eligible(
    tmp_path: Path, monkeypatch, safe_output
):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    repo.mkdir()
    local_root = _write_marketplace(marketplace_checkout, "global-marketplace")
    _write_json(
        Path.home() / ".copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "global-marketplace": {
                    "source": {
                        "source": "git",
                        "url": "https://example.com/marketplace.git",
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        mo.repos_mod, "find_repo", lambda _name: _entry(marketplace_checkout)
    )

    mo.reconcile(repo)

    path = _overlay(repo)["extraKnownMarketplaces"]["global-marketplace"][
        "source"
    ]["path"]
    assert Path(path) == local_root.resolve()


def test_session_start_emits_restart_only_when_changed(
    tmp_path: Path, monkeypatch, capsys
):
    repo = tmp_path / "consumer"
    repo.mkdir()
    monkeypatch.setattr(main, "_checkout_root", lambda _path: repo)
    results = iter(
        [
            {
                "action": "reconciled",
                "changed": True,
                "settings_local": str(repo / "settings.local.json"),
            },
            {
                "action": "reconciled",
                "changed": False,
                "settings_local": str(repo / "settings.local.json"),
            },
        ]
    )
    calls = []

    def reconcile(_repo, **kwargs):
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(mo, "reconcile", reconcile)
    args = SimpleNamespace(
        cwd=str(repo),
        stdin=False,
        session_start=True,
        ensure_ignored=False,
        json=False,
    )

    assert main.cmd_reconcile_marketplaces(args) == 0
    assert "Restart Copilot CLI" in capsys.readouterr().out
    assert main.cmd_reconcile_marketplaces(args) == 0
    assert capsys.readouterr().out.strip() == "{}"
    assert calls == [
        {
            "ensure_ignored": False,
            "refresh_repositories": False,
            "fast_forward_repositories": False,
        },
        {
            "ensure_ignored": False,
            "refresh_repositories": False,
            "fast_forward_repositories": False,
        },
    ]


def test_manual_reconcile_honors_project_fast_forward_opt_out(
    tmp_path: Path, monkeypatch, capsys
):
    repo = tmp_path / "consumer"
    repo.mkdir()
    monkeypatch.setattr(main, "_checkout_root", lambda _path: repo)
    monkeypatch.setattr(main, "_reverse_lookup_project", lambda _repo: "consumer")
    monkeypatch.setattr(main.cfg, "project_dir", lambda _name: tmp_path / ".consumer")
    monkeypatch.setattr(
        main.cfg,
        "load_config",
        lambda _path=None: SimpleNamespace(auto_fast_forward=False),
    )
    calls = []

    def reconcile(_repo, **kwargs):
        calls.append(kwargs)
        return {
            "action": "reconciled",
            "changed": False,
            "settings_local": str(repo / "settings.local.json"),
        }

    monkeypatch.setattr(mo, "reconcile", reconcile)
    args = SimpleNamespace(
        cwd=str(repo),
        stdin=False,
        session_start=False,
        ensure_ignored=False,
        json=False,
    )

    assert main.cmd_reconcile_marketplaces(args) == 0
    assert "Verified marketplace overrides" in capsys.readouterr().out
    assert calls == [
        {
            "ensure_ignored": False,
            "refresh_repositories": True,
            "fast_forward_repositories": False,
        }
    ]


def test_checkout_root_decodes_filesystem_bytes(tmp_path: Path, monkeypatch):
    repo = tmp_path / "consumer-\u00e9"
    repo.mkdir()
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout=os.fsencode(str(repo)) + b"\n",
            stderr=b"",
        ),
    )

    assert main._checkout_root(tmp_path) == repo.resolve()


def test_launch_and_hook_surfaces_include_reconciler():
    plugin = Path(__file__).parents[1]
    assert "reconcile-marketplaces" in (
        plugin / "bin" / "launch-session.ps1"
    ).read_text(encoding="utf-8")
    assert "reconcile-marketplaces" in (
        plugin / "bin" / "launch-session.sh"
    ).read_text(encoding="utf-8")
    hooks = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))
    commands = json.dumps(hooks["hooks"]["sessionStart"])
    assert "marketplace-overrides.ps1" in commands
    assert "marketplace-overrides.sh" in commands


def test_tracked_local_settings_are_rejected(tmp_path: Path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    main.git_ops.git("init", str(repo))
    _write_repo_settings(repo, {"theme": "tracked"}, local=True)
    main.git_ops.git("add", ".github/copilot/settings.local.json", cwd=repo)

    with pytest.raises(mo.MarketplaceOverrideError, match="tracked"):
        mo.reconcile(repo, ensure_ignored=True)


def test_case_variant_tracked_local_settings_are_rejected(tmp_path: Path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    main.git_ops.git("init", str(repo))
    tracked = repo / ".github" / "Copilot" / "settings.local.json"
    _write_json(tracked, {"theme": "tracked"})
    main.git_ops.git("add", ".github/Copilot/settings.local.json", cwd=repo)

    with pytest.raises(mo.MarketplaceOverrideError, match="tracked"):
        mo.reconcile(repo, ensure_ignored=True)


def test_git_status_error_fails_closed(tmp_path: Path, monkeypatch):
    repo = tmp_path / "consumer"
    repo.mkdir()
    monkeypatch.setattr(
        mo,
        "_git",
        lambda *_args: subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr="not a repository"
        ),
    )

    with pytest.raises(mo.MarketplaceOverrideError, match="cannot determine"):
        mo.reconcile(repo, ensure_ignored=True)


def test_git_subprocess_exception_fails_closed(tmp_path: Path, monkeypatch):
    repo = tmp_path / "consumer"
    repo.mkdir()

    def _raise(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git"], timeout=10)

    monkeypatch.setattr(subprocess, "run", _raise)

    with pytest.raises(mo.MarketplaceOverrideError, match="Git ownership"):
        mo.reconcile(repo, ensure_ignored=True)


def test_ensure_ignored_adds_git_exclude_rule(tmp_path: Path, monkeypatch):
    repo = tmp_path / "consumer"
    marketplace_checkout = tmp_path / "marketplace"
    repo.mkdir()
    main.git_ops.git("init", str(repo))
    _write_marketplace(marketplace_checkout, "shared-marketplace")
    _write_repo_settings(
        repo,
        {
            "extraKnownMarketplaces": {
                "shared-marketplace": _remote_definition()
            }
        },
    )
    monkeypatch.setattr(
        mo.repos_mod, "find_repo", lambda _name: _entry(marketplace_checkout)
    )
    mo.reconcile(repo, ensure_ignored=True)

    ignored = main.git_ops.git(
        "check-ignore",
        "--quiet",
        "--",
        ".github/copilot/settings.local.json",
        cwd=repo,
        check=False,
    )
    assert ignored.returncode == 0


def test_reparse_output_parent_is_rejected(tmp_path: Path):
    repo = tmp_path / "consumer"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    try:
        (repo / ".github").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(mo.MarketplaceOverrideError, match="link or reparse"):
        mo.reconcile(repo)
