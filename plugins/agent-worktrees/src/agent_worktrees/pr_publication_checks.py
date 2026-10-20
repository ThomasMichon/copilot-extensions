"""Built-in local self-review receipts and branch scope checks; never native approvals."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import subprocess
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from . import git_ops, push_timeout
from .pr_publication_config import PublicationChecksConfig, relative_pattern


class CheckError(ValueError):
    """Missing, ambiguous or changed publication evidence."""


def _git_bytes(workspace: Path, *args: str) -> bytes:
    try:
        result = push_timeout.run_bounded(
            ["git", *args], cwd=workspace, env=git_ops.repository_identity_env(),
            timeout=30, binary=True,
        )
        if result.returncode:
            raise CheckError("publication check Git read failed: " + result.stderr.decode("utf-8", "replace"))
        return result.stdout
    except (git_ops.GitError, OSError, subprocess.SubprocessError) as exc:
        raise CheckError(f"publication check Git read failed: {exc}") from exc


def _git(workspace: Path, *args: str) -> str:
    return _git_bytes(workspace, *args).decode("utf-8", "surrogateescape")


def _commit(workspace: Path, ref: str) -> str:
    if not isinstance(ref, str) or not ref or any(c in ref for c in "\x00\r\n"):
        raise CheckError("a nonempty unambiguous commit ref is required")
    return _git(workspace, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()


def _diff(workspace: Path, base: str, head: str) -> bytes:
    diff = _git_bytes(workspace, "diff", "--binary", "--no-ext-diff", "--no-textconv",
                      "--no-renames", "--no-color", base, head, "--")
    return b"\n".join(line for line in diff.split(b"\n") if not line.startswith(b"index "))


def _hash(value: object) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _lenses(workspace: Path, config: PublicationChecksConfig) -> dict[str, str]:
    if not config.review_lenses_path or not config.self_review:
        return {}
    folder = workspace.joinpath(*config.review_lenses_path.rstrip("/").split("/"))
    if not folder.resolve().is_relative_to(workspace) or not folder.is_dir():
        raise CheckError("configured review_lenses_path is missing or escapes the workspace")
    found = {}
    for skill in sorted(folder.glob("*/SKILL.md")):
        if not skill.resolve().is_relative_to(workspace):
            raise CheckError("configured review lens escapes the workspace")
        found[skill.parent.name] = hashlib.sha256(skill.read_bytes()).hexdigest()
    if not found:
        raise CheckError("configured review_lenses_path contains no review skills")
    return found


def _snapshot(workspace: str | Path, base: str, config: PublicationChecksConfig,
              *, expected_head: str | None = None, head: str = "HEAD") -> dict:
    root = Path(workspace).resolve()
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if root != top:
        raise CheckError("publication workspace must be the exact Git worktree root")
    if _git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise CheckError("tracked uncommitted changes block publication checks")
    current = _commit(root, "HEAD")
    if expected_head and current != _commit(root, expected_head):
        raise CheckError("workspace HEAD changed before publication checks")
    candidate = _commit(root, head)
    base_commit = _commit(root, base)
    bases = _git(root, "merge-base", "--all", candidate, base_commit).splitlines()
    if len(bases) != 1:
        raise CheckError("publication base is missing or ambiguous")
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir").strip()).resolve()
    lenses = _lenses(root, config)
    policy = asdict(config)
    if not config.ssh_url:
        policy.pop("ssh_url")
    result = {
        "workspace": str(root), "git_dir": str(git_dir), "base_ref": base,
        "merge_base": bases[0], "head": candidate, "workspace_head": current,
        "policy_hash": _hash({"config": policy, "lenses": lenses}),
        "diff_hash": _hash(_diff(root, bases[0], candidate)), "lenses_available": sorted(lenses),
    }
    if _commit(root, "HEAD") != current or _commit(root, head) != candidate:
        raise CheckError("publication candidate changed while collecting checks")
    return result


def _binding(snapshot: dict) -> dict:
    return {key: snapshot[key] for key in ("workspace", "git_dir", "base_ref", "policy_hash", "diff_hash")}


def _receipt_path(snapshot: dict) -> Path:
    return Path(snapshot["git_dir"]) / "publication-checks" / "self-review" / (_hash(_binding(snapshot)) + ".json")


def record_self_review(workspace: str | Path, base: str, config: PublicationChecksConfig,
                       *, lenses: list[str], summary: str, fixed: str = "",
                       expected_head: str | None = None) -> dict:
    snapshot = _snapshot(workspace, base, config, expected_head=expected_head)
    if not config.self_review:
        raise CheckError("self_review is not enabled")
    if not isinstance(lenses, list) or len(lenses) > 100 or any(not isinstance(name, str) for name in lenses):
        raise CheckError("review lenses must be a bounded string list")
    names = [name.strip() for name in lenses]
    if not names or any(not name or len(name) > 256 for name in names) or len(set(names)) != len(names):
        raise CheckError("name a unique nonempty list of review lenses")
    if config.review_lenses_path and set(names) - set(snapshot["lenses_available"]):
        raise CheckError("record names a lens outside the configured review corpus")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 16384:
        raise CheckError("record needs a nonempty bounded review summary")
    if not isinstance(fixed, str) or len(fixed) > 16384:
        raise CheckError("record fixes must be bounded text")
    root = Path(snapshot["workspace"])
    if not _diff(root, snapshot["merge_base"], snapshot["head"]).strip():
        raise CheckError("there is no branch diff to review")
    receipt = {
        "version": 1, "binding": _binding(snapshot), "source_commit": snapshot["head"],
        "merge_base": snapshot["merge_base"], "lenses": names, "summary": summary.strip(),
        "fixed": fixed.strip(), "recorded_at": time.time(),
    }
    target = _receipt_path(snapshot)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.resolve() != target.parent:
        raise CheckError("self-review receipt path is redirected")
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
        if _snapshot(root, base, config, expected_head=snapshot["head"]) != snapshot:
            raise CheckError("publication evidence changed while recording review")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"ok": True, **snapshot, "receipt": str(target), "lenses": names,
            "progress": f"PROGRESS review={snapshot['head'][:10]} lenses={','.join(names)}"}


def _verify(snapshot: dict, config: PublicationChecksConfig) -> dict:
    if not config.self_review:
        return {"name": "self_review", "ok": True, "enabled": False}
    root = Path(snapshot["workspace"])
    if not _diff(root, snapshot["merge_base"], snapshot["head"]).strip():
        return {"name": "self_review", "ok": True, "empty_diff": True}
    path = _receipt_path(snapshot)
    try:
        if path.resolve() != path:
            raise CheckError("self-review receipt path is redirected")
        receipt = json.loads(path.read_text("utf-8"))
        if (
            not isinstance(receipt, dict) or type(receipt.get("version")) is not int or receipt["version"] != 1
            or receipt.get("binding") != _binding(snapshot)
            or not isinstance(receipt.get("lenses"), list) or not receipt["lenses"]
            or any(not isinstance(name, str) or not name for name in receipt["lenses"])
            or not isinstance(receipt.get("summary"), str) or not receipt["summary"].strip()
            or not isinstance(receipt.get("fixed"), str)
            or type(receipt.get("recorded_at")) not in (int, float)
            or not math.isfinite(receipt["recorded_at"])
        ):
            raise CheckError("self-review receipt is malformed or has a different effective binding")
        if config.review_lenses_path and set(receipt["lenses"]) - set(snapshot["lenses_available"]):
            raise CheckError("self-review receipt names an unknown lens")
        reviewed_head = _commit(root, receipt.get("source_commit"))
        reviewed_base = _commit(root, receipt.get("merge_base"))
        if _hash(_diff(root, reviewed_base, reviewed_head)) != snapshot["diff_hash"]:
            raise CheckError("self-review receipt does not bind its reviewed head/base diff")
    except (OSError, ValueError, TypeError) as exc:
        return {"name": "self_review", "ok": False, "detail": f"review the current exact diff: {exc}"}
    return {"name": "self_review", "ok": True, "receipt": str(path),
            "reviewed_head": reviewed_head, "lenses": receipt["lenses"]}


def verify_self_review(workspace: str | Path, base: str, config: PublicationChecksConfig,
                       *, expected_head: str | None = None) -> dict:
    snapshot = _snapshot(workspace, base, config, expected_head=expected_head)
    return {**snapshot, **_verify(snapshot, config)}


def _glob(path: list[str], pattern: list[str]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        return any(_glob(path[i:], pattern[1:]) for i in range(len(path) + 1))
    return bool(path) and fnmatch.fnmatchcase(path[0], pattern[0]) and _glob(path[1:], pattern[1:])


def _allowed(path: str, patterns: tuple[str, ...]) -> bool:
    return any((pattern.endswith("/") and path.startswith(pattern))
               or _glob(path.split("/"), pattern.rstrip("/").split("/")) for pattern in patterns)


def _files(root: Path, base: str, head: str) -> dict[str, tuple[int, int]]:
    result = {}
    for entry in _git(root, "diff", "--numstat", "--no-renames", "-z", base, head, "--").split("\0"):
        parts = entry.split("\t", 2)
        if len(parts) == 3:
            added, deleted, path = parts
            result[path] = (int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0)
    return result


def _file_diff(root: Path, base: str, head: str, path: str) -> bytes:
    diff = _git_bytes(root, "diff", "--no-ext-diff", "--no-textconv", "-U0", "--no-renames",
                      "--no-color", base, head, "--", path)
    lines, in_hunk = [], False
    for line in diff.split(b"\n"):
        if line.startswith(b"@@"):
            in_hunk = True
        elif in_hunk and line[:1] in (b"+", b"-"):
            lines.append(line)
    return b"\n".join(lines) if lines else (
        _git_bytes(root, "ls-tree", head, "--", path).split(b"\t")[0] if diff.strip() else b""
    )


def _scope(snapshot: dict, config: PublicationChecksConfig, *, since: str | None = None,
           allowed_paths: list[str] | None = None) -> dict:
    root, base, head = Path(snapshot["workspace"]), snapshot["merge_base"], snapshot["head"]
    files = _files(root, base, head)
    task_patterns = tuple(relative_pattern(value) for value in allowed_paths) if allowed_paths is not None else None
    rows = []
    for path, (added, deleted) in sorted(files.items()):
        history = _git(root, "log", "--reverse", "--no-renames", "--format=%H%x00%s",
                       f"{base}..{head}", "--", path).splitlines()
        commit, _, subject = (history[0] if history else "").partition("\0")
        row = {"path": path, "added": added, "deleted": deleted, "commit": commit,
               "subject": subject, "new": not bool(_git(root, "ls-tree", base, "--", path).strip())}
        restrictions = [patterns for patterns in (config.allowed_paths, task_patterns) if patterns is not None]
        if restrictions:
            row["in_manifest"] = all(_allowed(path, patterns) for patterns in restrictions)
        rows.append(row)
    result = {"name": "scope_report", "ok": True, "merge_base": base, "files": rows,
              "added": sum(row["added"] for row in rows), "deleted": sum(row["deleted"] for row in rows)}
    if config.allowed_paths is not None or task_patterns is not None:
        result["outside_manifest"] = [row["path"] for row in rows if not row["in_manifest"]]
        result["ok"] = not result["outside_manifest"]
    if since:
        previous = _commit(root, since)
        bases = _git(root, "merge-base", "--all", previous, _commit(root, snapshot["base_ref"])).splitlines()
        if len(bases) != 1:
            raise CheckError("previous scope base is ambiguous")
        old = _files(root, bases[0], previous)
        result["since"] = {
            "previous": previous[:10], "joined": sorted(set(files) - set(old)),
            "left": sorted(set(old) - set(files)),
            "changed": sorted(path for path in set(files) & set(old)
                              if _file_diff(root, bases[0], previous, path) != _file_diff(root, base, head, path)),
            "lines_then": [sum(a for a, _ in old.values()), sum(d for _, d in old.values())],
        }
    progress = f"PROGRESS scope=files:{len(rows)} lines=+{result['added']}/-{result['deleted']}"
    if "since" in result:
        old = result["since"]
        progress += f" since={old['previous']}:+{len(old['joined'])}/-{len(old['left'])}/~{len(old['changed'])}"
    if "outside_manifest" in result:
        progress += f" outside-manifest={len(result['outside_manifest'])}"
    result["progress"] = progress
    return result


def scope_report(workspace: str | Path, base: str, config: PublicationChecksConfig,
                 *, since: str | None = None, allowed_paths: list[str] | None = None) -> dict:
    snapshot = _snapshot(workspace, base, config)
    return {**snapshot, **_scope(snapshot, config, since=since, allowed_paths=allowed_paths)}


def before_publish(workspace: str | Path, base: str, config: PublicationChecksConfig | None,
                   *, expected_head: str | None = None, head: str = "HEAD") -> dict:
    if config is None:
        return {"ok": True, "configured": False, "checks": []}
    try:
        snapshot = _snapshot(workspace, base, config, expected_head=expected_head, head=head)
        checks = [_verify(snapshot, config)]
        if config.scope_report:
            checks.append(_scope(snapshot, config))
        if _snapshot(workspace, base, config, expected_head=snapshot["workspace_head"], head=head) != snapshot:
            raise CheckError("publication evidence changed during checks")
        return {**snapshot, "ok": all(check["ok"] for check in checks), "checks": checks,
                "guidance": config.guidance}
    except (CheckError, OSError) as exc:
        return {"ok": False, "checks": [], "error": str(exc)}


def enforce(workspace: str | Path, repo, *, head: str = "HEAD") -> dict:
    config = getattr(repo.pr, "publication_checks", None)
    return before_publish(workspace, f"{repo.remote}/{repo.default_branch}", config, head=head)


def failure_detail(result: dict) -> str:
    return result.get("error") or "; ".join(
        check.get("detail") or (check["name"] + ": scope outside the allowed paths")
        for check in result.get("checks", []) if not check["ok"]
    )


def create_preflight(workspace: str | Path, repo, base_result: dict) -> dict | None:
    """Shape the create operation's refusal without reaching publication side effects."""
    result = enforce(workspace, repo)
    if result["ok"]:
        return None
    return {**base_result, "error": failure_detail(result), "publication_checks": result}


def allow_publication(workspace: str | Path, repo) -> bool:
    from . import output
    result = enforce(workspace, repo)
    if not result["ok"]:
        output.err(failure_detail(result))
    return result["ok"]


def require_clean_workspace(workspace: str | Path) -> bool:
    """Preserve the direct publisher's dirty-tree diagnostic and policy."""
    from . import output
    if git_ops.is_clean(cwd=workspace):
        return True
    detail = "\n".join(f"    {line}" for line in git_ops.get_dirty_files(cwd=workspace))
    output.err("Working tree has uncommitted changes. "
               f"Commit or stash them before pushing:\n{detail}")
    return False


def push_direct_candidate(workspace: str | Path, repo, anchor: str):
    """Recheck and pin the direct publisher's post-reconciliation candidate."""
    from . import output
    result = enforce(workspace, repo, head=repo.default_branch)
    if not result["ok"]:
        output.err(failure_detail(result))
        return None
    source = f"{result['head']}:refs/heads/{repo.default_branch}" if "head" in result else repo.default_branch
    return git_ops.push(repo.remote, source, cwd=anchor)


def pin_refspec(workspace: str | Path, refspec: str, checked_head: str) -> str:
    source, separator, destination = refspec.partition(":")
    if not separator:
        destination = _git(Path(workspace), "rev-parse", "--symbolic-full-name",
                           "--verify", "--end-of-options", source).strip()
        if not destination.startswith("refs/heads/"):
            raise CheckError("checked publication requires an explicit destination for a detached source")
    return f"{checked_head}:{destination}"


def dispatch_cli(argv: list[str]) -> int:
    """Only the central facade exposes these built-ins; no external check executables."""
    import argparse

    from . import output
    from .pr_read_context import load_read_config, resolve_read_repo_config

    parser = argparse.ArgumentParser(prog="pr-watch checks")
    parser.add_argument("repo")
    parser.add_argument("operation", choices=(
        "lenses", "record-self-review", "verify-self-review", "scope-report", "export-config",
    ))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--config")
    parser.add_argument("--output", help="New worker config file for export-config only")
    parser.add_argument("--base")
    parser.add_argument("--expected-head")
    parser.add_argument("--lenses", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--fixed", default="")
    parser.add_argument("--since")
    parser.add_argument("--allowed-path", action="append", default=None)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        config = load_read_config(Path(args.config) if args.config else None)
        resolution = resolve_read_repo_config(config, args.repo, explicit=bool(args.config))
        if not resolution.resolved:
            raise CheckError(resolution.error or "repository publication binding is unavailable")
        repo = resolution.repo_config
        policy = getattr(repo.pr, "publication_checks", None)
        if policy is None:
            raise CheckError("this repository has no configured publication checks")
        if args.operation == "export-config":
            from .pr_worker_config import export_config
            if not args.output or any((
                args.expected_head, args.lenses, args.summary, args.fixed,
                args.since, args.allowed_path is not None,
            )):
                raise CheckError("export-config needs --output and no local review/scope options")
            result = export_config(args.repo, repo, args.workspace, args.base, Path(args.output))
            print(json.dumps(result))
            return 0
        if args.output:
            raise CheckError("--output applies only to export-config")
        root = Path(args.workspace).resolve()
        remotes = _git(root, "remote", "get-url", "--push", "--all", repo.remote).splitlines()
        if len(remotes) != 1:
            raise CheckError("workspace publication remote must have exactly one push URL")
        from .pr_worker_config import matches_remote
        if not matches_remote(remotes[0], args.repo, repo.pr, str(root)):
            raise CheckError("workspace remote does not match the selected repository")
        base = args.base or f"{repo.remote}/{repo.default_branch}"
        if args.operation != "scope-report" and (args.since or args.allowed_path is not None):
            raise CheckError("--since/--allowed-path apply only to scope-report")
        if args.operation == "lenses":
            snapshot = _snapshot(root, base, policy, expected_head=args.expected_head)
            result = {"ok": True, "lenses": snapshot["lenses_available"], "guidance": policy.guidance}
        elif args.operation == "record-self-review":
            result = record_self_review(
                root, base, policy, lenses=args.lenses.split(","), summary=args.summary,
                fixed=args.fixed, expected_head=args.expected_head,
            )
        elif args.operation == "verify-self-review":
            result = verify_self_review(root, base, policy, expected_head=args.expected_head)
        else:
            result = scope_report(root, base, policy, since=args.since, allowed_paths=args.allowed_path)
        if args.json:
            print(json.dumps(result))
        else:
            print(result.get("progress") or json.dumps(result))
        return 0 if result["ok"] else 1
    except (ValueError, OSError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            output.err(str(exc))
        return 2
