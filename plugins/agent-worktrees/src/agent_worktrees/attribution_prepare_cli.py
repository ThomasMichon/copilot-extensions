"""Provider-neutral source metadata for externally authored pull requests."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from . import codename, config as cfg, git_ops, output, root_chain, tracking
from .providers import attribution

COMMENT_ORIGIN_PROTOCOL = "agent-worktrees.comment-origin/v1"


def resolve_comment_origin(
    source_root: Path, config: cfg.Config, *, visibility: str = "public",
) -> dict[str, object]:
    """Resolve one explicitly selected managed producer, without ambient CWD."""
    if not source_root.is_absolute():
        raise ValueError("an explicit managed producer root is required")
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("an explicit managed producer root is required")
    project = config.repo_name
    if not project:
        if len(config.repos) != 1:
            raise ValueError("selected producer requires one unambiguous configured project")
        project = next(iter(config.repos))
    if project not in config.repos:
        raise ValueError("selected producer project policy is unavailable")
    tracking_path = cfg.tracking_dir(project)
    matches = [
        record.worktree_id
        for record in tracking.list_records(
            tracking_path, repo_filter=project, copy_records=False,
        )
        if record.worktree_path and Path(record.worktree_path).resolve() == root
    ]
    if len(matches) > 1:
        raise ValueError("selected producer has ambiguous managed sources")
    record = tracking.load_record_by_id(
        matches[0], tracking_path=tracking_path,
    ) if matches else None
    if (
        record is None or record.repo != project or not record.worktree_path
        or Path(record.worktree_path).resolve(strict=True) != root
    ):
        raise ValueError("selected producer differs from its managed source")
    return prepare_comment_origin(record, config, visibility=visibility)


def add_parsers(sub) -> None:
    parser = sub.add_parser(
        "attribution-prepare", allow_abbrev=False,
        help="Prepare source metadata without provider access",
    )
    parser.add_argument("--body-file", type=Path, help="UTF-8 PR body; omit to emit only the marker")
    parser.add_argument("--tag", help="Optional public adopter tag; no tag is supplied by default")
    parser.add_argument("--omit-source", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--comment-origin", action="store_true", help="Emit compact comment-origin JSON, not a PR marker")
    parser.add_argument("--visibility", choices=("public", "internal"), help="Comment destination visibility (default: public)")
    parser.add_argument("--source-root", type=Path, help="Exact managed producer root; requires comment-origin mode and absolute --config")
    parser.add_argument("--capabilities", action="store_true", help="Discover comment-origin support without resolving source or configuration")


def prepare_comment_origin(
    record: tracking.WorktreeRecord, config: cfg.Config, *, visibility: str = "public",
) -> dict[str, object]:
    """Source-owner projection only; not a comment, native receipt or permission."""
    if visibility not in ("public", "internal"):
        raise ValueError("unsupported comment destination visibility")
    settings = config.default_repo.pr
    mode = settings.source_attribution
    result: dict[str, object] = {
        "protocol": COMMENT_ORIGIN_PROTOCOL, "privacy": "anonymous",
        "origin": None, "effects": "none",
    }
    if mode is False:
        return result
    if mode is not True and mode != "codename":
        raise ValueError("source attribution mode is not supported")
    private = visibility == "internal" and mode is True
    if (
        not record.codename or not codename.is_valid_handle(record.codename)
        or not attribution.may_publish_codename(
            codename_source=record.codename_source,
            source_attribution_configured=(
                settings.source_attribution_configured and (private or mode == "codename")
            ),
        )
    ):
        raise ValueError("comment source codename provenance is not authorized for publication")
    origin = {"codename": record.codename}
    if private:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", record.worktree_id):
            raise ValueError("comment source worktree stamp is not supported")
        origin["worktree"] = record.worktree_id
    result.update(privacy="internal" if private else "public", origin=origin)
    return result


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
        comment_origin = getattr(args, "comment_origin", False)
        visibility = getattr(args, "visibility", None)
        source_root = getattr(args, "source_root", None)
        capabilities = getattr(args, "capabilities", False)
        if comment_origin and (args.body_file or args.tag or args.omit_source):
            raise ValueError("comment origin cannot compose a PR body, tag or omission override")
        if not comment_origin and visibility is not None:
            raise ValueError("visibility requires comment-origin mode")
        if (source_root is not None or capabilities) and not comment_origin:
            raise ValueError("source-root and capabilities require comment-origin mode")
        if capabilities:
            if source_root is not None or args.config is not None or visibility is not None:
                raise ValueError("comment-origin discovery cannot select source, config or visibility")
            print(json.dumps({
                "schema": "agent-worktrees.comment-origin-capabilities", "version": 1,
                "origin_protocol": COMMENT_ORIGIN_PROTOCOL,
                "capabilities": ["explicit_source_root"], "effects": "none",
            }))
            return 0
        if source_root is not None:
            config_path = Path(args.config) if args.config else None
            if config_path is None or not config_path.is_absolute() or not config_path.is_file():
                raise ValueError("explicit comment origin requires an existing absolute config")
            settings = cfg._load_yaml_safe(config_path)
            project = settings.get("repo_name")
            if project in (None, ""):
                repositories = settings.get("repos")
                if (
                    not isinstance(repositories, dict) or len(repositories) != 1
                    or not isinstance(next(iter(repositories.values())), dict)
                ):
                    raise ValueError("explicit comment origin requires one configured project")
                project = next(iter(repositories))
            if not isinstance(project, str) or not project.strip():
                raise ValueError("explicit comment origin requires a configured project name")
            with cfg.config_acquisition.capture_config_observations() as observations:
                config = cfg.load_config(config_path, project=project)
            if any(
                finding.reason in {"unreadable-source", "invalid-shape", "invalid-yaml", "invalid-entry"}
                for observation in observations for finding in observation.findings
            ):
                raise ValueError("explicit comment origin configuration policy is unavailable")
            result = resolve_comment_origin(
                source_root, config, visibility=visibility or "public",
            )
            print(json.dumps(result))
            return 0
        config = cfg.load_config(Path(args.config) if args.config else None)
        cwd = Path.cwd()
        worktree_id = tracking.find_worktree_id_by_cwd(str(cwd), project=config.repo_name)
        record = tracking.load_record_by_id(worktree_id, tracking_path=cfg.tracking_dir(config.repo_name))
        if record is None:
            raise ValueError("run attribution-prepare inside a managed source worktree")
        if not record.worktree_path:
            raise ValueError("managed source worktree has no recorded root")
        if comment_origin:
            result = prepare_comment_origin(record, config, visibility=visibility or "public")
            print(json.dumps(result))
            return 0
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
