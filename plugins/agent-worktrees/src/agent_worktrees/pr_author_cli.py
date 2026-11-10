"""Author-side commands; repository policy and provider effects remain central."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from . import config as cfg, output, pr_config, pr_publication_labels_cli
from .providers.base import ProviderError

SCHEMA = "agent-worktrees.pr-author-capabilities"


def load_author_config(path, repository: str, *, claimant: bool = False):
    if path is None:
        return cfg.load_config()
    from .pr_read_context import read_explicit_config

    source = Path(path)
    raw = read_explicit_config(source)
    if "pr-worker" in raw.get("repos", {}) and raw.get("repo_name") != "pr-worker":
        raise ValueError("Explicit worker policy must identify its pr-worker repository")
    if raw.get("repo_name") != "pr-worker":
        return cfg.load_config(source)
    worker = raw.get("repos", {}).get("pr-worker")
    if not isinstance(worker, dict):
        raise ValueError("Explicit worker policy must declare its pr-worker repository")
    policy = cfg._parse_pr(worker.get("pr"))
    if policy.repository != repository:
        raise ValueError("Explicit worker policy addresses another PR target")
    config = cfg.load_config(
        source, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    target = replace(config.default_repo, pr=policy)
    if claimant:
        config = cfg.load_config()
    return replace(config, explicit_pr_target=target)


def add_parsers(sub):
    for name in ("pr-author-capabilities", "pr-update", "pr-push", "pr-author-ready",
                 "pr-author-nudge", "pr-thread-resolve", "pr-thread-reopen"):
        parser = sub.add_parser(name, help={
            "pr-author-capabilities": "Report target-qualified author operations without mutation",
            "pr-update": "Repair existing PR metadata without creating or pushing",
            "pr-push": "Publish an admitted delegated source without managed branch renaming",
            "pr-author-ready": "Move an explicit native draft PR to ready without consent or hold release",
            "pr-author-nudge": "Request the explicit target's configured automated reviewer without a comment",
            "pr-thread-resolve": "Close one native ADO discussion with exact candidate/thread expectations",
            "pr-thread-reopen": "Reopen one native ADO discussion with exact candidate/thread expectations",
        }[name], allow_abbrev=False)
        parser.add_argument("--repo", required=True)
        parser.add_argument("--config")
        parser.add_argument("--json", action="store_true")
        if name == "pr-author-capabilities":
            continue
        if name in ("pr-thread-resolve", "pr-thread-reopen"):
            parser.add_argument("--number", required=True, type=int)
            parser.add_argument("--thread", required=True, type=int)
            parser.add_argument("--expected-candidate", required=True)
            parser.add_argument("--expected-thread", required=True)
            parser.add_argument("--operation-id", required=True)
            continue
        if name in ("pr-author-ready", "pr-author-nudge"):
            parser.add_argument("--number", required=True, type=int)
            continue
        pr_publication_labels_cli.add_options(parser)
        if name == "pr-update":
            parser.add_argument("--number", required=True, type=int)
            parser.add_argument("--title")
            body = parser.add_mutually_exclusive_group()
            body.add_argument("--body")
            body.add_argument("--body-file")
            parser.add_argument("--reviewer", action="append", default=[])
            parser.add_argument("--work-item", action="append", default=[])
        else:
            parser.add_argument("--workspace", required=True)
            parser.add_argument("--branch", required=True)
            parser.add_argument("--expected-head", required=True)
            parser.add_argument("--expected-remote-head",
                                help="Optional exact old remote tip; empty means first push")


def capabilities(config, repository: str) -> dict:
    resolution = pr_config.resolve_repo_config_for_slug(config, repository)
    if not resolution.resolved:
        raise ValueError("Author target needs a registered repository PR binding")
    pr = resolution.repo_config.pr
    supported = pr.enabled and pr.provider == "azure-devops"
    return {
        "schema": SCHEMA, "version": 1, "repo": repository, "provider": pr.provider,
        "operations": {
            "qualified-create": supported, "metadata-update": supported,
            "source-push": supported, "remote-evidence": False,
            "worker-policy-config": pr.enabled and pr.provider in ("azure-devops", "github"),
            "worker-policy-export": True,
            "draft-ready": supported,
            "reviewer-request": pr.enabled and pr.provider == "github",
            "thread-resolve": supported,
            "thread-reopen": supported,
        },
        "source_adapter": "reachable-workspace" if supported else None,
        "completion_authority": "target-repository-policy",
    }


def cmd_capabilities(args):
    try:
        config = load_author_config(args.config, args.repo)
        result = capabilities(config, args.repo)
        output._json_output(result) if args.json else print(result)
        return 0
    except (ValueError, OSError, ProviderError) as exc:
        return output._json_error(str(exc)) if args.json else (output.err(str(exc)) or 1)


def cmd_ready(args: argparse.Namespace) -> int:
    """Keep admission failure distinct from an attempted provider transition."""
    from . import pr_cli, pr_readiness

    try:
        _, error = pr_cli.require_claimant_worktree("pr-author-ready")
        if error:
            raise ValueError(error)
        config = load_author_config(args.config, args.repo, claimant=True)
        result = pr_readiness.ready(config, args.repo, args.number)
    except (ValueError, OSError, ProviderError) as exc:
        result = pr_readiness.outcome(args.repo, args.number, error=str(exc))
    output._json_output(result) if args.json else print(result)
    return 0 if result["ok"] else 1


def readiness_project_unavailable(argv: list[str], message: str) -> int:
    """Preserve strict readiness syntax and its pre-dispatch failure envelope."""
    from . import pr_readiness

    parser = argparse.ArgumentParser(allow_abbrev=False)
    add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    if args.command in ("pr-thread-resolve", "pr-thread-reopen"):
        from .pr_thread_resolution import outcome
        result = outcome(args.repo, args.number, args.thread, args.operation_id,
                         args.expected_candidate, args.expected_thread,
                         reason="project-unavailable", error=message,
                         reopening=args.command == "pr-thread-reopen")
    elif args.command == "pr-author-nudge":
        from . import pr_reviewer_request
        result = pr_reviewer_request.outcome(args.repo, args.number, error=message)
    else:
        result = pr_readiness.outcome(args.repo, args.number, error=message)
    output._json_output(result) if args.json else print(result)
    return 1


def cmd_thread_resolve(args: argparse.Namespace) -> int:
    """Keep claimant/policy failure separate from one selected provider effect."""
    return _cmd_thread_status(args, reopening=False)


def cmd_thread_reopen(args: argparse.Namespace) -> int:
    """Reopen one selected discussion without reinterpreting close-only commands."""
    return _cmd_thread_status(args, reopening=True)


def _cmd_thread_status(args: argparse.Namespace, *, reopening: bool) -> int:
    from . import pr_cli, pr_thread_resolution

    try:
        _, error = pr_cli.require_claimant_worktree("pr-thread-reopen" if reopening else "pr-thread-resolve")
        if error:
            raise ValueError(error)
        config = load_author_config(args.config, args.repo, claimant=True)
        result = pr_thread_resolution.resolve(
            config, args.repo, args.number, args.thread, operation_id=args.operation_id,
            expected_candidate=args.expected_candidate, expected_thread=args.expected_thread,
            reopening=reopening,
        )
    except (ValueError, OSError, ProviderError) as exc:
        result = pr_thread_resolution.outcome(
            args.repo, args.number, args.thread, args.operation_id,
            args.expected_candidate, args.expected_thread, error=str(exc), reopening=reopening,
        )
    output._json_output(result) if args.json else print(result)
    return 0 if result["ok"] else 1


def cmd_nudge(args: argparse.Namespace) -> int:
    """Preserve pre-dispatch refusal separately from accepted or uncertain requests."""
    from . import pr_cli, pr_reviewer_request

    try:
        _, error = pr_cli.require_claimant_worktree("pr-author-nudge")
        if error:
            raise ValueError(error)
        config = load_author_config(args.config, args.repo, claimant=True)
        result = pr_reviewer_request.nudge(config, args.repo, args.number)
    except (ValueError, OSError, ProviderError) as exc:
        result = pr_reviewer_request.outcome(args.repo, args.number, error=str(exc))
    output._json_output(result) if args.json else print(result)
    return 0 if result["ok"] else 1


def cmd_update(args):
    from . import pr_cli, pr_metadata

    try:
        _, error = pr_cli.require_claimant_worktree("pr-update")
        if error:
            raise ValueError(error)
        config = load_author_config(args.config, args.repo, claimant=True)
        if args.number < 1:
            raise ValueError("--number must be positive")
        body = Path(args.body_file).read_bytes().decode("utf-8") if args.body_file else args.body
        result = pr_metadata.update_metadata(
            config, args.repo, args.number, title=args.title, body=body,
            reviewers=tuple(args.reviewer), work_items=tuple(args.work_item),
            publication_origin=args.publication_origin,
            publication_labels=tuple(args.publication_label),
        )
        output._json_output(result) if args.json else print(result)
        return 0 if result.get("ok") else 1
    except (ValueError, OSError, ProviderError) as exc:
        return output._json_error(str(exc)) if args.json else (output.err(str(exc)) or 1)


def cmd_push(args):
    from . import pr_cli, pr_publication_labels as labels, pr_source_publication, providers

    try:
        identity, error = pr_cli.require_claimant_worktree("pr-push")
        if error:
            raise ValueError(error)
        config = load_author_config(args.config, args.repo, claimant=True)
        config = labels.activate(config, args.publication_origin, args.publication_label)
        origin = labels.selected_for_config(config)
        resolution = pr_config.resolve_repo_config_for_slug(config, args.repo)
        if not resolution.resolved:
            raise ValueError("Source target needs a registered repository PR binding")
        prcfg = resolution.repo_config.pr
        if origin is not None:
            labels.validate_policy(prcfg, origin)
        result = pr_source_publication.publish_source(
            identity, config, target_repo=args.repo, workspace=args.workspace,
            branch=args.branch, expected_head=args.expected_head,
            expected_remote_head=args.expected_remote_head,
        )
        if result.get("ok") and origin is not None:
            try:
                provider = providers.get_provider(prcfg.provider)
                token = providers.account_token_for_slug(args.repo, prcfg)
                pull = provider.find_pull_by_head(args.repo, args.branch, api_base=prcfg.api_base, token=token)
                if (
                    pull is None or getattr(pull, "merged", False)
                    or getattr(pull, "state", "") not in ("open", "active")
                ):
                    result["publication_labels"] = {
                        "ok": False, "error": (
                            "Source pushed without a confirmed active PR; create or repair the intended PR "
                            "and apply origin labels without repeating the push."
                        ),
                    }
                else:
                    result.update(number=pull.number, url=pull.url)
                    result["publication_labels"] = labels.reconcile(
                        provider, args.repo, pull.number, origin, api_base=prcfg.api_base, token=token,
                    )
                result["ok"] = bool(result["publication_labels"] and result["publication_labels"]["ok"])
            except (ValueError, OSError, ProviderError):
                result["ok"] = False
                result["publication_labels"] = {
                    "ok": False, "error": "Source pushed; origin-label outcome is unconfirmed. Repair metadata only.",
                }
        output._json_output(result) if args.json else print(result)
        return 0 if result.get("ok") else 1
    except (ValueError, OSError, ProviderError) as exc:
        return output._json_error(str(exc)) if args.json else (output.err(str(exc)) or 1)
