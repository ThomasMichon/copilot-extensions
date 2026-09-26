"""Unit coverage for agent-worktrees' same-cell peer-launch adapter."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import peer_launch_adapter as pla


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    cell = tmp_path / "marketplaces" / "test-cell"
    root = cell / "plugins" / "agent-worktrees"
    root.mkdir(parents=True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.delenv("AGENT_WORKTREES_HOME", raising=False)
    return {"cell": cell, "root": root}


def test_explicit_context_treats_whitespace_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    assert pla.explicit_context() is False
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "   ")
    assert pla.explicit_context() is True


def test_validate_context_uses_receipt_parent_by_default(
    owner: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_validate_owner(name: str, root: Path, context: str) -> dict[str, str]:
        captured["name"] = name
        captured["root"] = root
        captured["context"] = context
        return {"cellRoot": str(owner["cell"]), "pluginRoot": str(owner["root"])}

    monkeypatch.setattr(pla, "validate_owner", fake_validate_owner)
    result = pla.validate_context()
    assert result == {"cellRoot": str(owner["cell"]), "pluginRoot": str(owner["root"])}
    assert captured == {
        "name": "agent-worktrees",
        "root": owner["root"],
        "context": str(owner["root"] / "install.json"),
    }


def test_validate_context_treats_windows_n_drive_receipt_as_a_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_validate_owner(name: str, root: Path, context: str) -> dict[str, str]:
        captured["name"] = name
        captured["root"] = root
        captured["context"] = context
        return {"cellRoot": "N:\\marketplaces\\cell", "pluginRoot": str(root)}

    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        r"N:\marketplaces\cell\plugins\agent-worktrees\install.json",
    )
    monkeypatch.setattr(pla, "validate_owner", fake_validate_owner)
    result = pla.validate_context()
    assert result == {
        "cellRoot": r"N:\marketplaces\cell",
        "pluginRoot": r"N:\marketplaces\cell\plugins\agent-worktrees",
    }
    assert captured == {
        "name": "agent-worktrees",
        "root": Path(r"N:\marketplaces\cell\plugins\agent-worktrees"),
        "context": r"N:\marketplaces\cell\plugins\agent-worktrees\install.json",
    }


def test_validate_context_ignores_legacy_agent_worktrees_home_override(
    owner: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    override = tmp_path / "override-root"
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(override))
    captured: dict[str, object] = {}

    def fake_validate_owner(name: str, root: Path, context: str) -> dict[str, str]:
        captured["name"] = name
        captured["root"] = root
        return {"cellRoot": str(owner["cell"]), "pluginRoot": str(owner["root"])}

    monkeypatch.setattr(pla, "validate_owner", fake_validate_owner)
    pla.validate_context()
    assert captured == {"name": "agent-worktrees", "root": owner["root"]}


def test_validate_context_wraps_refusals(owner: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", json.dumps({"installReceipt": str(owner["root"] / "install.json")}))
    monkeypatch.setattr(
        pla,
        "validate_owner",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad receipt")),
    )
    with pytest.raises(pla.ContextRefused, match="bad receipt"):
        pla.validate_context()


@pytest.mark.parametrize("raw", ["[]", "null"])
def test_validate_context_rejects_non_object_inline_json(
    raw: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", raw)
    monkeypatch.delenv("AGENT_WORKTREES_HOME", raising=False)
    with pytest.raises(pla.ContextRefused, match="JSON object"):
        pla.validate_context()


def test_run_degrades_when_peer_is_absent(
    owner: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pla,
        "validate_context",
        lambda: {"cellRoot": str(owner["cell"]), "pluginRoot": str(owner["root"])},
    )
    assert pla.run("agent-dispatch", "worktree-status", timeout=15) is None


def test_run_preserves_cwd_and_callback_args(
    owner: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    (owner["cell"] / "plugins" / "agent-dispatch").mkdir(parents=True)
    monkeypatch.setattr(
        pla,
        "validate_context",
        lambda: {"cellRoot": str(owner["cell"]), "pluginRoot": str(owner["root"])},
    )
    monkeypatch.setattr(
        pla,
        "launch_prefix",
        lambda *args: [
            sys.executable,
            "-c",
            (
                "import json, pathlib, sys; "
                "print(json.dumps({'cwd': str(pathlib.Path.cwd()), 'argv': sys.argv[1:]}))"
            ),
        ],
    )
    cwd = tmp_path / "peer-cwd"
    cwd.mkdir()
    result = pla.run("agent-dispatch", "worktree-status", "--machine", "book2", timeout=15, cwd=str(cwd))
    observed = json.loads(result.stdout)
    assert observed == {
        "cwd": str(cwd),
        "argv": ["worktree-status", "--machine", "book2"],
    }


def test_run_raises_context_refused_on_boundary_exit_126(
    owner: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    (owner["cell"] / "plugins" / "agent-dispatch").mkdir(parents=True)
    monkeypatch.setattr(
        pla,
        "validate_context",
        lambda: {"cellRoot": str(owner["cell"]), "pluginRoot": str(owner["root"])},
    )
    monkeypatch.setattr(pla, "launch_prefix", lambda *args: ["peer-launch"])
    monkeypatch.setattr(
        pla.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 126, "", "peer launch refused"),
    )
    with pytest.raises(pla.ContextRefused, match="peer launch refused"):
        pla.run("agent-dispatch", "worktree-status", timeout=15)
