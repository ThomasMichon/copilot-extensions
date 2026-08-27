"""Tests for trusted registered local marketplace source overrides."""

from __future__ import annotations

import json
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

    summary = mo.reconcile(repo)
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
    monkeypatch.setattr(
        mo, "reconcile", lambda _repo, *, ensure_ignored=False: next(results)
    )
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
        "--no-index",
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
