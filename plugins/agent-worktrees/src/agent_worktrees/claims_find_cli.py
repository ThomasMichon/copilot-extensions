"""``agent-worktrees claims find`` -- fleet-wide lookup of which locally
tracked worktrees hold a claim on a PR, in one repo or in every repo.

``--json`` carries a versioned per-project envelope, ``{"schema": 1,
"projects": [{"project", "status": "ok" | "failed", "error"?, "unreadable"?,
"prs": [{"worktree_id", "authority", "repo", "number", "state"}]}]}``, so a
caller can tell a project that couldn't be read from one with no PRs. With
``--repo`` it also keeps the original ``repo``/``state``/``live_checked``/
``matches`` keys. It exits non-zero only when the project registry itself
can't be read (3), and, with ``--repo``, when nothing matches (1).

The two-hop `claims <id>` -> `owner_ref` -> `claimant-liveness` recipe (see
the `tracing-claimant-graphs` skill) assumes you already know which
worktree opened a PR. This answers the other direction: given a repo, which
of THIS machine's worktrees -- across every registered project -- claim a
PR there matching a state filter. Motivated by
ThomasMichon/copilot-extensions#4086 (a fleet sweep that previously required
a hand-rolled script per investigation).

Scope: this machine's own tracking stores only, across every project
registered here (reusing `claims_owner._iter_records`'s existing all-projects
scan) -- it does NOT reach across a separate cell (e.g. a dual-boot
machine's native install next to its WSL install, which keeps an entirely
separate tracking store); that still needs a separate invocation per cell,
same as `list --include-other-platforms` itself.
"""

from __future__ import annotations

import argparse

from . import claims_owner, output


def _pr_number(pr) -> int | None:
    """The claim's PR number, falling back to parsing it out of ``url`` when
    unset -- some pre-existing records carry a stale/absent ``number`` even
    when the URL parses fine (issue #4086 calls this out explicitly).

    Covers every supported provider's PR URL shape: GitHub/generic
    ``/pull/<n>``, Gitea ``/pulls/<n>`` (see ``providers/gitea.py``'s own
    ``/repos/{repo}/pulls/{number}`` path), Azure DevOps
    ``/pullrequest/<n>`` (see ``providers/azure_devops.py``'s
    ``_pr_web_url``), and a generic ``/pull-requests/<n>`` some hosts use.
    """
    if pr.number:
        return pr.number
    if pr.url:
        import re

        m = re.search(r"/pull(?:s|-?requests?)?/(\d+)", pr.url)
        if m:
            return int(m.group(1))
    return None


def _pr_repo(pr) -> str:
    """The claim's ``owner/name`` (Azure DevOps: ``project/repo``), recovered
    from ``url`` when the record holds only a bare repo name, as some older
    records do. Empty when neither yields a slug."""
    repo = (pr.repo or "").strip()
    if "/" in repo:
        return repo
    import re
    from urllib.parse import urlparse

    parts = [p for p in urlparse(pr.url or "").path.split("/") if p]
    if "_git" in parts:  # Azure DevOps: .../<project>/_git/<repo>/pullrequest/<n>
        i = parts.index("_git")
        if 1 <= i < len(parts) - 1:
            return f"{parts[i - 1]}/{parts[i + 1]}"
    for i, part in enumerate(parts):
        if re.fullmatch(r"pull(?:s|-?requests?)?", part) and i >= 2:
            return f"{parts[i - 2]}/{parts[i - 1]}"
    return ""


def _candidate_prs(repo: str, state: str) -> list[dict]:
    """Every locally-tracked PR claim in ``repo`` whose local ``state``
    matches (or all states, when ``state == "all"``).

    A worktree's locally tracked PR ``state`` can read stale (e.g. still
    "open" long after the PR merged elsewhere) -- this is a CANDIDATE list;
    cross-check against the provider's live state (``--live``, or the PR
    host directly) before treating a match as authoritative.
    """
    wanted_repo = repo.strip().lower()
    matches: list[dict] = []
    for project, record in claims_owner._iter_records():
        for pr in record.prs or []:
            if (pr.repo or "").strip().lower() != wanted_repo:
                continue
            if state != "all" and pr.state != state:
                continue
            matches.append({
                "project": project,
                "worktree_id": record.worktree_id,
                "machine": record.machine,
                "platform": record.platform,
                "status": record.status,
                "owner_ref": record.owner_ref,
                "codename": record.codename,
                "pr": {
                    "number": _pr_number(pr),
                    "url": pr.url,
                    "state": pr.state,
                    "branch": pr.branch,
                    "provider": pr.provider,
                },
            })
    return matches


def _live_pr_state(repo: str, number: int | None, project: str, provider_name: str) -> str | None:
    """Best-effort live PR state ("open"/"closed"/"merged"); ``None`` when
    unnumbered, unconfigured, or unreachable -- never fatal, the caller keeps
    the candidate as unverified rather than dropping it.

    Resolves ``project``'s OWN PR configuration (api_base/token/provider
    default), not the invoking project's -- a fleet scan can surface a
    candidate from a project configured for a different provider (Gitea,
    Azure DevOps) than the one the command was invoked from."""
    if not number:
        return None
    try:
        from . import config as cfg
        from . import providers

        prcfg = cfg.load_project_config(project).default_repo.pr
        provider = providers.get_provider(provider_name or prcfg.provider or "github")
        token = providers.account_token_for_slug(repo, prcfg)
        result = provider.get_pull(
            repo, number, api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        return None
    if result.merged:
        return "merged"
    return result.state


def _apply_live_check(matches: list[dict], repo: str, state: str) -> list[dict]:
    kept: list[dict] = []
    for m in matches:
        live_state = _live_pr_state(
            repo, m["pr"].get("number"), m["project"], m["pr"].get("provider") or "",
        )
        m["pr"]["live_state"] = live_state
        if live_state is None or state == "all" or live_state == state:
            kept.append(m)
    return kept


#: The ``projects[]`` envelope's version (see :func:`scan_projects`).
SCHEMA = 1
#: Exit code when the project registry itself can't be read.
REGISTRY_UNREADABLE_EXIT = 3


class RegistryUnreadable(Exception):
    """The machine's project registry couldn't be read: no project list at all."""


def canonical_authority(endpoint: str) -> str | None:
    """A provider ``authority_endpoint()`` as a canonical key: scheme,
    credentials, a default port and a trailing slash dropped, the host
    lowercased, the path kept (an Azure DevOps organization, a path-hosted
    Gitea). ``None`` when it has no host."""
    from urllib.parse import urlparse

    value = (endpoint or "").strip()
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    try:
        host, port = (parsed.hostname or "").lower(), parsed.port
    except ValueError:
        return None
    if not host:
        return None
    scheme = (parsed.scheme or "https").lower()
    if port is not None and port != {"http": 80, "https": 443}.get(scheme):
        host = f"{host}:{port}"
    path = "/".join(part for part in (parsed.path or "").split("/") if part)
    return f"{host}/{path}" if path else host


def _authority_resolver(project: str):
    """``provider_name -> canonical authority | None`` for ``project``'s own PR
    configuration; ``None`` for every PR when that configuration can't load."""
    try:
        from . import config as cfg
        from . import providers

        prcfg = cfg.load_project_config(project).default_repo.pr
    except Exception:
        return lambda provider_name: None

    def resolve(provider_name: str) -> str | None:
        try:
            provider = providers.get_provider(provider_name or prcfg.provider or "github")
            return canonical_authority(provider.authority_endpoint(getattr(prcfg, "api_base", "") or ""))
        except Exception:
            return None

    return resolve


def scan_projects(repo: str | None, state: str) -> list[dict]:
    """Every adopted project's tracked PRs (in ``repo``, or every repo), each
    project ``ok`` or ``failed`` on its own. Raises :class:`RegistryUnreadable`
    when there is no project list to scan. A record file that can't be loaded
    is counted (``unreadable``), never silently dropped: the list is then
    known to be incomplete."""
    from . import installer

    try:
        registry = installer.read_projects_registry()
    except Exception as exc:
        raise RegistryUnreadable(str(exc)) from exc
    projects = registry.get("projects") if isinstance(registry, dict) else None
    if not isinstance(projects, dict):
        raise RegistryUnreadable("the project registry has no projects mapping")
    wanted = (repo or "").strip().lower() or None
    out: list[dict] = []
    for project in sorted(str(p) for p in projects if str(p)):
        try:
            tracking_dir = claims_owner._tracking_dir(project)
            if tracking_dir is None:
                raise RuntimeError("its project directory can't be resolved")
            files = sorted(tracking_dir.glob("*.yaml")) if tracking_dir.exists() else []
        except Exception as exc:  # noqa: BLE001 -- one project's failure is that project's
            out.append({"project": project, "status": "failed", "error": str(exc) or type(exc).__name__,
                        "prs": []})
            continue
        from . import tracking

        authority = _authority_resolver(project)
        prs, unreadable = [], 0
        for path in files:
            try:
                record = tracking.load_record(path)
            except Exception:  # noqa: BLE001
                unreadable += 1
                continue
            for pr in record.prs or []:
                slug = _pr_repo(pr)
                if wanted and slug.lower() != wanted and (pr.repo or "").strip().lower() != wanted:
                    continue
                if state != "all" and pr.state != state:
                    continue
                prs.append({"worktree_id": record.worktree_id, "authority": authority(pr.provider or ""),
                            "repo": slug, "number": _pr_number(pr), "state": pr.state})
        entry = {"project": project, "status": "ok", "prs": prs}
        if unreadable:
            entry["unreadable"] = unreadable
        out.append(entry)
    return out


def _emit_human(repo: str, state: str, live: bool, matches: list[dict]) -> None:
    if live:
        verified = (
            " (live-checked; entries with live=None could not be verified "
            "and are kept unconfirmed, not authoritative)"
        )
    else:
        verified = " (local tracking -- stale entries possible; use --live to verify)"
    if not matches:
        output.err(f"No worktree claims a '{state}' PR in {repo}{verified}")
        return
    output.header(f"Worktrees claiming '{state}' PRs in {repo}{verified}")
    for m in matches:
        output.info(
            f"{m['project']} / {m['worktree_id']} "
            f"({m['platform']}, {m['status']}, codename={m.get('codename')})"
        )
        pr = m["pr"]
        live_suffix = f" live={pr.get('live_state')}" if live else ""
        output.info(f"  PR #{pr['number']}: {pr['url']} state={pr['state']}{live_suffix}")
        if m.get("owner_ref"):
            output.info(f"  owner_ref: {m['owner_ref']}")


def _emit_projects_human(state: str, projects: list[dict]) -> None:
    output.header(f"Tracked '{state}' PRs in every repo, across {len(projects)} project(s) "
                  "(local tracking -- stale entries possible)")
    for entry in projects:
        if entry["status"] != "ok":
            output.err(f"{entry['project']}: couldn't be read: {entry.get('error')}")
            continue
        if entry.get("unreadable"):
            output.warn(f"{entry['project']}: {entry['unreadable']} tracking record(s) couldn't be read")
        for pr in entry["prs"]:
            output.info(f"{entry['project']} / {pr['worktree_id']}: {pr['authority']}/{pr['repo']}"
                        f"#{pr['number']} state={pr['state']}")


def _fail(args: argparse.Namespace, msg: str, code: int) -> int:
    if args.json:
        output._json_output({"error": msg})
    else:
        output.err(msg)
    return code


def cmd_claims_find(args: argparse.Namespace, target: list[str]) -> int:
    if not target or target[0] != "pr":
        return _fail(args, "claims find: usage 'find pr [--repo <owner/name>] "
                     "[--state open|closed|merged|all] [--live]'", 2)
    repo = getattr(args, "claim_repo", None)
    state = getattr(args, "claim_state", None) or "open"
    live = bool(getattr(args, "claim_live", False))
    if live and not repo:
        return _fail(args, "claims find pr: --live needs --repo <owner/name>", 2)
    try:
        projects = scan_projects(repo, state)
    except RegistryUnreadable as exc:
        return _fail(args, f"claims find pr: the project registry can't be read: {exc}",
                     REGISTRY_UNREADABLE_EXIT)
    if not repo:
        if args.json:
            output._json_output({"schema": SCHEMA, "repo": None, "state": state, "projects": projects})
        else:
            _emit_projects_human(state, projects)
        return 0
    matches = _candidate_prs(repo, state)
    if live:
        matches = _apply_live_check(matches, repo, state)
    if args.json:
        output._json_output({
            "schema": SCHEMA, "repo": repo, "state": state, "live_checked": live, "matches": matches,
            "projects": projects,
        })
    else:
        _emit_human(repo, state, live, matches)
    return 0 if matches else 1
