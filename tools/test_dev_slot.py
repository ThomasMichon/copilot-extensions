"""Tests for tools/dev_slot.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_slot


def _fake_preview(root: Path, plugin: str, version: str) -> Path:
    d = root / "preview" / plugin
    d.mkdir(parents=True, exist_ok=True)
    (d / "src.py").write_text("preview = True\n", encoding="utf-8")
    (d / "PREVIEW.json").write_text(
        json.dumps({"plugin": plugin, "hypothetical_version": version,
                    "source_commit": "abc123"}, indent=2),
        encoding="utf-8",
    )
    return d


def _fake_installed(root: Path, plugin: str) -> Path:
    d = root / "installed" / plugin
    d.mkdir(parents=True, exist_ok=True)
    (d / "src.py").write_text("real = True\n", encoding="utf-8")
    return d


def test_install_requires_yes_flag(tmp_path: Path, capsys):
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    code = dev_slot.main([
        "--target-root", str(tmp_path / "installed"),
        "install", "agent-worktrees", "--preview-dir", str(preview),
    ])
    assert code == 1
    assert "refusing without --yes" in capsys.readouterr().err
    assert not (tmp_path / "installed" / "agent-worktrees" / dev_slot.CLAIM_FILE).exists()


def test_install_backs_up_real_payload_and_writes_claim(tmp_path: Path):
    _fake_installed(tmp_path, "agent-worktrees")
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    target_root = tmp_path / "installed"

    target = dev_slot.install("agent-worktrees", preview, target_root, claimant="test-agent")

    assert (target / "src.py").read_text() == "preview = True\n"
    claim = json.loads((target / dev_slot.CLAIM_FILE).read_text())
    assert claim["claimant"] == "test-agent"
    assert claim["source_commit"] == "abc123"

    backup = dev_slot._backup_dir(target)
    assert (backup / "src.py").read_text() == "real = True\n"


def test_install_without_prior_real_payload_still_works(tmp_path: Path):
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    target_root = tmp_path / "installed"
    target = dev_slot.install("agent-worktrees", preview, target_root, claimant="test-agent")
    assert target.exists()
    assert not dev_slot._backup_dir(target).exists()


def test_install_refuses_when_backup_already_exists(tmp_path: Path):
    _fake_installed(tmp_path, "agent-worktrees")
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    target_root = tmp_path / "installed"
    dev_slot.install("agent-worktrees", preview, target_root, claimant="a")
    with pytest.raises(RuntimeError, match="run 'clean"):
        dev_slot.install("agent-worktrees", preview, target_root, claimant="b")


def test_status_reports_none_when_no_claim(tmp_path: Path):
    assert dev_slot.status("agent-worktrees", tmp_path / "installed") is None


def test_status_reports_claim_details(tmp_path: Path):
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    target_root = tmp_path / "installed"
    dev_slot.install("agent-worktrees", preview, target_root, claimant="test-agent")
    claim = dev_slot.status("agent-worktrees", target_root)
    assert claim["claimant"] == "test-agent"


def test_clean_restores_real_payload_and_removes_claim(tmp_path: Path):
    _fake_installed(tmp_path, "agent-worktrees")
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    target_root = tmp_path / "installed"
    target = dev_slot.install("agent-worktrees", preview, target_root, claimant="a")

    cleaned = dev_slot.clean("agent-worktrees", target_root)
    assert cleaned is True
    assert (target / "src.py").read_text() == "real = True\n"
    assert not (target / dev_slot.CLAIM_FILE).exists()
    assert not dev_slot._backup_dir(target).exists()


def test_clean_with_no_prior_real_payload_just_removes_override(tmp_path: Path):
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    target_root = tmp_path / "installed"
    dev_slot.install("agent-worktrees", preview, target_root, claimant="a")

    cleaned = dev_slot.clean("agent-worktrees", target_root)
    assert cleaned is True
    assert not (target_root / "agent-worktrees").exists()


def test_clean_reports_false_when_nothing_to_clean(tmp_path: Path):
    assert dev_slot.clean("agent-worktrees", tmp_path / "installed") is False


def test_full_cli_install_status_clean_round_trip(tmp_path: Path, capsys):
    _fake_installed(tmp_path, "agent-worktrees")
    preview = _fake_preview(tmp_path, "agent-worktrees", "1.5.6-dev1")
    target_root = tmp_path / "installed"

    assert dev_slot.main([
        "--target-root", str(target_root), "install", "agent-worktrees",
        "--preview-dir", str(preview), "--yes",
    ]) == 0
    capsys.readouterr()

    assert dev_slot.main(["--target-root", str(target_root), "status", "agent-worktrees"]) == 0
    assert "test-agent" not in capsys.readouterr().out  # default claimant is "local-agent"

    assert dev_slot.main(["--target-root", str(target_root), "clean", "agent-worktrees"]) == 0
    assert (target_root / "agent-worktrees" / "src.py").read_text() == "real = True\n"
