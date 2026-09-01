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
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

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
    return sanitize_remote(result.stdout.strip()) if result.returncode == 0 else ""


def sanitize_remote(remote: str) -> str:
    """Remove HTTP(S) userinfo before registration, output, or persistence."""
    if not remote or "://" not in remote:
        return remote
    parsed = urlsplit(remote)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        return remote
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


def knowledge_default_branch(knowledge_path: str) -> str:
    path = Path(knowledge_path).resolve()
    if not knowledge_path or not path.is_dir():
        return ""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "ls-remote",
                "--symref",
                "origin",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ref:") and "HEAD" in line:
                ref = line[len("ref:"):].split("\t", 1)[0].strip()
                if ref.startswith("refs/heads/"):
                    return ref.removeprefix("refs/heads/")

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "symbolic-ref",
                "--quiet",
                "--short",
                "refs/remotes/origin/HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().removeprefix("origin/")

    for candidate in ("main", "master"):
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(path),
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"refs/remotes/origin/{candidate}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode == 0:
            return candidate
    return ""


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


def _run_agent_worktrees(
    agent_worktrees_path: str,
    args: list[str],
    *,
    cwd: str = "",
    noninteractive: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    if not agent_worktrees_path:
        return None
    command = _agent_worktrees_command(agent_worktrees_path, args)
    if not command:
        return None
    try:
        return subprocess.run(
            command,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL if noninteractive else None,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(os.path.expanduser(left))) == os.path.normcase(
        os.path.abspath(os.path.expanduser(right))
    )


def _current_platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _registration_argv(
    knowledge: str,
    knowledge_path: str,
    *,
    remote: str,
    default_branch: str,
    account: str,
) -> list[str]:
    argv = [
        "repos",
        "add",
        knowledge,
        knowledge_path,
        "--class",
        "worktree",
    ]
    if remote:
        argv.extend(["--remote", remote])
    if default_branch:
        argv.extend(["--default-branch", default_branch])
    if account:
        argv.extend(["--account", account])
    return argv


def _agent_worktrees_command(
    agent_worktrees_path: str,
    args: list[str],
) -> list[str]:
    if (
        os.name == "nt"
        and Path(agent_worktrees_path).suffix.casefold() == ".ps1"
    ):
        host = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not host:
            return []
        return [
            host,
            "-NoProfile",
            "-NoLogo",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            agent_worktrees_path,
            *args,
        ]
    return [agent_worktrees_path, *args]


def _render_command(command: list[str]) -> str:
    if os.name == "nt":
        quoted = ["'" + value.replace("'", "''") + "'" for value in command]
        return "& " + " ".join(quoted)
    return shlex.join(command)


def _inspect_github_account(
    agent_worktrees_path: str,
    remote: str,
    account: str,
) -> tuple[str, str]:
    provider, repo = classify_origin(remote)
    if provider != "github" or not repo:
        return "not_applicable", ""
    if not account:
        return "not_ready", "canonical registration has no resolved GitHub account"
    result = _run_agent_worktrees(
        agent_worktrees_path,
        ["repos", "gh", repo, "--", "api", "user", "--jq", ".login"],
    )
    if result is None:
        return "unverified", "repository-scoped GitHub account check could not run"
    login = result.stdout.strip()
    used_ambient_auth = "using ambient auth" in result.stderr.casefold()
    if (
        result.returncode == 0
        and login.casefold() == account.casefold()
        and not used_ambient_auth
    ):
        return "ready", ""
    detail = result.stderr.strip() or result.stdout.strip() or "account check failed"
    return (
        "not_ready",
        f"resolved account {account} is not usable for {repo}: {detail}",
    )


def inspect_registration(
    knowledge: str,
    knowledge_path: str,
    agent_worktrees_path: str,
    account_override: str = "",
) -> dict[str, object]:
    """Inspect canonical registration without mistaking fallback discovery for it."""
    remote = knowledge_origin(knowledge_path)
    default_branch = knowledge_default_branch(knowledge_path)
    account = ""
    base = {
        "status": "unverified",
        "canonical": False,
        "path_source": "unverified",
        "name": knowledge,
        "expected_path": str(Path(knowledge_path).resolve()) if knowledge_path else "",
        "resolved_path": "",
        "class": "",
        "remote": remote,
        "default_branch": default_branch,
        "account": "",
        "account_status": "unverified",
        "reason": "agent-worktrees command was not supplied or could not be invoked",
        "registration_argv": [],
        "registration_command": "",
    }
    listed = _run_agent_worktrees(
        agent_worktrees_path,
        ["repos", "list", "--json"],
    )
    if listed is None:
        return base
    if listed.returncode != 0:
        base["reason"] = listed.stderr.strip() or "agent-worktrees repos list failed"
        return base
    try:
        payload = json.loads(listed.stdout)
        entries = payload.get("repos", [])
    except (json.JSONDecodeError, AttributeError):
        base["reason"] = "agent-worktrees repos list returned invalid JSON"
        return base

    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict)
            and str(item.get("name", "")) == knowledge
        ),
        None,
    )
    case_collision = next(
        (
            item
            for item in entries
            if isinstance(item, dict)
            and str(item.get("name", "")).casefold() == knowledge.casefold()
        ),
        None,
    )
    if entry is None and case_collision is not None:
        collision_name = str(case_collision.get("name", "") or "")
        return {
            **base,
            "status": "mismatch",
            "path_source": "canonical_registry",
            "resolved_path": str(
                (case_collision.get("paths") or {}).get(
                    _current_platform_key(),
                    "",
                )
            ),
            "class": str(case_collision.get("class", "") or ""),
            "remote": sanitize_remote(
                str(case_collision.get("remote", "") or remote)
            ),
            "default_branch": str(
                case_collision.get("default_branch", "") or default_branch
            ),
            "account": str(
                case_collision.get("resolved_account", "")
                or case_collision.get("account", "")
                or ""
            ),
            "account_status": "unverified",
            "reason": (
                f"canonical registry name is {collision_name}; "
                f"binding name {knowledge} differs only by case"
            ),
        }
    if entry is not None:
        paths = entry.get("paths") if isinstance(entry.get("paths"), dict) else {}
        resolved_path = str(paths.get(_current_platform_key(), "") or "")
        repo_class = str(entry.get("class", "") or "")
        account = str(entry.get("resolved_account", "") or entry.get("account", "") or "")
        explicit_account = str(entry.get("account", "") or "")
        registered_remote_raw = str(entry.get("remote", "") or "")
        registered_remote = sanitize_remote(registered_remote_raw)
        registered_branch = str(entry.get("default_branch", "") or "")
        account_status, account_reason = _inspect_github_account(
            agent_worktrees_path,
            registered_remote or remote,
            account,
        )
        effective_remote = remote or registered_remote
        effective_branch = default_branch or registered_branch
        repair_argv = _registration_argv(
            knowledge,
            knowledge_path,
            remote=effective_remote,
            default_branch=effective_branch,
            account=account_override or explicit_account,
        )
        mismatches = []
        if repo_class != "worktree":
            mismatches.append(f"class is {repo_class or 'unset'}, expected worktree")
        if not _same_path(resolved_path, knowledge_path):
            mismatches.append(
                f"registered path is {resolved_path or 'unset'}, expected {knowledge_path}"
            )
        if remote and registered_remote != remote:
            mismatches.append(
                f"registered remote is {registered_remote or 'unset'}, expected {remote}"
            )
        if registered_remote_raw != registered_remote:
            mismatches.append(
                "registered remote contains HTTP(S) userinfo and must be sanitized"
            )
        if default_branch and registered_branch != default_branch:
            mismatches.append(
                f"registered default branch is {registered_branch or 'unset'}, "
                f"expected {default_branch}"
            )
        if not (registered_branch or default_branch):
            mismatches.append(
                "default branch is unset and could not be determined from the remote"
            )
        if account_status in {"not_ready", "unverified"}:
            mismatches.append(account_reason)
        if account_override and account.casefold() != account_override.casefold():
            mismatches.append(
                f"resolved account is {account or 'unset'}, expected {account_override}"
            )
        account_repairable = bool(account_override)
        if account_status in {"not_ready", "unverified"} and not account_repairable:
            mismatches.append(
                "pass --account <login> to repair the repository account mapping"
            )
            repair_argv = []
        return {
            **base,
            "status": "mismatch" if mismatches else "ready",
            "canonical": True,
            "path_source": "canonical_registry",
            "resolved_path": resolved_path,
            "class": repo_class,
            "remote": registered_remote or remote,
            "default_branch": registered_branch or default_branch,
            "account": account,
            "account_status": account_status,
            "reason": "; ".join(mismatches),
            "registration_argv": repair_argv if mismatches and repair_argv else [],
            "registration_command": (
                _render_command(
                    _agent_worktrees_command(
                        agent_worktrees_path,
                        repair_argv,
                    )
                )
                if mismatches and repair_argv
                else ""
            ),
        }

    account = ""
    fallback = _run_agent_worktrees(
        agent_worktrees_path,
        ["repos", "find", knowledge, "--json"],
    )
    resolved_path = ""
    path_source = "unresolved"
    if fallback is not None and fallback.returncode == 0:
        try:
            fallback_payload = json.loads(fallback.stdout)
            resolved_path = str(fallback_payload.get("path", "") or "")
        except (json.JSONDecodeError, AttributeError):
            resolved_path = ""
        if resolved_path:
            path_source = "fallback_discovery"
    register_argv = _registration_argv(
        knowledge,
        knowledge_path,
        remote=remote,
        default_branch=default_branch,
        account=account_override,
    )
    return {
        **base,
        "status": "missing",
        "path_source": path_source,
        "resolved_path": resolved_path,
        "account": account,
        "account_status": "unverified",
        "reason": "knowledge repo is absent from the canonical registry",
        "registration_argv": register_argv,
        "registration_command": _render_command(
            _agent_worktrees_command(
                agent_worktrees_path,
                register_argv,
            )
        ),
    }


def ensure_registration(
    knowledge: str,
    knowledge_path: str,
    agent_worktrees_path: str,
    account_override: str = "",
) -> dict[str, object]:
    registration = inspect_registration(
        knowledge,
        knowledge_path,
        agent_worktrees_path,
        account_override,
    )
    if registration["status"] == "ready":
        return registration
    if not registration.get("default_branch"):
        raise RuntimeError(
            "knowledge repo default branch is unknown; fetch origin/HEAD or "
            "register the repo with an explicit default branch"
        )
    argv = registration.get("registration_argv")
    if not agent_worktrees_path or not isinstance(argv, list) or not argv:
        raise RuntimeError(str(registration["reason"]))
    result = _run_agent_worktrees(
        agent_worktrees_path,
        argv,
        noninteractive=True,
    )
    if result is None:
        raise RuntimeError("agent-worktrees registration command could not be invoked")
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "agent-worktrees registration failed"
        )
    registration = inspect_registration(
        knowledge,
        knowledge_path,
        agent_worktrees_path,
        account_override,
    )
    if registration["status"] != "ready":
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f"; registration output: {detail}" if detail else ""
        raise RuntimeError(
            "canonical knowledge-repo registration is still not ready: "
            + str(registration["reason"])
            + suffix
        )
    return registration


def inspect_state_root(
    knowledge: str,
    knowledge_path: str,
    harness_path: str,
    agent_worktrees_path: str,
) -> dict[str, object]:
    if not harness_path or not Path(harness_path).is_dir():
        return {
            "status": "unverified",
            "bound": False,
            "repo": "",
            "state_root": "",
            "reason": "harness checkout path is unavailable",
        }
    result = _run_agent_worktrees(
        agent_worktrees_path,
        ["state-root", "--json"],
        cwd=harness_path,
    )
    if result is None:
        return {
            "status": "unverified",
            "bound": False,
            "repo": "",
            "state_root": "",
            "reason": "agent-worktrees command was not supplied or could not be invoked",
        }
    if result.returncode != 0:
        return {
            "status": "not_ready",
            "bound": False,
            "repo": "",
            "state_root": "",
            "reason": result.stderr.strip() or "agent-worktrees state-root failed",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "unverified",
            "bound": False,
            "repo": "",
            "state_root": "",
            "reason": "agent-worktrees state-root returned invalid JSON",
        }
    bound = bool(payload.get("bound"))
    repo = str(payload.get("repo", "") or "")
    state_root = str(payload.get("state_root", "") or "")
    ready = (
        bound
        and repo.casefold() == knowledge.casefold()
        and _same_path(state_root, knowledge_path)
    )
    reason = str(payload.get("error", "") or "")
    if not ready and not reason:
        reason = (
            f"state-root resolved repo={repo or 'unset'} "
            f"path={state_root or 'unset'}"
        )
    return {
        "status": "ready" if ready else "not_ready",
        "bound": bound,
        "repo": repo,
        "state_root": state_root,
        "reason": reason,
    }


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
    agent_worktrees_path: str = "",
    register: bool = False,
    account: str = "",
) -> dict:
    """Write the machine-local binding. Idempotent. Returns a summary dict."""
    del product_repos  # Retained as a compatibility argument for existing callers.
    registration = (
        ensure_registration(
            knowledge,
            knowledge_path,
            agent_worktrees_path,
            account,
        )
        if register
        else inspect_registration(
            knowledge,
            knowledge_path,
            agent_worktrees_path,
            account,
        )
    )
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
        "registration": registration,
        "state_root": inspect_state_root(
            knowledge,
            knowledge_path,
            harness_path,
            agent_worktrees_path,
        ),
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
        "--agent-worktrees-path",
        default="",
        help="Exact agent-worktrees argv[0] from its session command catalog.",
    )
    p.add_argument(
        "--register",
        action="store_true",
        help="Idempotently create or repair the canonical worktree-class registration.",
    )
    p.add_argument(
        "--account",
        default="",
        help="Writable GitHub login to persist when account mapping needs repair.",
    )
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

    try:
        summary = bind(
            args.harness, args.knowledge, args.knowledge_path,
            home=Path(args.home), harness_path=args.harness_path,
            product_repos=_parse_products(args.product),
            agent_worktrees_path=args.agent_worktrees_path,
            register=args.register,
            account=args.account,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Bound {args.harness} -> knowledge_repo: {args.knowledge}")
        print(f"  pointer:      {summary['config']}")
        registration = summary["registration"]
        print(
            "Knowledge registration: "
            f"{registration['status']} ({registration['path_source']})"
        )
        if registration["canonical"]:
            print(
                f"  class: {registration['class'] or 'unset'}; "
                f"path: {registration['resolved_path'] or 'unset'}"
            )
            print(
                f"  remote: {registration['remote'] or 'unset'}; "
                f"default branch: {registration['default_branch'] or 'unset'}; "
                f"account: {registration['account'] or 'unset'} "
                f"({registration['account_status']})"
            )
        if registration["status"] in {"missing", "mismatch"}:
            print(f"  {registration['reason']}")
            print("Next: create or repair the canonical registration:")
            print(f"  {registration['registration_command']}")
        elif registration["status"] == "unverified":
            print(f"  {registration['reason']}; registration was not assumed missing")
        state_root = summary["state_root"]
        print(
            f"State root: {state_root['status']} "
            f"(repo={state_root['repo'] or 'unset'}, "
            f"path={state_root['state_root'] or 'unset'})"
        )
        if state_root["status"] != "ready":
            print(f"  {state_root['reason']}")
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
