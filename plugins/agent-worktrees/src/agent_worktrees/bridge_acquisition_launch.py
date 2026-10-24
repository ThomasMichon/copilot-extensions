"""Bridge-owned CLI launch intent, validated under the worktree lifecycle lock."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import finalize, launch_admission, output, sessions
from .session_liveness import session_liveness


def intent_error(args: argparse.Namespace, raw_id: str | None) -> str | None:
    """Reject incompatible acquisition intent before resolving or preparing it."""
    if getattr(args, "require_native_admission", False) and getattr(args, "bridge_acquisition", None) in {None, "fresh"}:
        return "Native admission requires an exact existing conversation"
    if (
        getattr(args, "require_plugin_preparation", False) or getattr(args, "bridge_plugin_request", None)
    ) and not getattr(args, "bridge_acquisition", None):
        return "Plugin preparation requires typed Bridge acquisition"
    if getattr(args, "bridge_acquisition", None) and (not raw_id or getattr(args, "seed", None) or getattr(args, "copilot_args", None)):
        return "bridge acquisition requires an explicit worktree and no seed/passthrough args"
    return None


def apply_bridge_scope(env: dict[str, str], args: argparse.Namespace) -> None:
    scope = getattr(args, "bridge_scope_id", None)
    if scope:
        env["AGENT_BRIDGE_SCOPE_ID"] = scope


def add_arguments(
    parser: argparse.ArgumentParser, intent_group: argparse._ActionsContainer,
) -> None:
    intent_group.add_argument(
        "--bridge-acquisition", metavar="NATIVE-ID|fresh",
        help="Bridge-owned unseeded launch; exact native resume or explicit fresh. "
        "Refuses a live mux and does not consume pending worktree seeds.",
    )
    parser.add_argument(
        "--require-native-admission", action="store_true",
        help="Require the exact-head, owner-fenced cold-resume contract",
    )
    parser.add_argument("--require-plugin-preparation", action="store_true",
                        help="Require allocated-checkout plugin preparation before native launch")
    parser.add_argument("--bridge-plugin-request", metavar="JSON-FILE",
                        help="Bridge-owned producer plan; applied with the actual launch environment")


def prepare_plugins(args, worktree_id: str, work_dir: str, environment: dict[str, str]):
    """Use the selected launch environment without changing productive HOME."""
    raw = getattr(args, "bridge_plugin_request", None)
    if raw is None:
        if getattr(args, "require_plugin_preparation", False):
            raise ValueError("Bridge plugin preparation requires its producer request")
        return None, lambda: None
    from plugin_resolve.preparation import _git, prepare
    from plugin_resolve.registration import atomic_json
    from plugin_resolve.reflection import plan_digest
    if not getattr(args, "bridge_acquisition", None):
        raise ValueError("Plugin preparation belongs only to typed Bridge acquisition")
    env = {**os.environ, **environment}
    repo = Path(work_dir).resolve(strict=True)
    if Path(_git(repo, "rev-parse", "--show-toplevel", environment=env)).resolve() != repo:
        raise ValueError("Plugin preparation requires the actual allocated repository root")
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir", environment=env)).resolve(strict=True)
    path = Path(raw).resolve(strict=True)
    expected = git_dir / "copilot-venue-plugins" / "launch-requests"
    if path.name != "request.json" or path.parent.parent != expected:
        raise ValueError("Plugin producer request is outside the allocated checkout's operation state")
    request = json.loads(path.read_text(encoding="utf-8"))
    if (
        request.get("version") != 1 or request.get("worktree_id") != worktree_id
        or Path(request["cwd"]).resolve() != repo
        or not isinstance(request.get("plan"), dict)
        or not isinstance(request.get("deadline"), (int, float))
        or not math.isfinite(request["deadline"]) or request["deadline"] > time.time() + 240
    ):
        raise ValueError("Plugin producer request does not match the allocated checkout")
    def check_cancelled():
        if path.with_suffix(".cancel").exists() or time.time() >= request["deadline"]:
            raise InterruptedError("Bridge plugin preparation cancelled or timed out")

    executable = env.get("COPILOT_PATH") or shutil.which("copilot", path=env.get("PATH", "")) or "copilot"
    command = (executable,)
    if sys.platform == "win32" and executable.lower().endswith((".cmd", ".bat")):
        command = (env.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", executable)
    context = {}
    prepare(
        {**request["plan"], "repo": str(repo)}, environment=env, copilot_command=command,
        check_cancelled=check_cancelled, prepared_context=context,
    )
    if context.get("acp_only_sources"):
        raise ValueError(
            "Native CLI registration requires original marketplace origins; installed-only availability is ACP-only: "
            + ", ".join(context["acp_only_sources"])
        )
    check_cancelled()
    context["producer_plan_digest"] = plan_digest(request["plan"])
    atomic_json(path.with_suffix(".prepared.json"), context)
    return context, check_cancelled


def preparation_environment(environment: dict[str, str], work_dir: str) -> dict[str, str]:
    """Pin public packaging selectors instead of inheriting stale mux defaults."""
    from plugin_resolve.inventory import inventory_paths

    merged = {**os.environ, **environment}
    home, cache = inventory_paths(Path(work_dir), merged)
    keys = (
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME", "PATH", "PATHEXT", "COPILOT_PATH", "COMSPEC",
    )
    return {
        **{key: merged[key] for key in keys if key in merged}, **environment,
        "COPILOT_HOME": str(home), "COPILOT_CACHE_HOME": str(cache),
    }


def preparation_failure(worktree_id: str, work_dir: str, error: Exception) -> dict:
    """This receipt is emitted only before calling the mux creation primitive."""
    return {
        "ok": False, "plugin_preparation_version": 1, "native_launch_started": False,
        "worktree_id": worktree_id, "work_dir": work_dir, "error": str(error),
    }


@contextmanager
def launch_fence(repo, record_path: Path):
    """Order lifecycle and per-worktree native admission around real mux start."""
    from . import agent_policy

    agent_policy.require_record(record_path)
    lifecycle = finalize.FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock", timeout=300, stale_after=3600,
    )
    with output.stdout_to_stderr():
        lifecycle.acquire()
    try:
        with launch_admission.guard(record_path):
            agent_policy.require_record(record_path)
            yield
    finally:
        lifecycle.release()


def refusal(args: argparse.Namespace, record: object, worktree_id: str) -> str | None:
    intent = getattr(args, "bridge_acquisition", None)
    if getattr(args, "require_native_admission", False) and (not intent or intent == "fresh"):
        return "native admission requires an exact existing native conversation"
    if not intent:
        return None
    if getattr(record, "status", None) == "finalizing":
        return "the worktree is finalizing and cannot start a native conversation"
    if getattr(record, "pending_handoffs", ()):
        return "the native worktree head has a pending handoff"
    if getattr(record, "execution_leg_opaque", False) or getattr(record, "session_backend_opaque", False):
        return "native execution authority is opaque"
    if getattr(args, "seed", None) or getattr(args, "copilot_args", None):
        return "bridge acquisition cannot carry a seed or unchecked Copilot arguments"
    if sessions.has_mux_session(worktree_id):
        return "a live mux holds the bridge acquisition target"
    # Query the same authoritative reduction ordinary resume uses, without its
    # filesystem fallback; absence must not be filled by a random transcript.
    head = getattr(record, "resolved_head_session", None)
    if intent != "fresh" and intent != head:
        return "bridge acquisition native identity does not match the worktree head"
    if head and session_liveness(head) != "dead":
        return "cannot prove the prior native conversation has stopped"
    return None
