"""Metadata-only publication-label attachment and repair."""
from pathlib import Path

from . import config as cfg, output, pr_config, providers, tracking, worktree_identity
from . import pr_publication_labels as labels


def add_options(parser):
    parser.add_argument("--publication-origin", default=None,
                        help="Explicitly select the publication-origin policy for this operation")
    parser.add_argument("--publication-label", action="append", default=[],
                        help="Add an origin label (repeatable; requires --publication-origin)")


def add_parser(sub):
    parser = sub.add_parser("pr-publication-labels", help="Attach/repair origin labels without creating or pushing")
    parser.add_argument("worktree_id", nargs="?")
    parser.add_argument("--repo")
    parser.add_argument("--number", type=int)
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    add_options(parser)


def cmd_publication_labels(args):
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        config = labels.activate(config, args.publication_origin, args.publication_label)
        if args.repo or args.number is not None:
            if not args.repo or args.number is None or args.number < 1:
                raise ValueError("Explicit repair needs --repo and a positive --number")
            origin = labels.selected(config.default_repo.pr)
            if origin is None:
                raise ValueError("Explicit repair requires --publication-origin")
            resolution = pr_config.resolve_repo_config_for_slug(config, args.repo)
            if not resolution.resolved:
                raise ValueError("Repair target must have a registered PR binding")
            prcfg = resolution.repo_config.pr
            labels.validate_policy(prcfg, origin)
            provider = providers.get_provider(prcfg.provider)
            token = providers.account_token_for_slug(args.repo, prcfg)
            provider.get_pull(args.repo, args.number, api_base=prcfg.api_base, token=token)
            result = labels.reconcile(provider, args.repo, args.number, origin,
                                      api_base=prcfg.api_base, token=token)
        else:
            identity = worktree_identity._infer_worktree_id(args.worktree_id, config)
            if not identity:
                raise ValueError("Repair needs a worktree ID or a current managed worktree")
            identity = worktree_identity._resolve_worktree_id(identity, project=config.repo_name)
            record = tracking.load_record(cfg.tracking_dir(config.repo_name) / f"{identity}.yaml")
            target = record.active_pr()
            if target is None or target.number is None:
                raise ValueError("Repair needs a tracked existing PR")
            result = labels.refresh(config, record, target) or {"ok": True, "skipped": "no publication origin"}
        output._json_output(result) if args.json else print(result)
        return 0 if result["ok"] else 1
    except (ValueError, OSError, providers.ProviderError) as exc:
        return output._json_error(str(exc)) if args.json else (output.err(str(exc)) or 1)
