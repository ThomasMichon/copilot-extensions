#!/usr/bin/env python3
"""Bind a stateless harness to its knowledge repo -- the machine-local half.

This is the mechanical, idempotent core of the harness-first setup flow (see the
``binding-knowledge`` skill, which orchestrates the interactive ask + repo
creation/cloning/registration and then calls this). It writes ONLY machine-local
state, so the shareable harness tree stays generic and name-free:

  1. ``~/.<harness>/config.yaml`` -> set the top-level ``knowledge_repo: <name>``
     pointer (the seam the state-root resolver reads), preserving the rest of the
     file (comments included).
  2. Retire legacy managed knowledge-binding instruction fragments. Live
     binding, pair, and write-routing context is owned natively by
     agent-worktrees' session-conduct hook.

It never writes into the harness checkout and never touches the committed
``related.yaml`` (that would leak a repo name into the shareable tree). Pure +
idempotent; safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MANAGED_MARKER = "<!-- managed by harness-knowledge -->"


def set_top_yaml_key(text: str, key: str, value: str) -> str:
    """Replace or insert a top-level ``key: value`` line, preserving the rest.

    Line-based (not a YAML round-trip) so comments and formatting survive. If the
    key already exists at column 0, its line is replaced; otherwise the pair is
    inserted after any leading comment/blank block (so it lands near the top,
    below the file header).
    """
    line = f"{key}: {value}"
    pat = re.compile(rf"^{re.escape(key)}\s*:.*$", re.MULTILINE)
    if pat.search(text):
        return pat.sub(line, text, count=1)

    lines = text.splitlines()
    insert_at = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "" or s.startswith("#"):
            insert_at = i + 1
            continue
        break
    lines.insert(insert_at, line)
    out = "\n".join(lines)
    if text.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def bind(
    harness: str,
    knowledge: str,
    knowledge_path: str,
    *,
    home: Path,
    harness_path: str = "",
    product_repos: list[tuple[str, str]] | None = None,
    assemble_plugins: bool = True,
) -> dict:
    """Write the machine-local binding. Idempotent. Returns a summary dict."""
    del product_repos  # Retained as a compatibility argument for existing callers.
    base = Path(home) / f".{harness}"
    base.mkdir(parents=True, exist_ok=True)

    cfg = base / "config.yaml"
    existing = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    if not existing.strip():
        existing = f"# Machine-local config for {harness} (harness-knowledge managed pointer).\nrepo_name: {harness}\n"
    updated = set_top_yaml_key(existing, "knowledge_repo", knowledge)
    cfg.write_text(updated, encoding="utf-8")

    legacy_fragments = (
        base / ".github" / "instructions" / "knowledge-binding.instructions.md",
        base / "knowledge-binding.md",
    )
    for fragment in legacy_fragments:
        try:
            if (
                fragment.exists()
                and MANAGED_MARKER in fragment.read_text(encoding="utf-8")
            ):
                fragment.unlink()
        except OSError:
            pass

    summary = {
        "harness": harness,
        "knowledge_repo": knowledge,
        "knowledge_path": knowledge_path,
        "config": str(cfg),
    }

    # #955: assemble the harness's personal-plugin overlay from the knowledge
    # repo's .ai local marketplace(s), so the operator's personal skills/agents
    # load in the name-free harness. Best-effort: a missing/plugin-less knowledge
    # checkout just yields no overlay; never fails the bind.
    if assemble_plugins and harness_path and knowledge_path:
        try:
            from assemble_plugins import assemble
        except ImportError:
            import importlib.util as _ilu
            _p = Path(__file__).resolve().parent / "assemble_plugins.py"
            _spec = _ilu.spec_from_file_location("assemble_plugins", _p)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            assemble = _mod.assemble
        try:
            summary["plugins"] = assemble(harness_path, knowledge_path)
        except Exception as exc:  # noqa: BLE001 -- never fail the bind on plugin assembly
            summary["plugins_error"] = str(exc)

    return summary


def _parse_products(items: list[str] | None) -> list[tuple[str, str]]:
    out = []
    for it in items or []:
        if "=" in it:
            name, path = it.split("=", 1)
            out.append((name.strip(), path.strip()))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="bind_knowledge",
        description="Write the machine-local harness<->knowledge binding (idempotent).",
    )
    p.add_argument("--harness", required=True, help="The stateless harness repo name (e.g. citadel-harness).")
    p.add_argument("--knowledge", required=True, help="The knowledge repo name.")
    p.add_argument("--knowledge-path", default="", help="Local checkout path of the knowledge repo.")
    p.add_argument("--harness-path", default="", help="Local checkout path of the harness (for the label).")
    p.add_argument(
        "--product",
        action="append",
        default=[],
        metavar="name=path",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--home", default=str(Path.home()), help="Home dir override (testing).")
    p.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    args = p.parse_args(argv)

    summary = bind(
        args.harness, args.knowledge, args.knowledge_path,
        home=Path(args.home), harness_path=args.harness_path,
        product_repos=_parse_products(args.product),
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Bound {args.harness} -> knowledge_repo: {args.knowledge}")
        print(f"  pointer:      {summary['config']}")
        print("Next: register the knowledge repo so state-root can resolve it, e.g.")
        print(f"  agent-worktrees repos add {args.knowledge} \"{args.knowledge_path or '<path>'}\" --class worktree")
        print("Verify binding: agent-worktrees state-root --json")
        print("Verify writable pair from a harness worktree: agent-worktrees state-root --pair --json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
