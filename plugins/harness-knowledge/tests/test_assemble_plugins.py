"""Tests for the harness-knowledge assemble_plugins configurator (#955)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MOD = (Path(__file__).resolve().parents[1] / "skills" / "binding-knowledge"
        / "scripts" / "assemble_plugins.py")
_spec = importlib.util.spec_from_file_location("assemble_plugins", _MOD)
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


def _write_settings(repo: Path, data: dict, *, claude=False, local=False):
    if claude:
        d = repo / ".claude"
        name = "settings.local.json" if local else "settings.json"
    else:
        d = repo / ".github" / "copilot"
        name = "settings.local.json" if local else "settings.json"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(data), encoding="utf-8")


def _knowledge_with_ai(tmp_path: Path) -> Path:
    """A knowledge repo declaring a `.ai` directory marketplace + a remote one."""
    k = tmp_path / "knowledge"
    (k / ".ai").mkdir(parents=True)
    _write_settings(k, {
        "extraKnownMarketplaces": {
            "kn-plugins": {"source": {"source": "directory", "path": "./.ai"}},
            "dev-remote": {"source": {"source": "github", "repo": "owner/remote"}},
        },
        "enabledPlugins": {
            "connect@kn-plugins": True,
            "weekly@kn-plugins": True,
            "shared@dev-remote": True,   # remote -> must NOT be carried
            "disabled@kn-plugins": False,
        },
    })
    return k


# --- read_repo_settings -------------------------------------------------------

def test_read_native_and_claude_native_wins(tmp_path: Path):
    repo = tmp_path / "r"
    _write_settings(repo, {"enabledPlugins": {"a@m": False},
                           "extraKnownMarketplaces": {"m": {"source": {"source": "directory", "path": "./x"}}}})
    _write_settings(repo, {"enabledPlugins": {"a@m": True}}, claude=True)
    enabled, mkts = ap.read_repo_settings(repo)
    # native (a@m: False) wins over claude (a@m: True)
    assert enabled["a@m"] is False
    assert "m" in mkts


def test_read_missing_is_empty(tmp_path: Path):
    enabled, mkts = ap.read_repo_settings(tmp_path / "nope")
    assert enabled == {} and mkts == {}


# --- assemble -----------------------------------------------------------------

def test_assemble_carries_only_local_marketplace(tmp_path: Path):
    k = _knowledge_with_ai(tmp_path)
    h = tmp_path / "harness"
    h.mkdir()
    summary = ap.assemble(h, k)
    out = json.loads((h / ".github" / "copilot" / "settings.local.json").read_text())
    # Only the local `.ai` marketplace is carried, path made absolute.
    assert "kn-plugins" in out["extraKnownMarketplaces"]
    assert "dev-remote" not in out["extraKnownMarketplaces"]
    src = out["extraKnownMarketplaces"]["kn-plugins"]["source"]
    assert src["source"] == "directory"
    assert Path(src["path"]).is_absolute()
    assert src["path"].endswith("/.ai")
    # Only enabled local plugins carried; remote + disabled excluded.
    assert out["enabledPlugins"] == {
        "connect@kn-plugins": True, "weekly@kn-plugins": True
    }
    assert summary["count"] == 2
    assert summary["marketplaces"] == ["kn-plugins"]


def test_assemble_absolute_path_points_into_knowledge(tmp_path: Path):
    k = _knowledge_with_ai(tmp_path)
    h = tmp_path / "harness"
    h.mkdir()
    ap.assemble(h, k)
    out = json.loads((h / ".github" / "copilot" / "settings.local.json").read_text())
    p = out["extraKnownMarketplaces"]["kn-plugins"]["source"]["path"]
    assert Path(p) == (k / ".ai").resolve()


def test_assemble_preserves_unmanaged_and_refreshes_stale(tmp_path: Path):
    k = _knowledge_with_ai(tmp_path)
    h = tmp_path / "harness"
    out_dir = h / ".github" / "copilot"
    out_dir.mkdir(parents=True)
    # Pre-existing settings.local.json with an unmanaged marketplace + a STALE
    # managed-marketplace plugin that should be cleared on refresh.
    (out_dir / "settings.local.json").write_text(json.dumps({
        "extraKnownMarketplaces": {"user-own": {"source": {"source": "github", "repo": "me/own"}}},
        "enabledPlugins": {"mine@user-own": True, "old@kn-plugins": True},
    }), encoding="utf-8")
    ap.assemble(h, k)
    out = json.loads((out_dir / "settings.local.json").read_text())
    # Unmanaged entries preserved.
    assert out["extraKnownMarketplaces"]["user-own"]["source"]["repo"] == "me/own"
    assert out["enabledPlugins"]["mine@user-own"] is True
    # Managed marketplace refreshed; the stale old@kn-plugins is gone.
    assert "old@kn-plugins" not in out["enabledPlugins"]
    assert out["enabledPlugins"]["connect@kn-plugins"] is True


def test_assemble_idempotent(tmp_path: Path):
    k = _knowledge_with_ai(tmp_path)
    h = tmp_path / "harness"
    h.mkdir()
    ap.assemble(h, k)
    first = (h / ".github" / "copilot" / "settings.local.json").read_text()
    ap.assemble(h, k)
    second = (h / ".github" / "copilot" / "settings.local.json").read_text()
    assert first == second


def test_assemble_no_local_marketplace_is_noop_ish(tmp_path: Path):
    k = tmp_path / "knowledge"
    _write_settings(k, {
        "extraKnownMarketplaces": {"dev-remote": {"source": {"source": "github", "repo": "o/r"}}},
        "enabledPlugins": {"x@dev-remote": True},
    })
    h = tmp_path / "harness"
    h.mkdir()
    summary = ap.assemble(h, k)
    assert summary["count"] == 0
    out = json.loads((h / ".github" / "copilot" / "settings.local.json").read_text())
    # No managed marketplace/plugins written.
    assert out.get("extraKnownMarketplaces", {}) == {}
    assert out.get("enabledPlugins", {}) == {}


def test_assemble_claude_convention_source(tmp_path: Path):
    # A knowledge repo declaring its `.ai` via the Claude settings convention.
    k = tmp_path / "knowledge"
    (k / ".ai").mkdir(parents=True)
    _write_settings(k, {
        "extraKnownMarketplaces": {"kn": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"s@kn": True},
    }, claude=True)
    h = tmp_path / "harness"
    h.mkdir()
    summary = ap.assemble(h, k)
    assert summary["count"] == 1
    out = json.loads((h / ".github" / "copilot" / "settings.local.json").read_text())
    assert "kn" in out["extraKnownMarketplaces"]


# --- Paired-worktree re-assembly (#1017) --------------------------------------

def _pair_json(harness: Path, knowledge: Path, *, anchor=False):
    """A `state-root --pair --json` payload with the given role paths."""
    return {
        "paired": True,
        "pair_id": "pair-1",
        "self": {"worktree_id": "wt-h", "role": "harness", "path": str(harness)},
        "sibling": {
            "worktree_id": None if anchor else "wt-k",
            "role": "knowledge",
            "path": str(knowledge),
            "kind": "anchor" if anchor else "worktree",
            "status": None if anchor else "active",
        },
    }


def test_pair_paths_maps_roles_regardless_of_position(tmp_path: Path):
    h, k = tmp_path / "h", tmp_path / "k"
    # Even when the *self* entry is the knowledge side, roles drive the mapping.
    data = {
        "paired": True,
        "self": {"worktree_id": "wt-k", "role": "knowledge", "path": str(k)},
        "sibling": {"worktree_id": "wt-h", "role": "harness", "path": str(h)},
    }
    harness, knowledge, err = ap.pair_paths_from_resolution(data)
    assert err is None
    assert harness == str(h) and knowledge == str(k)


def test_pair_paths_unpaired(tmp_path: Path):
    harness, knowledge, err = ap.pair_paths_from_resolution(
        {"paired": False, "error": "not paired"})
    assert harness is None and knowledge is None
    assert "not paired" in err


def test_pair_paths_anchor_kind(tmp_path: Path):
    h, k = tmp_path / "h", tmp_path / "kanchor"
    harness, knowledge, err = ap.pair_paths_from_resolution(
        _pair_json(h, k, anchor=True))
    assert err is None
    assert harness == str(h) and knowledge == str(k)


def test_pair_paths_missing_role(tmp_path: Path):
    data = {"paired": True,
            "self": {"role": "harness", "path": str(tmp_path / "h")},
            "sibling": {"role": "harness", "path": str(tmp_path / "h2")}}
    harness, knowledge, err = ap.pair_paths_from_resolution(data)
    assert harness is None and knowledge is None
    assert "knowledge" in err


def test_assemble_from_pair_renders_against_worktree(tmp_path: Path):
    k = _knowledge_with_ai(tmp_path)  # knowledge worktree with a `.ai` marketplace
    h = tmp_path / "harness"
    h.mkdir()
    resolver = _fake_resolver(tmp_path, _pair_json(h, k))
    summary = ap.assemble_from_pair(cwd=h, resolver_cmd=resolver)
    assert summary.get("paired") is not False
    assert summary["pair"]["harness_path"] == str(h)
    assert summary["pair"]["knowledge_path"] == str(k)
    out = json.loads((h / ".github" / "copilot" / "settings.local.json").read_text())
    assert "kn-plugins" in out["extraKnownMarketplaces"]


def test_assemble_from_pair_unpaired_is_safe(tmp_path: Path):
    h = tmp_path / "harness"
    h.mkdir()
    resolver = _fake_resolver(tmp_path, {"paired": False, "error": "not paired"}, exit_code=3)
    summary = ap.assemble_from_pair(cwd=h, resolver_cmd=resolver)
    assert summary["paired"] is False
    assert "not paired" in summary["error"]
    # No overlay written when unpaired.
    assert not (h / ".github" / "copilot" / "settings.local.json").exists()


def test_resolve_pair_missing_binary_is_safe(tmp_path: Path):
    harness, knowledge, err = ap.resolve_pair(
        cwd=tmp_path, resolver_cmd=["definitely-not-a-real-binary-xyz"])
    assert harness is None and knowledge is None
    assert err  # a reason, not a crash


def _fake_resolver(tmp_path: Path, payload: dict, *, exit_code: int = 0):
    """Write a tiny python script that prints `payload` and exits `exit_code`,
    returned as a resolver_cmd list."""
    script = tmp_path / f"fake_resolver_{abs(hash(json.dumps(payload, sort_keys=True))) % 10000}.py"
    script.write_text(
        "import json,sys\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    import sys as _sys
    return [_sys.executable, str(script)]
