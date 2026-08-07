#!/usr/bin/env python3
"""Assemble the harness's personal-plugin overlay from the knowledge repo's ``.ai``.

Part of the citadel stateless-harness split (#955): a name-free harness must load
the OPERATOR's personal skills/agents, which live as ``.ai`` local-marketplace
plugins in the PRIVATE knowledge repo -- not in the shareable harness tree.
Copilot loads plugins from the LAUNCH repo's settings, so this renders a
machine-local, gitignored ``<harness>/.github/copilot/settings.local.json`` that
re-declares the knowledge repo's LOCAL (``.ai``) marketplace(s) with an ABSOLUTE
path into the knowledge checkout + the same ``enabledPlugins``. Copilot merges
``settings.local.json`` over the committed ``settings.json`` on launch (local tier
wins), so the personal plugins load while the harness tree stays name-free.

Reads BOTH the Copilot-native (``.github/copilot/settings.json``) and Claude
(``.claude/settings.json``) conventions, native preferred -- mirroring the
``plugin_resolve`` lib, but pure-stdlib (a skill script carries no deps). Only
LOCAL (``directory`` / ``local``) marketplaces are carried across: a remote
(github / git) marketplace the harness can declare itself or is globally
installed, and re-pointing it would be wrong. Idempotent and merge-safe:
unmanaged content already in ``settings.local.json`` is preserved, and stale
entries for the managed local marketplaces are refreshed to exactly mirror the
knowledge repo.

The rendered file MUST be gitignored in the harness tree (it is machine-local
and names the concrete knowledge checkout) -- keep ``.github/copilot/settings.local.json``
in the harness ``.gitignore``.

**Paired-worktree re-assembly (#1017).** By default the overlay points at the
knowledge *anchor*. In a paired ``-harness``/``-knowledge`` worktree (the citadel
#957 lifecycle) the operator's personal-plugin state lives in the paired
knowledge *worktree*; ``--from-pair`` re-renders the overlay against the pair
resolved by ``agent-worktrees state-root --pair`` -- pointing the paired harness
worktree's overlay at the paired knowledge worktree's ``.ai`` -- so a
pair-launched session loads personal plugins from the paired worktree.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Settings files in native-first order (Claude first, native last => native wins
# on a key conflict; ``.local`` overrides its base within a convention). Mirrors
# ``plugin_resolve.conventions.SETTINGS_RELS``.
_SETTINGS_RELS = (
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".github", "copilot", "settings.json"),
    (".github", "copilot", "settings.local.json"),
)

# Local (on-disk) marketplace source spellings -- a directory on this machine.
_LOCAL_SOURCE_KINDS = frozenset({"directory", "local"})


def _load_json(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_repo_settings(repo_dir: str | Path) -> tuple[dict, dict]:
    """Merge a repo's ``enabledPlugins`` + ``extraKnownMarketplaces`` (native-first).

    Returns ``(enabled, marketplaces)`` folded across both conventions with
    last-file-wins (native over Claude, ``.local`` over base). Fail-safe -> empties.
    """
    base = Path(repo_dir)
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    for rel in _SETTINGS_RELS:
        data = _load_json(base.joinpath(*rel))
        if not data:
            continue
        en = data.get("enabledPlugins")
        if isinstance(en, dict):
            for k, v in en.items():
                if isinstance(k, str):
                    enabled[k] = bool(v)
        mk = data.get("extraKnownMarketplaces")
        if isinstance(mk, dict):
            for k, v in mk.items():
                if isinstance(k, str) and isinstance(v, dict):
                    marketplaces[k] = v
    return enabled, marketplaces


def _is_local_marketplace(defn: dict) -> bool:
    src = defn.get("source") if isinstance(defn, dict) else None
    return bool(isinstance(src, dict) and src.get("source") in _LOCAL_SOURCE_KINDS)


def _abs_dir_source(defn: dict, repo_dir: Path) -> dict:
    """Return a copy of a local-marketplace def with its path made ABSOLUTE.

    A relative ``path`` (e.g. the ``.ai`` standard's ``./.ai``) is resolved
    against ``repo_dir`` -- the knowledge checkout -- and emitted with forward
    slashes so the harness resolves it regardless of the launch cwd. An already-
    absolute path is normalized as-is.
    """
    src = dict(defn.get("source") or {})
    raw = str(src.get("path") or "").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = (repo_dir / p)
    src["path"] = p.resolve().as_posix()
    out = dict(defn)
    out["source"] = src
    return out


def assemble(harness_path: str | Path, knowledge_path: str | Path) -> dict:
    """Render the harness ``settings.local.json`` personal-plugin overlay.

    Reads the knowledge repo's LOCAL (``.ai``) marketplaces + their enabled
    plugins, re-declares them with an absolute path into the knowledge checkout,
    and merges the result into ``<harness>/.github/copilot/settings.local.json``
    (preserving unmanaged entries). Idempotent. Returns a summary dict.
    """
    harness = Path(harness_path)
    knowledge = Path(knowledge_path)

    k_enabled, k_marketplaces = read_repo_settings(knowledge)
    local_names = {
        name for name, defn in k_marketplaces.items() if _is_local_marketplace(defn)
    }

    # The managed marketplaces (absolute-path rewritten) + the knowledge's
    # currently-enabled plugins that belong to them.
    managed_marketplaces = {
        name: _abs_dir_source(k_marketplaces[name], knowledge)
        for name in sorted(local_names)
    }
    managed_enabled = {
        spec: True
        for spec, on in k_enabled.items()
        if on and "@" in spec and spec.rsplit("@", 1)[1] in local_names
    }

    # Merge into any existing (unmanaged) local settings, refreshing exactly the
    # managed marketplaces + their plugin entries so a dropped plugin doesn't
    # linger.
    out_path = harness / ".github" / "copilot" / "settings.local.json"
    existing = _load_json(out_path) or {}
    ext = dict(existing.get("extraKnownMarketplaces") or {})
    en = dict(existing.get("enabledPlugins") or {})

    # Refresh the managed marketplaces.
    ext.update(managed_marketplaces)
    # Clear stale enabledPlugins for the managed marketplaces, then re-add.
    en = {
        spec: v
        for spec, v in en.items()
        if not ("@" in spec and spec.rsplit("@", 1)[1] in local_names)
    }
    en.update(managed_enabled)

    result = dict(existing)
    if ext:
        result["extraKnownMarketplaces"] = ext
    if en:
        result["enabledPlugins"] = en

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "settings_local": str(out_path),
        "marketplaces": sorted(local_names),
        "enabled_plugins": sorted(managed_enabled),
        "count": len(managed_enabled),
    }


# --- Paired-worktree re-assembly (#1017) --------------------------------------
#
# In a paired ``-harness``/``-knowledge`` worktree (the citadel #957 lifecycle),
# the operator's personal-plugin state lives in the paired KNOWLEDGE *worktree*,
# not the knowledge anchor the bind-time overlay points at. ``--from-pair``
# re-assembles the overlay against the pair resolved by
# ``agent-worktrees state-root --pair --json`` so a pair-launched harness session
# loads personal plugins from its paired knowledge worktree. Keyed entirely off
# ``state-root --pair`` -- no repo name or path is hardcoded.

# Default resolver command; overridable in tests / non-PATH contexts.
_PAIR_RESOLVER_CMD = ("agent-worktrees", "state-root", "--pair", "--json")


def pair_paths_from_resolution(data: dict) -> tuple[str | None, str | None, str | None]:
    """Map a ``state-root --pair --json`` payload to ``(harness, knowledge, error)``.

    Reads the ``self`` + ``sibling`` entries and picks paths by **role** (not by
    self/sibling position) so it works whether invoked from the harness or the
    knowledge side of the pair, and for both worktree- and anchor-kind pairs
    (an anchor sibling has ``worktree_id: null`` but a real ``path``). On success
    ``error`` is ``None``; otherwise both paths are ``None`` and ``error`` says why.
    """
    if not isinstance(data, dict):
        return None, None, "pair resolver returned a non-object payload"
    if not data.get("paired"):
        return None, None, str(data.get("error") or "current worktree is not paired")
    by_role: dict[str, str] = {}
    for entry in (data.get("self"), data.get("sibling")):
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        path = entry.get("path")
        if isinstance(role, str) and isinstance(path, str) and path:
            by_role[role] = path
    harness = by_role.get("harness")
    knowledge = by_role.get("knowledge")
    if not harness or not knowledge:
        return None, None, (
            "pair resolution did not yield both a harness and a knowledge path "
            f"(got roles: {sorted(by_role)})"
        )
    return harness, knowledge, None


def resolve_pair(
    cwd: str | Path | None = None,
    *,
    resolver_cmd: tuple[str, ...] | list[str] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Run the pair resolver from ``cwd`` and return ``(harness, knowledge, error)``.

    Fail-safe: a missing resolver binary, a non-zero exit, or unparseable output
    all yield ``(None, None, <reason>)`` -- never raises.
    """
    cmd = list(resolver_cmd or _PAIR_RESOLVER_CMD)
    # Resolve the launcher via PATH so a Windows binstub (agent-worktrees.cmd /
    # .ps1, no .exe) is found -- a bare name would fail subprocess with WinError 2.
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd[0] = resolved
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, f"could not run pair resolver ({cmd[0]}): {exc}"
    out = (proc.stdout or "").strip()
    data: dict | None = None
    if out:
        try:
            data = json.loads(out)
        except ValueError:
            data = None
    if data is None:
        # Non-zero with no JSON -> surface stderr; the resolver exits 3 when the
        # cwd is untracked/unpaired but still prints a JSON body with --json.
        reason = (proc.stderr or "").strip() or f"pair resolver exited {proc.returncode}"
        return None, None, reason
    return pair_paths_from_resolution(data)


def assemble_from_pair(
    cwd: str | Path | None = None,
    *,
    resolver_cmd: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Re-assemble the overlay against the current worktree's paired sibling.

    Resolves the pair via ``state-root --pair`` (from ``cwd``, default process
    cwd), then renders the overlay into the paired HARNESS worktree pointing at
    the paired KNOWLEDGE worktree's ``.ai``. Returns the ``assemble`` summary
    with an extra ``pair`` block, or ``{"paired": False, "error": ...}`` when the
    current worktree is not part of a resolvable pair.
    """
    harness, knowledge, error = resolve_pair(cwd, resolver_cmd=resolver_cmd)
    if error or not harness or not knowledge:
        return {"paired": False, "error": error or "pair not resolved"}
    summary = assemble(harness, knowledge)
    summary["pair"] = {"harness_path": harness, "knowledge_path": knowledge}
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="assemble_plugins",
        description=(
            "Render the harness settings.local.json personal-plugin overlay from "
            "the knowledge repo's .ai local marketplace(s) (idempotent)."
        ),
    )
    p.add_argument("--harness-path",
                   help="Local checkout path of the stateless harness (where "
                        "settings.local.json is written). Required unless "
                        "--from-pair is given.")
    p.add_argument("--knowledge-path",
                   help="Local checkout path of the knowledge repo (its .ai is "
                        "the personal-plugin source). Required unless "
                        "--from-pair is given.")
    p.add_argument("--from-pair", action="store_true",
                   help="Re-assemble against the paired -harness/-knowledge "
                        "worktree of the current directory (resolved via "
                        "'agent-worktrees state-root --pair'), pointing the "
                        "overlay at the paired KNOWLEDGE worktree's .ai. Ignores "
                        "--harness-path/--knowledge-path.")
    p.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    args = p.parse_args(argv)

    if args.from_pair:
        summary = assemble_from_pair()
        if summary.get("error"):
            if args.json:
                print(json.dumps(summary, indent=2))
            else:
                print(f"No paired overlay assembled: {summary['error']}",
                      file=sys.stderr)
            return 3
    else:
        if not args.harness_path or not args.knowledge_path:
            p.error("--harness-path and --knowledge-path are required unless "
                    "--from-pair is given")
        summary = assemble(args.harness_path, args.knowledge_path)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        n = summary["count"]
        print(f"Assembled {n} personal plugin(s) into {summary['settings_local']}")
        if summary.get("pair"):
            print(f"  from pair: harness {summary['pair']['harness_path']}")
            print(f"             knowledge {summary['pair']['knowledge_path']}")
        if summary["marketplaces"]:
            print(f"  marketplaces: {', '.join(summary['marketplaces'])}")
        for spec in summary["enabled_plugins"]:
            print(f"    + {spec}")
        print("Copilot merges settings.local.json over settings.json on launch, "
              "so these load in the harness. Keep it gitignored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
