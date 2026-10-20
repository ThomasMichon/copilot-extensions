"""Provider-neutral source metadata for externally authored pull requests."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from . import codename, config as cfg, git_ops, output, root_chain, tracking
from .providers import attribution


def add_parsers(sub) -> None:
    parser = sub.add_parser("attribution-prepare", help="Prepare a source marker or PR body without provider access")
    parser.add_argument("--body-file", type=Path, help="UTF-8 PR body; omit to emit only the marker")
    parser.add_argument("--tag", help="Optional public adopter tag; no tag is supplied by default")
    parser.add_argument("--omit-source", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--config")


def prepare(body: str, record, config, *, head: str, omit: bool = False, tag: str | None = None) -> dict:
    mode = config.default_repo.pr.source_attribution
    marker = ""
    if tag is not None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", tag):
            raise ValueError("fallback tag must be a public 1-64 character label")
        if any(value and value.lower() in tag.lower() for value in (
            record.machine, record.execution_machine, record.worktree_id,
        )):
            raise ValueError("fallback tag contains a private source identifier")
    if not omit and mode is True:
        sessions = record.sessions
        live = [item for item in sessions if not item.ended_at]
        session = (live[-1] if live else sessions[-1]).session_id if sessions else ""
        marker = attribution.build_marker(
            record.worktree_id, machine=record.execution_machine, session=session, head=head,
        )
    elif not omit and mode == "codename":
        if not record.codename or not codename.is_valid_handle(record.codename):
            raise ValueError("source worktree has no valid assigned codename")
        if not attribution.may_publish_codename(
            codename_source=record.codename_source,
            source_attribution_configured=config.default_repo.pr.source_attribution_configured,
        ):
            raise ValueError("source codename provenance is not authorized for publication")
        marker = root_chain.build_codename_marker_with_root(record.codename, record, config, head)
    elif not omit and mode is not False:
        raise ValueError("source attribution mode is not supported")
    prepared = attribution.strip_marker(body)
    if marker and tag:
        line = f"Attribution: {tag}"
        if line not in prepared.splitlines():
            prepared = f"{prepared}\n\n{line}\n" if prepared else f"{line}\n"
    if marker:
        prepared = attribution.append_marker(prepared, marker)
    return {"version": 1, "marker": marker, "body": prepared, "attributed": bool(marker)}


def cmd_attribution_prepare(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        cwd = Path.cwd()
        worktree_id = tracking.find_worktree_id_by_cwd(str(cwd), project=config.repo_name)
        record = tracking.load_record_by_id(worktree_id, tracking_path=cfg.tracking_dir(config.repo_name))
        if record is None:
            raise ValueError("run attribution-prepare inside a managed source worktree")
        if not record.worktree_path:
            raise ValueError("managed source worktree has no recorded root")
        root = Path(record.worktree_path).resolve()
        body = args.body_file.read_text(encoding="utf-8") if args.body_file else ""
        head = git_ops.git(
            "rev-parse", "HEAD", cwd=str(root), check=True, isolated_repository=True, timeout=10,
        ).stdout.strip()
        result = prepare(body, record, config, head=head, omit=args.omit_source, tag=args.tag)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if args.json:
            return output._json_error(str(exc))
        output.err(str(exc))
        return 1
    print(json.dumps(result) if args.json else result["body"] if args.body_file or args.tag else result["marker"])
    return 0
