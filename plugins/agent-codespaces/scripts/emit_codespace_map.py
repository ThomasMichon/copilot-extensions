#!/usr/bin/env python3
"""emit-codespace-map -- agent-codespaces sessionStart additionalContext.

Emits a brief, persistent map of the repos that are **delegated to CodeSpaces**
so every session knows -- without being told -- which repos have no local
checkout and must be worked in a GitHub CodeSpace (via agent-codespaces /
agent-bridge) rather than edited in place.

The map is derived, never hardcoded: it reads ``agent-worktrees related list
--json`` and keeps the entries whose ``delegate`` is ``agent-codespaces`` (the
same registration ``agent-worktrees related resolve <name>`` and the
``codespace:<name>`` dispatcher consume). Each row surfaces the CodeSpaces
vessel repo, the in-CodeSpace checkout folder, and the machine, so a session can
dispatch correctly at a glance.

Output contract (Copilot CLI sessionStart hook): a single JSON object on stdout
-- ``{"additionalContext": "<markdown>"}`` when there is at least one
CodeSpace-delegated repo, else ``{}``. cwd-gated to a managed agent-worktrees
project so nothing leaks into unrelated repos. Never raises: any error degrades
to ``{}``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def _emit_empty() -> None:
    sys.stdout.write("{}")
    raise SystemExit(0)


def _aw_binstub() -> str | None:
    """Locate the ``agent-worktrees`` binstub (its own marker-resolving launcher).

    Cross-plugin calls go through the OTHER plugin's binstub, never by reaching
    into its runtime venv -- the binstub resolves agent-worktrees' own
    ``current-version`` marker (#1106). PATH first, then the conventional
    ``~/.local/bin`` install location.
    """
    exe = shutil.which("agent-worktrees")
    if exe:
        return exe
    local = os.path.join(os.path.expanduser("~"), ".local", "bin")
    for name in ("agent-worktrees.cmd", "agent-worktrees"):
        cand = os.path.join(local, name)
        if os.path.exists(cand):
            return cand
    return None


def _aw(*args: str) -> str | None:
    """Run ``agent-worktrees`` via its own binstub and return stdout."""
    exe = _aw_binstub()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _codespace_delegated(related: list[dict]) -> list[dict]:
    rows = []
    for entry in related:
        if (entry.get("delegate") or "").strip() != "agent-codespaces":
            continue
        locus = entry.get("locus") or {}
        preferred = (locus.get("preferred") or "local").strip()
        preferred_kind = preferred.split(":", 1)[0].lower()
        if preferred_kind != "codespace":
            continue
        cs = locus.get("codespace") or {}
        rows.append({
            "name": entry.get("name", "?"),
            "role": (entry.get("role") or "").strip(),
            "locus": preferred_kind,
            "delegate": (entry.get("delegate") or "").strip(),
            "summary": (entry.get("summary") or "").strip(),
            "vessel": cs.get("repo", ""),
            "workspace_folder": cs.get("workspace_folder", ""),
            "machine": cs.get("machine", ""),
        })
    return rows


def _render(rows: list[dict]) -> str:
    lines = [
        "## CodeSpace-delegated repos (agent-codespaces)",
        "",
        "These repos have **no local checkout on this machine** -- work them in a "
        "GitHub CodeSpace via `agent-codespaces` + `agent-bridge`, never by editing "
        "a local path. Dispatch with "
        "`agent-bridge send codespace:<name> \"<task>\"` (reuse a Shutdown "
        "CodeSpace; it auto-starts on connect). Full plan: "
        "`agent-worktrees related resolve <name>`.",
        "",
    ]
    for r in rows:
        bits = []
        if r["vessel"]:
            bits.append(f"vessel `{r['vessel']}`")
        if r["workspace_folder"]:
            bits.append(f"checkout `{r['workspace_folder']}`")
        if r["machine"]:
            bits.append(f"machine `{r['machine']}`")
        detail = f" -- {', '.join(bits)}" if bits else ""
        lines.append(f"- **{r['name']}**{detail}")
    return "\n".join(lines).strip()


def _render_aggregate(rows: list[dict], version: str) -> str:
    prefix = (
        f"[owner: agent-codespaces@{version}]\n"
        "CodeSpace routes (delegate=agent-codespaces): "
    )
    suffix = (
        "\nNo local checkout; resolve with "
        "`agent-worktrees related resolve <name>`."
    )
    entries = []
    for index, row in enumerate(rows):
        entry = (
            f"{row.get('name') or '?'}("
            f"role={row.get('role') or '-'},"
            f"locus={row.get('locus') or '-'})"
        )
        remaining = len(rows) - index - 1
        label = "; ".join([*entries, entry])
        if remaining:
            label += f"; +{remaining} more"
        candidate = f"{prefix}{label}.{suffix}"
        if len(_serialize_context(candidate).encode("utf-8")) > 384:
            break
        entries.append(entry)
    omitted = len(rows) - len(entries)
    label = "; ".join(entries)
    if omitted:
        label += (f"; +{omitted} more" if label else f"+{omitted} more")
    return f"{prefix}{label}.{suffix}"


def _serialize_context(context: str) -> str:
    return json.dumps(
        {"additionalContext": context},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def main() -> None:
    # cwd-gate: only emit inside a managed agent-worktrees project.
    project = (_aw("get", "project") or "").strip()
    if not project:
        _emit_empty()

    raw = _aw("related", "list", "--json")
    if not raw:
        _emit_empty()
    try:
        data = json.loads(raw)
    except ValueError:
        _emit_empty()

    rows = _codespace_delegated(data.get("related") or [])
    if not rows:
        _emit_empty()

    if "--aggregate" in sys.argv[1:]:
        manifest = Path(__file__).resolve().parents[1] / "plugin.json"
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        sys.stdout.write(_serialize_context(_render_aggregate(rows, version)))
    else:
        sys.stdout.write(_serialize_context(_render(rows)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.stdout.write("{}")
