"""Same-cell resolution and safe-degrade contracts for compaction's tracked-
worktree lookup. Real cross-process/two-cell proof lives in dispatch's shared
``test_procutil.py`` matrix."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_logger._peer_launch import ContextRefused
from agent_logger.sync import compact


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    cell = tmp_path / "marketplaces" / "test-cell"
    root = cell / "plugins" / "agent-logger"
    root.mkdir(parents=True)
    (cell / "plugins" / "agent-worktrees").mkdir()
    own = {"cellRoot": str(cell), "pluginRoot": str(root)}
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.setattr(compact, "home_dir", lambda: root)
    monkeypatch.setattr(compact._peer_launch, "validate_owner", lambda *args: own)
    monkeypatch.setattr("shutil.which", lambda _: pytest.fail("ambient PATH selected"))
    return own


def test_lookup_uses_native_same_cell_prefix(
    owner: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = compact._peer_launch.launch_prefix(
        "agent-logger", Path(owner["pluginRoot"]),
        str(Path(owner["pluginRoot"]) / "install.json"), "agent-worktrees",
    )

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv == [*prefix, "list", "--json"]
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "worktrees": [{"path": "C:/repo/wt-a"}, {"path": "C:/repo/wt-b"}],
        }), "")

    monkeypatch.setattr(subprocess, "run", run)
    result = compact.tracked_worktree_paths()
    assert result == {
        compact._normalize_path("C:/repo/wt-a"), compact._normalize_path("C:/repo/wt-b"),
    }


def test_valid_owner_with_no_peer_is_the_sole_absence_case(
    owner: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Remove the peer directory created by the fixture: valid owner, no peer.
    (tmp_path / "marketplaces" / "test-cell" / "plugins" / "agent-worktrees").rmdir()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("peer launch attempted"),
    )
    assert compact.tracked_worktree_paths() is None


@pytest.mark.parametrize("response", [
    subprocess.CompletedProcess([], 126, "", "peer governance refused"),
    subprocess.CompletedProcess([], 1, "", "lookup failed"),
    subprocess.CompletedProcess([], 0, "", ""),
    subprocess.CompletedProcess([], 0, "{", ""),
    subprocess.CompletedProcess([], 0, "[]", ""),
    subprocess.CompletedProcess([], 0, "{}", ""),
    subprocess.CompletedProcess([], 0, json.dumps({"worktrees": "not-a-list"}), ""),
    subprocess.CompletedProcess([], 0, json.dumps({"worktrees": [{}]}), ""),
    subprocess.CompletedProcess([], 0, json.dumps({"worktrees": ["not-a-dict"]}), ""),
    subprocess.CompletedProcess([], 0, json.dumps(
        {"worktrees": [{"path": "C:/ok"}, {"path": 7}]}
    ), ""),
    subprocess.CompletedProcess([], 0, json.dumps(
        {"worktrees": [{"path": "C:/ok"}, {"path": "   "}]}
    ), ""),
])
def test_failed_or_malformed_probe_raises_context_refused_not_none(
    owner: dict[str, str], response: subprocess.CompletedProcess[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed/partial response must not be trusted as "nothing tracked"."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: response)
    with pytest.raises(ContextRefused):
        compact.tracked_worktree_paths()


@pytest.mark.parametrize("error", [OSError("cannot launch"), subprocess.TimeoutExpired("peer", 30)])
def test_probe_execution_failures_raise_context_refused(
    owner: dict[str, str], error: Exception, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ContextRefused):
        compact.tracked_worktree_paths()


def test_invalid_owner_context_raises_context_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    monkeypatch.setattr(compact._peer_launch, "validate_owner", lambda *a: (_ for _ in ()).throw(
        ValueError("bad receipt")
    ))
    with pytest.raises(ContextRefused, match="bad receipt"):
        compact.tracked_worktree_paths()


def test_legacy_ambient_path_also_rejects_malformed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without explicit context, a malformed row must not silently degrade to
    a partial/empty (falsely "authoritative") set either."""
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "agent-worktrees")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, json.dumps({"worktrees": [{"path": "ok"}, {}]}), "",
        ),
    )
    assert compact.tracked_worktree_paths() is None


def test_legacy_ambient_path_also_rejects_whitespace_only_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "agent-worktrees")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, json.dumps({"worktrees": [{"path": "ok"}, {"path": "   "}]}), "",
        ),
    )
    assert compact.tracked_worktree_paths() is None


class TestResolveTrackedPathsOrNone:
    """The select_compactable-safe resolver: an unresolved lookup -> None."""

    def test_disabled_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            compact, "tracked_worktree_paths", lambda: pytest.fail("should not be called"),
        )
        assert compact._resolve_tracked_paths_or_none(False) is None

    def test_success_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(compact, "tracked_worktree_paths", lambda: {"a"})
        assert compact._resolve_tracked_paths_or_none(True) == {"a"}

    def test_context_refused_degrades_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raiser() -> None:
            raise ContextRefused("blocked")

        monkeypatch.setattr(compact, "tracked_worktree_paths", raiser)
        assert compact._resolve_tracked_paths_or_none(True) is None


class TestResolveHubTrackedPaths:
    """The hub-safe resolver: an unresolved lookup must fail closed, not proceed."""

    def test_disabled_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            compact, "tracked_worktree_paths", lambda: pytest.fail("should not be called"),
        )
        assert compact.resolve_hub_tracked_paths(False) == (None, False)

    def test_success_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(compact, "tracked_worktree_paths", lambda: {"a"})
        assert compact.resolve_hub_tracked_paths(True) == ({"a"}, False)

    def test_context_refused_reports_unresolved_not_none_alone(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def raiser() -> None:
            raise ContextRefused("blocked")

        monkeypatch.setattr(compact, "tracked_worktree_paths", raiser)
        assert compact.resolve_hub_tracked_paths(True) == (None, True)
