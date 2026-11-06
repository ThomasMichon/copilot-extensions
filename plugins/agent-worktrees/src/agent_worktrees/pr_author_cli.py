"""Author-side commands; repository policy and provider effects remain central."""
from __future__ import annotations

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
    for name in ("pr-author-capabilities", "pr-update", "pr-push"):
        parser = sub.add_parser(name, help={
            "pr-author-capabilities": "Report target-qualified author operations without mutation",
            "pr-update": "Repair existing PR metadata without creating or pushing",
            "pr-push": "Publish an admitted delegated source without managed branch renaming",
        }[name], allow_abbrev=False)
        parser.add_argument("--repo", required=True)
        parser.add_argument("--config")
        parser.add_argument("--json", action="store_true")
        if name == "pr-author-capabilities":
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
            "worker-policy-config": supported, "worker-policy-export": True,
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
        origin = labels.selected(config.default_repo.pr)
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
