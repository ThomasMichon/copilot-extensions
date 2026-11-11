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
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING, TypedDict, cast
from urllib.parse import urlparse, urlsplit, urlunsplit

if TYPE_CHECKING:
    from assemble_plugins import ComposerSummary


Registration = TypedDict("Registration", {
    "status": str, "canonical": bool, "path_source": str, "name": str,
    "expected_path": str, "resolved_path": str, "class": str, "remote": str,
    "default_branch": str, "account": str, "account_status": str, "reason": str,
    "registration_argv": list[str], "registration_command": str,
})


class StateRoot(TypedDict):
    status: str
    bound: bool
    repo: str
    state_root: str
    reason: str


class _BindingFields(TypedDict):
    harness: str
    knowledge_repo: str
    knowledge_path: str
    config: str
    registration: Registration
    state_root: StateRoot
    issues: dict[str, str]


class BindSummary(_BindingFields, total=False):
    plugins: ComposerSummary
    plugins_error: str
    control_plane: dict[str, Any]
    control_plane_source: object


class _YamlModule(Protocol):
    strip_yaml_comment: Callable[[str], str]
    yaml_scalar: Callable[[str], str]
    check_existing_binding: Callable[[str, str], bool]
    read_issue_route: Callable[[Path], dict[str, str] | None]
    set_top_yaml_key: Callable[[str, str, str], str]
    write_binding: Callable[..., None]


class _GitModule(Protocol):
    origin: Callable[[str], str]
    default_branch: Callable[[str], str]


class _CommandsModule(Protocol):
    command: Callable[[str, list[str]], list[str]]
    run: Callable[..., subprocess.CompletedProcess[str] | None]
    registration_argv: Callable[..., list[str]]
    render_command: Callable[[list[str]], str]
    preflight_command: Callable[..., int]


MANAGED_MARKER = "<!-- managed by harness-knowledge -->"

_yaml_spec = importlib.util.spec_from_file_location(
    "binding_yaml", Path(__file__).with_name("binding_yaml.py")
)
if _yaml_spec is None or _yaml_spec.loader is None:
    raise ImportError("Cannot load the binding YAML module")
_binding_yaml = cast(_YamlModule, importlib.util.module_from_spec(_yaml_spec))
_yaml_spec.loader.exec_module(cast(Any, _binding_yaml))
_strip_yaml_comment = _binding_yaml.strip_yaml_comment
_yaml_scalar = _binding_yaml.yaml_scalar
_check_existing_binding = _binding_yaml.check_existing_binding
read_issue_route = _binding_yaml.read_issue_route
set_top_yaml_key = _binding_yaml.set_top_yaml_key

_git_spec = importlib.util.spec_from_file_location(
    "binding_git", Path(__file__).with_name("binding_git.py")
)
if _git_spec is None or _git_spec.loader is None:
    raise ImportError("Cannot load the binding Git module")
_binding_git = cast(_GitModule, importlib.util.module_from_spec(_git_spec))
_git_spec.loader.exec_module(cast(Any, _binding_git))

_commands_spec = importlib.util.spec_from_file_location(
    "binding_commands", Path(__file__).with_name("binding_commands.py")
)
if _commands_spec is None or _commands_spec.loader is None:
    raise ImportError("Cannot load the binding command module")
_binding_commands = cast(_CommandsModule, importlib.util.module_from_spec(_commands_spec))
_commands_spec.loader.exec_module(cast(Any, _binding_commands))


def knowledge_origin(knowledge_path: str) -> str:
    path = Path(knowledge_path).resolve()
    if not knowledge_path or not path.is_dir():
        return ""
    try:
        return sanitize_remote(_binding_git.origin(str(path)))
    except RuntimeError:
        return ""


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
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


knowledge_default_branch = _binding_git.default_branch


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
    return _binding_commands.run(
        agent_worktrees_path, args, cwd=cwd, noninteractive=noninteractive,
        reader=globals().get("_NATIVE_BINDING_READ_RUNNER"), command_builder=_agent_worktrees_command,
    )


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


_registration_argv = _binding_commands.registration_argv
_agent_worktrees_command = _binding_commands.command
_render_command = _binding_commands.render_command


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
    *, platform_key: str = "", registry: dict[str, Any] | None = None,
) -> Registration:
    """Inspect canonical registration, optionally sharing a read-only preflight inventory.

    The binding writer does not supply an inventory: it must re-read registration
    immediately before writing, even when preflight inspected it already.
    """
    remote = knowledge_origin(knowledge_path)
    default_branch = knowledge_default_branch(knowledge_path)
    account = ""
    base: Registration = {
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
    if registry is None:
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
            registry = json.loads(listed.stdout)
        except json.JSONDecodeError:
            base["reason"] = "agent-worktrees repos list returned invalid JSON"
            return base
    if not isinstance(registry, dict):
        base["reason"] = "agent-worktrees repos list returned invalid JSON"
        return base
    payload = registry
    entries = payload.get("repos", [])

    if "platform" in payload:
        native_platform = payload["platform"]
        if not isinstance(native_platform, str) or native_platform not in {
            "windows", "wsl", "linux",
        }:
            base["reason"] = "agent-worktrees repos list returned an invalid native platform"
            return base
        if platform_key and platform_key != native_platform:
            base["reason"] = "native inventory and control-plane platforms disagree"
            return base
        platform_key = native_platform
    platform_key = platform_key or _current_platform_key()

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
        raw_collision_paths = case_collision.get("paths")
        collision_paths = (
            raw_collision_paths
            if isinstance(raw_collision_paths, dict)
            else {}
        )
        return {
            **base,
            "status": "mismatch",
            "path_source": "canonical_registry",
            "resolved_path": str(
                collision_paths.get(
                    platform_key,
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
        raw_paths = entry.get("paths")
        paths = raw_paths if isinstance(raw_paths, dict) else {}
        resolved_path = str(paths.get(platform_key, "") or "")
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
            repo_class=repo_class if repo_class in {"knowledge", "worktree"} else "knowledge",
        )
        mismatches = []
        if repo_class not in {"knowledge", "worktree"}:
            mismatches.append(
                f"class is {repo_class or 'unset'}, expected knowledge or worktree"
            )
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
    *, platform_key: str = "",
) -> Registration:
    registration = inspect_registration(
        knowledge,
        knowledge_path,
        agent_worktrees_path,
        account_override,
        platform_key=platform_key,
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
        platform_key=platform_key,
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
    *,
    harness: str = "",
) -> StateRoot:
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
        (["-p", harness] if harness else []) + ["state-root", "--json"],
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
        / ".agent-worktrees"  # marketplace-isolation: allow project-owned-config
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
    preserve_binding: bool = False,
    config_root: Path | None = None,
    platform_key: str = "",
    precondition: Callable[[], None] | None = None,
    pointer_committed: Callable[[], None] | None = None,
) -> BindSummary:
    """Write the machine-local binding. Idempotent. Returns a summary dict."""
    del product_repos  # Retained as a compatibility argument for existing callers.
    registration = (
        ensure_registration(
            knowledge,
            knowledge_path,
            agent_worktrees_path,
            account,
            platform_key=platform_key,
        )
        if register
        else inspect_registration(
            knowledge,
            knowledge_path,
            agent_worktrees_path,
            account,
            platform_key=platform_key,
        )
    )
    if preserve_binding and registration["status"] != "ready":
        raise RuntimeError(
            "canonical companion registration changed before binding: "
            + str(registration["reason"])
        )
    base = config_root if config_root is not None else Path(home) / f".{harness}"
    cfg = base / "config.yaml"
    _binding_yaml.write_binding(
        cfg, harness, knowledge, preserve=preserve_binding, precondition=precondition, committed=pointer_committed,
    )

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

    summary: BindSummary = {
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
            harness=harness if preserve_binding else "",
        ),
        "issues": inspect_issue_routing(knowledge_path),
    }

    # #955: assemble the harness's personal-plugin overlay from the knowledge
    # repo's .ai marketplace(s); best-effort, never fails the bind.
    if assemble_plugins and harness_path and knowledge_path:
        try:
            from assemble_plugins import assemble
        except ImportError:
            import importlib.util as _ilu
            _p = Path(__file__).resolve().parent / "assemble_plugins.py"
            _spec = _ilu.spec_from_file_location("assemble_plugins", _p)
            if _spec is None or _spec.loader is None:
                raise ImportError("Cannot load the plugin assembler")
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            assemble = _mod.assemble
        try:
            summary["plugins"] = assemble(
                harness_path, knowledge_path,
                agent_worktrees_command=agent_worktrees_path or None)
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
    p.add_argument("--harness", default="", help="The stateless harness repo name.")
    p.add_argument("--knowledge", default="", help="The knowledge repo name.")
    p.add_argument("--capabilities", action="store_true", help="Report binding support without effects.")
    p.add_argument("--preflight", action="store_true", help="Read native repository/provider policy without binding.")
    p.add_argument("--configuration", choices=("preview", "apply", "apply-locked"), help="Confirm an existing canonical binding.")
    p.add_argument("--preview-token", default="")
    p.add_argument(
        "--control-plane",
        default="",
        help="Explicit machines.yaml carrying the registered harness/knowledge pair.",
    )
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
    p.add_argument("--home", default=None, help="Home dir override (testing).")
    p.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    args = p.parse_args(argv)
    if args.preflight:
        return _binding_commands.preflight_command(args, runner=_run_agent_worktrees, error=p.error)
    if args.capabilities:
        print(json.dumps({
            "version": 1, "registration_classes": ["knowledge", "worktree"],
            "preserves_registration_class": True, "control_plane_pair": True,
            "existing_binding_configuration": 2,
            "portable_binding_precondition": 1,
            "binding_precondition_freshness": 1,
            "repository_registration_preflight": 1,
        }))
        return 0
    if not args.control_plane and not (args.harness and args.knowledge):
        p.error("--harness and --knowledge are required without --control-plane")

    try:
        if args.configuration:
            spec = importlib.util.spec_from_file_location(
                "binding_configuration", Path(__file__).with_name("binding_configuration.py"),
            )
            if spec is None or spec.loader is None:
                raise ImportError("Cannot load the existing-binding Configuration owner")
            binding_configuration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(binding_configuration)
            return cast(int, binding_configuration.command(sys.modules[__name__], args))
        declaration = None
        if args.control_plane:
            path = Path(__file__).with_name("control_plane_binding.py")
            spec = importlib.util.spec_from_file_location("control_plane_binding", path)
            if spec is None or spec.loader is None:
                raise ImportError("Cannot load the control-plane binding module")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            declaration = module.resolve_binding(
                sys.modules[__name__],
                args.control_plane,
                args.agent_worktrees_path,
                home=Path(args.home) if args.home is not None else None,
                harness=args.harness,
                knowledge=args.knowledge,
                harness_path=args.harness_path,
                knowledge_path=args.knowledge_path,
            )
            args.harness = declaration["harness"]
            args.knowledge = declaration["knowledge"]
            args.harness_path = declaration["harness_path"]
            args.knowledge_path = declaration["knowledge_path"]
        summary = bind(
            args.harness, args.knowledge, args.knowledge_path,
            home=Path(args.home) if args.home is not None else Path.home(),
            harness_path=args.harness_path,
            product_repos=_parse_products(args.product),
            agent_worktrees_path=args.agent_worktrees_path,
            register=args.register,
            account=args.account,
            preserve_binding=declaration is not None,
            config_root=Path(declaration["config_root"]) if declaration else None,
            platform_key=declaration["platform_key"] if declaration else "",
        )
        if declaration is not None:
            summary["control_plane"] = declaration["control_plane"]
            summary["control_plane_source"] = declaration["source"]
            if (
                summary["registration"]["status"] != "ready"
                or summary["state_root"]["status"] != "ready"
                or summary.get("plugins_error")
            ):
                print(json.dumps(summary, indent=2))
                print("error: paired control-plane binding is incomplete", file=sys.stderr)
                return 1
    except RuntimeError as exc:
        if args.configuration and args.json:
            print(json.dumps({"status": "refused", "pointer_committed": False}))
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
