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
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

MANAGED_MARKER = "<!-- managed by harness-knowledge -->"


def _strip_yaml_comment(value: str) -> str:
    quote = ""
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _yaml_scalar(value: str) -> str:
    value = _strip_yaml_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def read_issue_route(config: Path) -> dict[str, str] | None:
    """Read the supported ``issues`` YAML subset without requiring PyYAML."""
    if not config.is_file():
        return None
    lines = config.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^issues\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        inline = _strip_yaml_comment(match.group(1))
        if inline:
            body = inline.strip()
            if body.startswith("{") and body.endswith("}"):
                body = body[1:-1]
            if not body.strip():
                return {}
            route = {}
            for item in body.split(","):
                if ":" not in item:
                    continue
                key, value = item.split(":", 1)
                key = key.strip()
                if key in {"provider", "repo"}:
                    route[key] = _yaml_scalar(value)
            if not route:
                raise ValueError("issues block has an unrecognized inline shape")
            return route

        children = []
        for child in lines[index + 1 :]:
            if child.strip() == "" or child.lstrip().startswith("#"):
                continue
            indent = len(child) - len(child.lstrip())
            if indent == 0:
                break
            children.append((indent, child))
        direct_indent = min((indent for indent, _ in children), default=0)
        route = {}
        for indent, child in children:
            if indent != direct_indent:
                continue
            child_match = re.match(r"^\s+(provider|repo)\s*:\s*(.*?)\s*$", child)
            if child_match:
                route[child_match.group(1)] = _yaml_scalar(child_match.group(2))
        if not children:
            return {}
        if not route:
            raise ValueError("issues block has an unrecognized nested shape")
        return route
    return None


def knowledge_origin(knowledge_path: str) -> str:
    path = Path(knowledge_path).resolve()
    if not knowledge_path or not path.is_dir():
        return ""
    try:
        root = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if root.returncode != 0 or Path(root.stdout.strip()).resolve() != path:
            return ""
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def classify_origin(remote: str) -> tuple[str, str]:
    """Return ``(provider, repo)`` without exposing credentials or raw URLs."""
    if not remote:
        return "missing", ""

    host = ""
    path = ""
    scp_match = (
        re.match(r"^[^@]+@([^:]+):(.+)$", remote)
        if "://" not in remote
        else None
    )
    if scp_match:
        host, path = scp_match.groups()
    else:
        parsed = urlparse(remote)
        host = parsed.hostname or ""
        path = parsed.path

    host = host.lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if host == "github.com":
        parts = path.split("/")
        repo = "/".join(parts[:2]) if len(parts) >= 2 else ""
        return "github", repo
    if (
        host in {"dev.azure.com", "ssh.dev.azure.com"}
        or host.endswith(".visualstudio.com")
        or host.endswith(".vs-ssh.visualstudio.com")
    ):
        return "azure-devops", ""
    return "other", ""


def inspect_issue_routing(knowledge_path: str) -> dict[str, str]:
    if not knowledge_path or not Path(knowledge_path).is_dir():
        return {
            "status": "unknown",
            "source": "",
            "provider": "",
            "repo": "",
            "origin_provider": "missing",
            "config": "",
            "reason": "knowledge checkout path is unavailable",
        }
    config = (
        Path(knowledge_path).resolve()
        / ".agent-worktrees"
        / "config.yaml"
    )
    try:
        explicit = read_issue_route(config)
    except (OSError, UnicodeError, ValueError):
        return {
            "status": "unknown",
            "source": "config",
            "provider": "",
            "repo": "",
            "origin_provider": "unknown",
            "config": str(config),
            "reason": "knowledge issue-routing config is unreadable or malformed",
        }
    origin_provider, origin_repo = classify_origin(knowledge_origin(knowledge_path))
    provider = (explicit or {}).get("provider", "github").lower()
    repo = (explicit or {}).get("repo", "")
    valid_repo = bool(re.fullmatch(r"[^/\s]+/[^/\s]+", repo))

    if explicit is not None:
        source = "config"
        if provider != "github":
            status = "unsupported"
            reason = f"configured issue provider is not supported: {provider}"
        elif repo and not valid_repo:
            status = "routing_required"
            reason = "GitHub issue repo must use owner/name form"
        elif repo or (origin_provider == "github" and origin_repo):
            status = "ready"
            reason = ""
            if not repo:
                repo = origin_repo
                source = "config+origin"
        else:
            status = "routing_required"
            reason = "GitHub issue routing requires an explicit owner/repo"
    elif origin_provider == "github" and origin_repo:
        status = "ready"
        reason = ""
        provider = "github"
        repo = origin_repo
        source = "origin"
    else:
        status = "routing_required"
        if origin_provider == "github":
            reason = "GitHub origin does not identify owner/repo"
        elif origin_provider == "missing":
            reason = "knowledge origin is missing or cannot be resolved"
        else:
            reason = "knowledge origin is not GitHub"
        provider = ""
        source = "origin"

    return {
        "status": status,
        "source": source,
        "provider": provider,
        "repo": repo,
        "origin_provider": origin_provider,
        "config": str(config),
        "reason": reason,
    }


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
        "issues": inspect_issue_routing(knowledge_path),
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
        issues = summary["issues"]
        if issues["status"] == "ready":
            print(f"Personal issues: {issues['provider']}:{issues['repo']} ({issues['source']})")
        else:
            print(f"Personal issues: {issues['status']} -- {issues['reason']}")
            if issues["status"] == "routing_required":
                print(
                    "  declare issues.provider and issues.repo from a writable "
                    "knowledge worktree"
                )
                print(f"  inspected knowledge config: {issues['config']}")
            elif issues["status"] == "unsupported":
                print(
                    "  configure provider: github with an explicit owner/repo, "
                    "or file issues manually"
                )
            elif issues["status"] == "unknown":
                print("  re-run with a readable --knowledge-path checkout")
        print("Verify binding: agent-worktrees state-root --json")
        print("Verify writable pair from a harness worktree: agent-worktrees state-root --pair --json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
