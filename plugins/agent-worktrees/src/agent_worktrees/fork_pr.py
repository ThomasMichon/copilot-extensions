"""Role-aware fork-PR flow support for ``create_pr`` (split out of
``pr_ops.py`` to respect its shrink-only module-size baseline, mirroring
``record_cache.py``/``process_table_cache.py``'s own splits out of
``tracking.py``/``reclaim.py``).

Covers two related pieces of the fork-publish flow (see
``efforts/active/role-aware-fork-pr-flow``):

* :func:`ensure_fork_and_remote` -- create/verify the caller's GitHub fork of
  a repo and point a local git remote at it.
* The durable per-(operator, repo) fork-consent registry
  (:func:`fork_consent_for` / :func:`record_fork_consent`) that lets
  ``create_pr`` stop re-asking ``--confirm-fork`` once an operator has
  already confirmed a given repo's fork-publish flow before
  (ThomasMichon/copilot-extensions#4756) -- a one-time per-(operator, repo)
  decision, not a prompt on every fork-mode PR.
"""

from __future__ import annotations

from pathlib import Path

from . import git_ops, registry_paths


def ensure_fork_and_remote(worktree_path: str, repo_slug: str, prcfg) -> dict:
    """Ensure the caller's fork of ``repo_slug`` exists and a local git remote
    (``prcfg.fork.remote``) points at it.

    Returns ``{"owner": <fork-owner>}`` on success, or ``{"error": <message>}``
    on any failure (never raises) -- GitHub-only, matching ``pr.fork``'s scope.
    An explicit ``prcfg.fork.owner`` overrides the fork-owner login used to
    build the PR head, in case the caller pushes through a differently-named
    fork than the one their own token would create/read.
    """
    if prcfg.provider != "github":
        return {"error": (
            f"pr.fork is only supported for provider 'github' today "
            f"(this repo is configured for provider {prcfg.provider!r})."
        )}
    from . import providers
    try:
        provider = providers.get_provider(prcfg.provider)
        token = providers.account_token_for_slug(repo_slug, prcfg)
        fork = provider.ensure_fork(repo_slug, token=token)
    except (providers.ProviderError, OSError) as exc:
        return {"error": f"Could not create/verify a fork of '{repo_slug}': {exc}"}
    if fork is None:
        return {"error": (
            f"Could not create/verify a fork of '{repo_slug}' (no 'gh' auth, "
            f"an API error, or an unsupported provider)."
        )}
    owner, clone_url = fork
    if prcfg.fork.owner:
        owner = prcfg.fork.owner
    if not git_ops.ensure_remote(prcfg.fork.remote, clone_url, cwd=worktree_path):
        return {"error": (
            f"Could not point local git remote '{prcfg.fork.remote}' at "
            f"'{clone_url}'."
        )}
    return {"owner": owner}


def _fork_consent_path() -> Path:
    """Path to the durable, machine-local record of fork targets the
    operator has already confirmed, keyed by GitHub ``owner/repo`` slug."""
    return registry_paths.registry_path("fork_consent.yaml")


def load_fork_consent() -> dict:
    """Return the full fork-consent registry, or ``{}`` if absent/unreadable."""
    path = _fork_consent_path()
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def fork_consent_for(repo_slug: str) -> dict | None:
    """Return the previously-confirmed fork record for ``repo_slug``, or
    ``None`` if this (operator, repo) pair has never been confirmed."""
    entry = load_fork_consent().get(repo_slug)
    return entry if isinstance(entry, dict) else None


def record_fork_consent(repo_slug: str, remote: str, owner: str) -> None:
    """Durably remember that the operator confirmed the fork-PR flow for
    ``repo_slug``, so future ``create_pr`` calls don't re-ask."""
    import yaml

    path = _fork_consent_path()
    data = load_fork_consent()
    data[repo_slug] = {"remote": remote, "owner": owner}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
