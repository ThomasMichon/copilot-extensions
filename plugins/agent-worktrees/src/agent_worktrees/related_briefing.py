"""Algorithmically generated per-related-repo operating-guide briefings.

Computes the mechanical "how to work here" skeleton for every related repo
from the resolved three-layer ``related.yaml`` aggregate (see
:func:`agent_worktrees.related.build_resolution` -- the exact same
computation ``related resolve`` already performs) and writes one bounded
markdown file per repo into the current session's session-state folder, so
an agent can read the relevant repo's briefing lazily once it determines the
current task actually touches that repo -- never preloaded unconditionally.

This is the algorithmic floor named by the harness's own
``stateless-harness/related-repo-briefing`` vision: a generated skeleton that
can never drift from the config it was computed from, because it has no
independent existence between sessions, composed with (never replacing) a
hand-authored narrative doc (``related doc <repo>``) when one exists.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from . import doctor, related, repos, sessions, state_root

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_BRIEFING_SUBDIR = ("files", "related-briefings")
_MAX_BRIEFING_BYTES = 8 * 1024


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _safe_filename(name: str) -> str | None:
    """A filesystem-safe filename stem for a related-repo name.

    Repo names are operator-declared config keys, not arbitrary external
    input, but a writer must still never trust them as a path component
    unchecked -- reject anything that could escape the briefings directory.
    """
    if not name or name in {".", ".."}:
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    return name


def render_briefing(
    entry: related.RelatedEntry,
    resolution: related.Resolution,
    *,
    doc_relpath: str | None,
    current_checkout_path: str | None = None,
) -> str:
    """Render the deterministic markdown skeleton for one related repo.

    Reuses ``build_resolution``'s already-computed ``steps``/``notes``/
    ``explore`` prose (the same sentences ``related resolve`` prints) rather
    than inventing new wording -- this generator's job is composition and
    delivery, not a new source of truth for what those sentences say.

    ``current_checkout_path``, when set, means this entry describes the
    **same repo this session is already checked out in** (cwd-derived, not a
    config fact). Resolved config can lag reality (e.g. a `locus.machines`
    list that hasn't been updated to include this machine yet) -- without
    this override a self-referential entry could flatly contradict the
    session's own observable checkout, which is worse than merely stale.
    """
    name = entry.name
    lines = [f"# {name} -- generated operating guide", ""]
    lines.append(
        "> Generated this session from the resolved `related.yaml` aggregate "
        f"(`agent-worktrees related resolve {name}`). Mechanical facts only "
        "-- never hand-edit this file; it is recomputed every session and "
        "any edit is lost."
    )
    if current_checkout_path:
        lines.append("")
        lines.append(
            "> **This is the repository this session is currently working "
            f"in** -- its checkout is at `{current_checkout_path}`. Some "
            "facts below come from resolved config that may not yet list "
            "this machine; trust your own checkout over a stale config fact "
            "and report the drift to the operator rather than treating it "
            "as unavailable."
        )
    lines.append("")
    lines.append(f"- **Role:** {entry.role or '_(unset)_'}")
    ownership = related.effective_ownership(entry)
    if ownership:
        lines.append(f"- **Ownership:** {ownership}")
    locus_desc = resolution.locus_kind
    if resolution.target_machine:
        locus_desc += f" (machine: {resolution.target_machine})"
    lines.append(f"- **Locus:** {locus_desc}")
    lines.append(f"- **Delegate:** {resolution.delegate_via or 'none'}")
    lines.append(f"- **Editing model:** {resolution.editing_model or 'unknown'}")
    available_here = resolution.available_here or bool(current_checkout_path)
    if not available_here:
        lines.append("- **Not available on this machine.**")
    if entry.summary:
        lines.append("")
        lines.append("## Why it matters")
        lines.append("")
        lines.append(entry.summary)
    lines.append("")
    lines.append("## How to make a change")
    lines.append("")
    if current_checkout_path:
        lines.append(
            f"- You are already in this repo's checkout (`{current_checkout_path}`) "
            "-- work here directly rather than provisioning another venue."
        )
    elif resolution.steps:
        for step in resolution.steps:
            lines.append(f"- {step}")
    else:
        lines.append(
            f"- Resolve the checkout with `agent-worktrees repos find {name}`."
        )
    notes = resolution.notes
    if current_checkout_path:
        # Suppress a stale "not checked out here" note that would otherwise
        # contradict the self-identification callout above.
        notes = [n for n in notes if "not checked out on" not in n.lower()]
    if notes:
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
    if resolution.explore and not current_checkout_path:
        lines.append("")
        lines.append("## Exploring / reading the code")
        lines.append("")
        for hint in resolution.explore:
            lines.append(f"- {hint}")
    lines.append("")
    lines.append("## Hand-authored narrative")
    lines.append("")
    if doc_relpath:
        lines.append(
            f"A hand-authored narrative doc exists for this repo at "
            f"`{doc_relpath}` -- read it for product-specific gotchas, "
            "conventions, and policy this generated skeleton cannot derive "
            "from config alone."
        )
    else:
        lines.append(
            f"No hand-authored narrative doc exists yet for this repo. "
            f"Scaffold one with `agent-worktrees related doc {name}` if this "
            "repo's operating guidance needs detail beyond this generated "
            "skeleton."
        )
    text = "\n".join(lines) + "\n"
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_BRIEFING_BYTES:
        text = (
            encoded[:_MAX_BRIEFING_BYTES].decode("utf-8", errors="ignore")
            + "\n...[truncated]\n"
        )
    return text


def _atomic_write(target_dir: Path, target: Path, content: str) -> bool:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target_dir,
            prefix=".related-briefing.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, target)
        return True
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        return False


def write_related_briefings(
    config: Any,
    record: Any,
    *,
    cwd: str,
    session_id: str | None,
    plugin_related_anchors: list[str] | None = None,
) -> list[str]:
    """Write one generated briefing file per related repo for this session.

    Mirrors ``session_context.render_registry_context``'s own anchor
    resolution so both read the identical resolved topology -- the generator
    has no independent source of truth. Best-effort and silent on any
    failure (missing session dir, reparse, unreadable config): this is a
    convenience addition to session start, never a gate on it, matching the
    related-repo-briefing vision's "fail toward the existing terse line,
    never toward silence or block" behavior.

    Returns the list of related-repo names a briefing was actually written
    for (used to render a pointer line), or ``[]`` on any failure.
    """
    if not session_id:
        return []
    try:
        anchors = state_root.config_source_anchors(config, cwd=cwd)
        plugin_anchors = (
            related.installed_plugin_related_anchors()
            if plugin_related_anchors is None
            else plugin_related_anchors
        )
        topology = related.read_related_grafted(
            [*plugin_anchors, *[item.anchor for item in anchors]]
        )
        current_machine = getattr(config, "machine", "") or ""
        try:
            projects = doctor._read_projects()
        except Exception:
            projects = {}
    except Exception:
        return []

    try:
        session_dir = sessions._session_state_dir() / session_id
        if not session_dir.is_dir() or _is_reparse(session_dir):
            return []
        files_dir = session_dir
        for part in _BRIEFING_SUBDIR:
            files_dir = files_dir / part
            files_dir.mkdir(exist_ok=True)
            if _is_reparse(files_dir):
                return []
    except OSError:
        return []

    written: list[str] = []
    current_repo_name = (getattr(record, "repo", "") or "").strip()
    current_checkout_path = (getattr(record, "worktree_path", "") or cwd or "").strip()
    for entry in topology.related.values():
        safe = _safe_filename(entry.name)
        if not safe:
            continue
        try:
            reg = repos.find_repo(entry.name)
            adopted = entry.name in projects
            base_repo = bool(projects.get(entry.name, {}).get("base_repo", False))
            resolution = related.build_resolution(
                entry,
                current_machine=current_machine,
                repo_class=(reg.repo_class if reg else None),
                repo_path=(reg.local_path() if reg else None),
                adopted=adopted,
                base_repo=base_repo,
            )
            is_current = bool(current_repo_name) and entry.name == current_repo_name
            doc_path = related.doc_abs_path(cwd, entry)
            doc_relpath = str(doc_path) if doc_path.is_file() else None
            content = render_briefing(
                entry, resolution, doc_relpath=doc_relpath,
                current_checkout_path=(current_checkout_path if is_current else None),
            )
            target = files_dir / f"{safe}.md"
            if _atomic_write(files_dir, target, content):
                written.append(entry.name)
        except Exception:
            continue
    return written


def pointer_line(names: list[str]) -> str:
    """One bounded sentence naming which generated briefings are available.

    Deliberately a pointer, not the content itself: the related-repo-briefing
    vision's "read lazily" behavior means an agent reads the one file
    relevant to its current task, not all of them preloaded into every
    session's context.
    """
    if not names:
        return ""
    listing = ", ".join(sorted(names))
    return (
        "Generated related-repo briefings available this session -- read "
        f"`files/related-briefings/<name>.md` for one of: {listing} (only "
        "the one(s) relevant to your current task; these are not preloaded)."
    )


def augment_session_message(
    message: str,
    config: Any,
    record: Any,
    *,
    cwd: str,
    session_id: str | None,
    plugin_related_anchors: list[str] | None = None,
) -> str:
    """Append a generated-briefings pointer to an existing session message.

    Never raises and never drops ``message`` -- a failure anywhere in
    briefing generation degrades silently to the unmodified message, matching
    the related-repo-briefing vision's "fail toward the existing terse line,
    never toward silence or block" behavior.
    """
    try:
        names = write_related_briefings(
            config, record, cwd=cwd, session_id=session_id,
            plugin_related_anchors=plugin_related_anchors,
        )
        pointer = pointer_line(names)
    except Exception:
        return message
    return f"{message}\n{pointer}" if pointer else message
