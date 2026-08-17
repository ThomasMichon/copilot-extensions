"""Resolve a REMOTE target repo's OWN enabled ``.ai`` plugins to ``--plugin-dir``.

The **remote** counterpart of :func:`repo_own_plugins.repo_plugin_dir_args`
(local). agent-bridge owns transport-agnostic session logic; the venue repo
checked out on the target (e.g. ``/workspaces/example-web``) declares its own
plugins in ``.github/copilot/settings.json`` / ``.claude/settings.json`` and
ships them in a local (``directory``) marketplace such as ``.ai``. Since
``copilot --acp`` ignores ``enabledPlugins`` and only surfaces plugin skills via
``--plugin-dir``, those in-repo plugins never load for a dispatched agent unless
we point ``--plugin-dir`` at each resolved dir.

The payloads already live on the target, so there is nothing to stage -- only to
*resolve*: ship the canonical, vendored ``plugin_resolve`` package to the target
and run it there against the repo dir (identical logic to the local
``repo_plugin_dir_args``), over the transport-exec seam (:mod:`target_exec`). No
duplicated logic, identical results across venues.

Moved here from agent-codespaces (PR2, dotfiles#1422): agent-bridge is the
session brain; agent-codespaces is the transport. The old
``agent-codespaces resolve-ai-plugin-dirs`` CLI seam is retired in favour of this.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import shlex
import tarfile
from pathlib import Path

from . import target_exec as tx

log = logging.getLogger("agent-bridge")

# Marker the remote driver prefixes its JSON line with, so we can extract the
# result from any surrounding login-shell / hook noise on the channel.
RESULT_MARKER = "AI_PLUGIN_RESOLVE_JSON:"


def find_plugin_resolve_pkg() -> Path | None:
    """Locate the installed ``plugin_resolve`` package dir, or ``None``.

    Resolved via import so it tracks whatever the runtime actually loads (the
    vendored copy installed into this venv), not a hard-coded source path.
    """
    try:
        import plugin_resolve  # noqa: PLC0415

        f = getattr(plugin_resolve, "__file__", None)
        if not f:
            return None
        pkg = Path(f).parent
        return pkg if (pkg / "__init__.py").is_file() else None
    except Exception:  # pragma: no cover - defensive
        return None


def tar_pkg_b64(pkg_dir: Path) -> str:
    """Tar+gzip the ``plugin_resolve`` package (arcname ``plugin_resolve``) -> base64."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(str(pkg_dir), arcname="plugin_resolve")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Remote python driver: import the shipped canonical resolver and emit the
# resolved local-marketplace plugin dirs (+ unresolved sources) as one JSON line.
# argv: [1]=extract dir on sys.path, [2]=repo checkout dir.
_DRIVER = (
    "import sys,json;"
    "sys.path.insert(0,sys.argv[1]);"
    "from plugin_resolve import resolve_repo_plugins as r;"
    "x=r(sys.argv[2]);"
    "print('" + RESULT_MARKER + "'+json.dumps("
    "{'resolved':{k:str(v) for k,v in x.resolved.items()},"
    "'unresolved':list(x.unresolved)}))"
)


def build_resolve_command(pkg_tar_b64: str, repo_dir: str) -> str:
    """Bash to ship + run the resolver on the target against ``repo_dir``.

    Extracts the shipped ``plugin_resolve`` package into a temp dir, runs the
    driver against ``repo_dir``, and cleans up. Prefers ``python3`` then
    ``python``. Never aborts the channel: on any failure it emits an empty result
    line so the caller degrades to "no repo-own plugins" rather than erroring.
    """
    repo_q = shlex.quote(repo_dir)
    driver_q = shlex.quote(_DRIVER)
    empty = f'{RESULT_MARKER}{{"resolved":{{}},"unresolved":[]}}'
    return (
        'D=$(mktemp -d) || { echo ' + shlex.quote(empty) + '; exit 0; }; '
        f'printf %s {pkg_tar_b64} | base64 -d | tar -xzf - -C "$D" 2>/dev/null; '
        'PY=$(command -v python3 || command -v python); '
        f'if [ -n "$PY" ]; then "$PY" -c {driver_q} "$D" {repo_q} '
        f'2>/dev/null || echo {shlex.quote(empty)}; '
        f'else echo {shlex.quote(empty)}; fi; '
        'rm -rf "$D" 2>/dev/null || true'
    )


def parse_resolve_result(output: str) -> tuple[list[str], list[str]]:
    """Parse the driver's marker line -> ``(resolved_dirs, unresolved_sources)``.

    Scans for the last ``RESULT_MARKER`` line (ignoring login-shell noise). The
    resolved dirs are absolute paths on the target, ready to pass as
    ``--plugin-dir``. Fail-safe -> ``([], [])``.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    for line in (output or "").splitlines():
        idx = line.find(RESULT_MARKER)
        if idx < 0:
            continue
        payload = line[idx + len(RESULT_MARKER):].strip()
        try:
            data = json.loads(payload)
        except Exception:
            continue
        rv = data.get("resolved")
        if isinstance(rv, dict):
            resolved = [str(v) for v in rv.values() if v]
        uv = data.get("unresolved")
        if isinstance(uv, list):
            unresolved = [str(s) for s in uv if s]
    return resolved, unresolved


def resolve_remote_repo_ai_plugin_dirs(
    session: dict, repo_dir: str, *, timeout: float = 90.0,
) -> tuple[list[str], list[str]]:
    """Resolve ``repo_dir``'s own enabled ``.ai`` plugins on ``session``'s target.

    Ships ``plugin_resolve`` to the target and runs it there via the
    transport-exec seam. Returns ``(resolved_dirs, unresolved_sources)``.
    Best-effort: any failure (no vendored resolver, no python on target,
    transport error, malformed output) yields ``([], [])`` so the dispatch
    proceeds without repo-own plugins -- never blocking the connect.
    """
    if not repo_dir:
        return [], []
    pkg = find_plugin_resolve_pkg()
    if pkg is None:
        log.debug("repo-own .ai resolve skipped: plugin_resolve package not found")
        return [], []
    try:
        command = build_resolve_command(tar_pkg_b64(pkg), repo_dir)
        output = tx.exec_bash_on_target(session, command, timeout=timeout)
    except tx.TargetExecError as exc:
        log.debug("repo-own .ai resolve transport failed: %s", exc)
        return [], []
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("repo-own .ai resolve failed: %s", exc)
        return [], []
    return parse_resolve_result(output)
