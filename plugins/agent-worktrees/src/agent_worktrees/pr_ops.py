"""Pull-request workflow git operations (PR mode).

This module owns the *git* side of the PR workflow -- it never talks to a
provider API.  The agent (via a Gitea/GitHub/ADO sub-agent) creates the actual
pull request and records its URL/number back via ``set-pr``.

Branch topology (PR mode)::

    origin/master  <-  worktree/{id}  <-  feature/{slug}-{suffix}
      (upstream)       (local base,        (the PR branch: one squashed
                        tracks master)      work commit, pushed to remote)

``create_pr`` squashes the worktree's commits into one and rebases that commit
onto the upstream default branch.  The local worktree then **always lands on
that squashed commit** -- HEAD stays on ``worktree/{id}`` and the branch is
never reset off it (#1804) -- regardless of ``pr.head_scheme``.  The scheme only
selects how the PR head is *published* (its name + push mechanism):

- ``refspec`` (default, #1815/#1899): push ``worktree/{id}`` straight to the PR
  head ref (``worktree/{id}:refs/heads/{head}``, e.g. ``pr/{slug}``) -- no local
  feature branch.
- ``snapshot`` (legacy/compatible): copy the squashed commit onto a
  ``feature/{slug}-{suffix}`` branch (the older namespace) and push THAT.
  ``worktree/{id}`` is left on the squashed commit (sitting ahead of master
  while the PR is open); a later ``git sync`` reconciles it on merge. Needs no
  pre-push-hook cooperation, so it is the safe opt-out for a repo whose hook
  still blocks the mediated refspec push.

Either way the worktree stays on its own branch at the squashed commit; the
``head_scheme`` toggle is purely about PR-head naming + publish mechanism, not
about whether the worktree is reset.

See ``docs/plans/pr-workflow.md`` in test-chamber.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

from . import config as cfg
from . import git_ops, hooks, tracking
from .config import Config, SourceAttribution
from .tracking import PRRecord

HOLD_LABEL = "do-not-merge"

__all__ = [
    "HOLD_LABEL",
    "audit_attribution_risk",
    "create_pr",
    "feature_branch_name",
    "pr_head_name",
    "pr_ready",
    "pr_status",
    "resolve_head_pattern",
    "set_pr",
    "slugify",
]


def slugify(text: str, *, max_len: int = 40) -> str:
    """Sanitize *text* into a branch-safe slug (ascii, lowercase, dashes)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "change"


def missing_required_body_sections(
    body: str | None,
    required_sections: tuple[str, ...],
) -> list[str]:
    """Return required Markdown sections that are absent or visibly empty."""
    lines: list[str] = []
    in_comment = False
    fence_char = ""
    fence_len = 0
    for raw_line in (body or "").splitlines():
        if fence_char:
            closing = re.fullmatch(
                rf"\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*",
                raw_line,
            )
            if closing is not None:
                fence_char = ""
                fence_len = 0
            continue
        line = raw_line
        visible_parts: list[str] = []
        while line:
            if in_comment:
                end = line.find("-->")
                if end < 0:
                    line = ""
                    continue
                line = line[end + 3:]
                in_comment = False
                continue
            start = line.find("<!--")
            if start < 0:
                visible_parts.append(line)
                line = ""
                continue
            visible_parts.append(line[:start])
            line = line[start + 4:]
            in_comment = True
        visible_line = "".join(visible_parts)
        if in_comment and not visible_line:
            continue
        line = visible_line
        fence = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            fence_char = fence.group(1)[0]
            fence_len = len(fence.group(1))
            continue
        lines.append(line)
    headings: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append(
                (match.group(2).strip().casefold(), index, len(match.group(1)))
            )
    missing: list[str] = []
    for required in required_sections:
        wanted = required.strip().casefold()
        match_index = next(
            (index for index, heading in enumerate(headings) if heading[0] == wanted),
            None,
        )
        if match_index is None:
            missing.append(required)
            continue
        _, heading_line, heading_level = headings[match_index]
        end = len(lines)
        for _, next_line, next_level in headings[match_index + 1:]:
            if next_level <= heading_level:
                end = next_line
                break
        section = "\n".join(lines[heading_line + 1:end])
        section = re.sub(
            r"^\s{0,3}#{1,6}\s+.*$",
            "",
            section,
            flags=re.MULTILINE,
        )
        if not section.strip():
            missing.append(required)
    return missing


def feature_branch_name(prefix: str, title: str, worktree_id: str) -> str:
    """Build ``{prefix}/{slug}-{worktree_id_suffix}``.

    The suffix is the final dash-delimited token of the worktree id (its
    short hash), which keeps feature branches unique per worktree.
    """
    suffix = worktree_id.rsplit("-", 1)[-1] if "-" in worktree_id else worktree_id
    slug = slugify(title)
    return f"{(prefix or 'feature')}/{slug}-{suffix}"


def _worktree_suffix(worktree_id: str) -> str:
    """The final dash-delimited token of a worktree id (its short hash)."""
    return git_ops.worktree_suffix(worktree_id)


def _sanitize_head_ref(name: str) -> str:
    """Collapse a formatted head-name template into a tidy, valid git ref.

    Trims each ``/``-delimited segment and drops empty ones (e.g. an
    unresolved ``{username}`` that expanded to nothing), so a pattern like
    ``user/{username}/{slug}-{suffix}`` never yields a ``//`` or trailing/
    leading slash.
    """
    parts = [seg.strip().strip("-") for seg in name.split("/")]
    parts = [seg for seg in parts if seg]
    return "/".join(parts) or "pr/change"


def _resolve_username(cwd: str | None) -> str:
    """Resolve the ``{username}`` token from the repo's git identity.

    Prefers the local-part of ``user.email`` (e.g. ``cjohnson@...`` ->
    ``cjohnson``), then ``user.name``, slugified; falls back to ``user``.
    """
    if not cwd:
        return "user"
    for key in ("user.email", "user.name"):
        r = git_ops.git("config", key, cwd=cwd, check=False)
        val = r.stdout.strip() if r.returncode == 0 else ""
        if val:
            local = val.split("@", 1)[0]
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", local.lower()).strip("-")
            if slug:
                return slug
    return "user"


def resolve_head_pattern(prcfg) -> str:
    """The PR head-name template for *prcfg* (explicit override or default).

    An explicit ``head_pattern`` wins. Azure DevOps otherwise defaults to
    ``user/{username}/{slug}-{suffix}`` regardless of ``head_scheme``; other
    providers keep the existing scheme defaults: ``refspec`` uses ``pr/{slug}-{suffix}``
    and ``snapshot`` keeps ``{prefix}/{slug}-{suffix}`` (``feature/<slug>``).
    """
    if getattr(prcfg, "head_pattern", ""):
        return prcfg.head_pattern
    if getattr(prcfg, "provider", "") == "azure-devops": return "user/{username}/{slug}-{suffix}"
    if getattr(prcfg, "head_scheme", "snapshot") == "refspec":
        return "pr/{slug}-{suffix}"
    return "{prefix}/{slug}-{suffix}"


def _pattern_references_token(pattern: str, token: str) -> bool:
    """Whether ``pattern`` contains a ``str.format`` field for ``token``."""
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(pattern):
        if not field_name:
            continue
        root = field_name.split(".", 1)[0].split("[", 1)[0]
        if root == token:
            return True
    return False


def _augment_default_head_pattern_for_topic(pattern: str, *, has_topic: bool) -> str:
    """Insert ``{topic}`` ahead of ``{suffix}`` for default head patterns only."""
    if not has_topic or "{suffix}" not in pattern:
        return pattern
    return pattern.replace("{suffix}", "{topic}-{suffix}", 1)


def _effective_head_pattern(prcfg, *, topic: str = "") -> tuple[str, str | None]:
    """Resolved head pattern plus an optional note about ignored ``topic``."""
    pattern = resolve_head_pattern(prcfg)
    if not topic:
        return pattern, None
    if getattr(prcfg, "head_pattern", ""):
        if _pattern_references_token(pattern, "topic"):
            return pattern, None
        return pattern, (
            "Ignoring --topic because explicit pr.head_pattern does not "
            "reference {topic}."
        )
    return _augment_default_head_pattern_for_topic(pattern, has_topic=True), None


def audit_attribution_risk(config: Config) -> list[str]:
    """Migration audit (pr-attribution-codenames Phase 5): flag this repo's
    configured ``pr.head_pattern`` for the branch-name leak class.

    Config-only: it does not run ``create-pr`` or touch git. A repo is at
    risk when ``pr.source_attribution`` is not exactly ``true`` -- ``false``,
    ``"codename"``, **or omitted entirely** (codename-attribution-by-default
    flipped the runtime default to ``"codename"``, so an absent key is just
    as much at risk as one explicitly set to ``codename``) -- and its
    configured ``head_pattern`` embeds ``{machine}`` (in any ``str.format``
    conversion/format-spec variant), which could carry a private identifier
    into a published branch name. ``{worktree_id}`` is deliberately never
    flagged: it is not part of ``pr_head_name``'s actual rendering contract
    (only ``prefix``/``slug``/``suffix``/``username``/``machine`` are), so a
    pattern containing it raises inside ``str.format`` and falls back to the
    safe default rather than ever publishing that literal text -- see
    ``providers.attribution.head_pattern_leak_risk``. Returns a list of
    human-readable findings (empty when this repo's config is not at risk).
    """
    from .providers.attribution import audit_source_attribution_risk

    prcfg = config.default_repo.pr
    reported_attribution = (
        prcfg.source_attribution if prcfg.source_attribution_configured else None
    )
    return audit_source_attribution_risk(
        source_attribution=reported_attribution,
        head_pattern=getattr(prcfg, "head_pattern", "") or "",
    )


def pr_head_name(
    prcfg,
    title: str,
    worktree_id: str,
    *,
    cwd: str | None = None,
    machine: str = "",
    topic: str = "",
) -> str:
    """Build the PR head branch name from the repo's configured template.

    Resolves the ``head_pattern`` template (scheme-aware default) against the
    ``{prefix}`` / ``{slug}`` / ``{suffix}`` / ``{username}`` / ``{machine}``
    / ``{topic}`` tokens. With the ``snapshot`` default this returns exactly
    ``feature_branch_name(prefix, title, worktree_id)``.
    """
    topic = slugify(topic) if topic.strip() else ""
    pattern, _topic_note = _effective_head_pattern(prcfg, topic=topic)
    tokens = {
        "prefix": (getattr(prcfg, "branch_prefix", "") or "feature"),
        "slug": slugify(title),
        "suffix": _worktree_suffix(worktree_id),
        "username": _resolve_username(cwd),
        "machine": machine or "",
        "topic": topic,
    }
    try:
        name = pattern.format(**tokens)
    except (KeyError, IndexError, ValueError):
        # A malformed template must never break create-pr -- fall back to the
        # legacy default rather than raising.
        name = f"{tokens['prefix']}/{tokens['slug']}-{tokens['suffix']}"
    return _sanitize_head_ref(name)


def _rollback(worktree_path: str, wt_branch: str, orig_sha: str | None) -> None:
    """Restore the worktree branch to its pre-create-pr commit."""
    if orig_sha:
        git_ops.git("checkout", wt_branch, "--quiet", cwd=worktree_path, check=False)
        git_ops.git("reset", "--hard", orig_sha, "--quiet", cwd=worktree_path, check=False)
    git_ops.git("update-ref", "-d", "refs/pre-squash-backup", cwd=worktree_path, check=False)


def _rev(ref: str, *, cwd: str) -> str:
    r = git_ops.git("rev-parse", ref, cwd=cwd, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def _patch_id(base: str, head: str, *, cwd: str) -> str:
    """Squash-invariant patch-id of ``base..head`` (#898), or "" on failure.

    ``git diff base..head | git patch-id --stable`` identifies the *change
    content*, invariant across a squash / rebase / re-commit -- so a downstream
    recorder (an issue-close comment, a session ref) can bind to something that
    survives the server-side squash-merge rewriting the commit SHA, instead of a
    pre-squash SHA that dangles once the work lands. Best-effort: an empty diff or
    any git error yields "".
    """
    if not base:
        return ""
    diff = git_ops.git("diff", f"{base}..{head}", cwd=cwd, check=False)
    if diff.returncode != 0 or not diff.stdout:
        return ""
    try:
        import subprocess
        pid = subprocess.run(
            ["git", "patch-id", "--stable"],
            input=diff.stdout, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if pid.returncode != 0:
        return ""
    out = pid.stdout.strip()
    # `git patch-id` prints "<patch-id> <commit-id>"; take the patch-id token.
    return out.split()[0] if out else ""


def _commit_patch_ids(base: str, head: str, *, cwd: str) -> dict[str, set[str]]:
    """Map patch IDs to non-merge commits in ``base..head``.

    Stream one ``git log`` process into one ``git patch-id`` process. This
    avoids both per-commit process spawning and buffering a long patch history
    in memory.
    """
    if not base:
        return {}
    import subprocess

    log_process: subprocess.Popen[bytes] | None = None
    patch_process: subprocess.Popen[str] | None = None
    env = git_ops.repository_identity_env()
    try:
        log_process = subprocess.Popen(
            [
                "git", "log", "--no-merges", "--format=commit %H", "-p",
                f"{base}..{head}",
            ],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if log_process.stdout is None:
            log_process.kill()
            log_process.wait()
            return {}
        patch_process = subprocess.Popen(
            ["git", "patch-id", "--stable"],
            cwd=cwd,
            env=env,
            stdin=log_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log_process.stdout.close()
        output, _ = patch_process.communicate(timeout=30)
        log_returncode = log_process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        if patch_process is not None and patch_process.poll() is None:
            patch_process.kill()
            patch_process.communicate()
        if log_process is not None and log_process.poll() is None:
            log_process.kill()
            log_process.wait()
        return {}
    if patch_process.returncode != 0 or log_returncode != 0:
        return {}
    result: dict[str, set[str]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            result.setdefault(parts[0], set()).add(parts[1])
    return result


def _title_from_commits(worktree_path: str, upstream: str) -> str | None:
    """Best-effort worktree title from its own commit history.

    Returns the subject of the newest commit on ``{upstream}..HEAD`` -- the work
    the worktree currently carries -- or ``None`` when the worktree has no commit
    beyond ``upstream`` (nothing to describe) or git is unavailable.

    Used as the ``create_pr`` title fallback so a worktree that never carried a
    session summary still yields a meaningful PR title and Picker label instead
    of the opaque ``worktree_id``.
    """
    try:
        result = git_ops.git(
            "log", "-1", "--format=%s", f"{upstream}..HEAD",
            cwd=worktree_path, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    subject = (result.stdout or "").strip()
    return subject or None


def _resolve_caller_role(prcfg, repo_slug: str) -> str | None:
    """Resolve the caller's live permission on ``repo_slug`` for role-aware PR
    flow resolution (efforts/active/role-aware-fork-pr-flow, GitHub-only).

    Reuses the already-landed ``providers.actor_viewer_permission()`` (#2433)
    rather than a second provider-specific primitive -- no extra request shape
    to maintain. Returns ``None`` (never raises) when the repo's provider
    isn't ``"github"``, ``repo_slug`` is empty, or the live read is unknown/
    failed -- callers must treat that exactly like "no role resolved" (the
    base, non-role-scoped ``PRConfig`` applies), never as a denial.
    """
    if prcfg.provider != "github" or not repo_slug:
        return None
    from . import providers
    try:
        provider = providers.get_provider(prcfg.provider)
        token = providers.account_token_for_slug(repo_slug, prcfg)
        permission = providers.actor_viewer_permission(
            provider, repo_slug, api_base=prcfg.api_base, token=token,
        )
    except Exception:
        return None
    return permission or None


def _ensure_fork_and_remote(worktree_path: str, repo_slug: str, prcfg) -> dict:
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


def create_pr(
    worktree_id: str,
    config: Config,
    *,
    title: str | None = None,
    branch: str | None = None,
    topic: str | None = None,
    target_repo: str | None = None,
    new: bool = False,
    body: str | None = None,
    open_pr: bool | None = None,
    hold: bool = False,
    draft: bool = False,
    attribution: SourceAttribution | None = None,
    dry_run: bool = False,
    confirm_fork: bool = False,
) -> dict:
    """Squash worktree commits, create + push a feature branch for a PR.

    Returns a JSON-friendly result dict.  On success it includes ``branch``,
    ``remote``, ``base_sha``, ``head_sha``, ``provider`` and ``default_branch``.

    When a provider is configured and ``pr.auto_open`` is on (and ``open_pr``
    is not False), the matching provider plugin **opens the PR** right after
    the push and **auto-recording** the resulting url/number on the worktree (no
    skippable manual ``set-pr``). By default (codename-attribution-by-default),
    the body also receives a public-safe hidden marker carrying only the
    worktree's assigned codename. Repos that explicitly enable the full raw
    ``pr.source_attribution: true`` instead receive a hidden marker with the
    raw machine/worktree/session values -- unsuitable for public PRs, so this
    stays off outside closed-circuit repos. Provider failure is non-fatal: the
    feature branch is already pushed, so the result carries ``pr_open_error``
    and the agent can fall back to delegating PR creation manually.

    ``confirm_fork`` gates the role-aware fork-PR flow (see
    ``efforts/active/role-aware-fork-pr-flow`` in this repo, GitHub-only
    today): when the repo's ``pr.roles``/``pr.fork`` config resolves the
    caller's live role to a flow that publishes through a personal fork
    rather than a direct push, ``create_pr`` does **not** silently fork or
    push anywhere on a caller's first call. It returns a
    ``needs_confirmation: "fork_setup"`` result explaining what it would do,
    for the calling agent to relay to the human. Only a second call with
    ``confirm_fork=True`` actually creates/verifies the fork, points a local
    remote at it, and publishes there. A repo that never configures
    ``pr.fork``/``pr.roles`` never resolves a fork flow, so this parameter is
    a no-op for it -- fully backward compatible.

    A worktree can track multiple PRs.  When the active PR is **terminal**
    (merged/closed) -- or ``new`` is set, or none exists -- a *fresh* PR is
    appended (new branch off the current default-branch tip).  When a **live**
    (open/creating) PR exists, its branch is reused and the call iterates it.

    ``topic`` (``--topic``) is an optional mini-task token folded into the
    generated default head name. Explicit ``--branch`` still fully overrides
    head naming, and an explicit ``pr.head_pattern`` only uses ``topic`` when
    it actually references ``{topic}``.

    ``target_repo`` (``--repo owner/name``) records the PR's target repo;
    it defaults to the worktree's own repo.

    Idempotent: safe to re-run.  A successful run leaves HEAD on the worktree
    branch (``worktree/{id}``) at the squashed commit -- it is never reset off
    it -- so a retry after a push-that-failed-to-open lands there with the
    squashed work still in place and is recognized as a re-run of the live
    tracked PR: the head is simply (re)pushed (force-with-lease) with the
    tracking state advanced to ``open``.  Two legacy/migration cases are handled
    the same way: HEAD still on the feature branch (a push that failed before the
    old code returned HEAD), and ``worktree/{id}`` sitting at the upstream tip
    with the feature branch still local (a worktree created under the old
    reset-to-upstream scheme).
    """
    repo = config.default_repo
    prcfg = repo.pr
    remote = repo.remote
    # ``--hold`` is retained as a deprecated alias for ``--draft``: the old
    # "open held with a do-not-merge label" model is retired in favour of
    # Gitea's native draft state (a WIP-prefixed title), which ``pr-ready``
    # clears. Both flags mean the same thing now -- open the PR as a draft.
    want_draft = bool(draft or hold)
    upstream = f"{remote}/{repo.default_branch}"
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)
    wt_branch = f"worktree/{worktree_id}"

    base: dict = {"success": False, "worktree_id": worktree_id}

    if not prcfg.enabled:
        return {**base, "error": (
            "PR mode is not enabled for this repo. Set pr.enabled: true in "
            "the repo config to use create-pr."
        )}

    if not Path(worktree_path).exists():
        return {**base, "error": f"Worktree path not found: {worktree_path}"}

    # Load tracking record (optional but expected).
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record: tracking.WorktreeRecord | None = None
    if yaml_path.exists():
        try:
            record = tracking.load_record(yaml_path)
        except Exception:
            record = None

    # Round-22 finding: preflight the same allocation-time policy
    # `_open_via_provider`'s lazy backfill would otherwise only catch too
    # late (after the squash/force-push and PR-record save, per that
    # function's own docstring). Fail create_pr outright, before any side
    # effect, when this record will need to lazy-backfill a codename (no
    # codename yet) under an effective "codename" attribution -- the exact
    # scenario the policy exists to block. A residual TOCTOU window between
    # this preflight and the actual backfill is accepted (matching this
    # plan's established narrowed-transactionality posture elsewhere); the
    # already-documented re-raise inside `_open_via_provider` is the
    # defense-in-depth backstop for that window, not a duplicate of this
    # preflight.
    if record is not None and not record.codename:
        _want_attribution = (
            prcfg.source_attribution if attribution is None else attribution
        )
        if _want_attribution == "codename":
            from . import codename_tracking
            codename_tracking.check_allocation_policy(
                pr_enabled=prcfg.enabled,
                codename_source=codename_tracking.classify_codename_source(
                    repo.codename
                ),
                source_attribution_configured=prcfg.source_attribution_configured,
            )

    # Gate the write on the *normalized* value -- a whitespace-only `title`
    # is truthy and must not overwrite an existing, genuinely curated
    # persisted title with `None`.
    _normalized_input_title = tracking.normalize_title(title)
    if _normalized_input_title and record:
        record.title = _normalized_input_title

    # Normalize both candidates before deciding whether a title is already
    # usable -- an un-normalized whitespace-only `title` or a stale
    # whitespace-only persisted `record.title` (e.g. from a pre-existing
    # record set by an older version) is truthy but not meaningful, and must
    # not skip the commit-subject derivation below.
    eff_title = _normalized_input_title or tracking.normalize_title(
        record.title if record else None
    )
    if not eff_title:
        # No explicit --title and no persisted worktree title. Derive a
        # meaningful default from the worktree's own newest commit subject
        # rather than degrading to the opaque worktree_id -- which yields a
        # useless "<machine>-<ts>-<hash>" PR title, a "<id> changes" squash
        # message, and leaves the worktree reading as "(untitled)" in the
        # Picker forever (its session, if any, may never have registered).
        derived = _title_from_commits(worktree_path, upstream)
        if derived:
            eff_title = derived
            # Persist it so the worktree stops showing as untitled -- but only
            # when the record has no curated title of its own (true here by
            # construction), so an operator/PR title is never clobbered.
            if record and not (record.title and record.title != "null"):
                record.title = tracking.normalize_title(derived)
    # Last resort: never fall back to the raw worktree_id (it embeds the
    # authoring machine name and creation timestamp) or any other synthetic
    # placeholder. If even the commit-subject derivation above came up empty,
    # this worktree genuinely has no meaningful title anywhere -- require the
    # caller to supply one explicitly rather than inventing one. Normalize
    # first (whitespace-only -> None) WITHOUT truncating -- `eff_title`
    # publishes as the PR title and the branch-name slug, so it must not be
    # silently shortened to the Picker's short-display limit.
    eff_title = tracking.normalize_title(eff_title)
    # Keep the persisted record in lockstep with the normalized value -- a
    # caller-supplied title containing carriage returns/tabs/other control
    # characters must not survive un-normalized in either the tracking YAML
    # or (via squash_msg below, which reads record.title first) the squash
    # commit message.
    if record and eff_title:
        record.title = eff_title
    if not eff_title:
        return {**base, "error": (
            "No usable title could be determined for this PR: --title was "
            "either omitted or contained only whitespace, no title is "
            "persisted on the worktree, and no meaningful title could be "
            "derived from the worktree's own commit history (e.g. an empty "
            "commit subject). Re-run with an explicit, non-blank --title "
            "describing the change."
        )}

    # Resolve the active PR and whether it is still live (can receive pushes).
    # A *terminal* active PR (merged/closed) must NOT have its branch reused --
    # pushing onto a merged branch does not reopen it (the #1088->#1104 bug).
    # First reconcile the active PR's state against the provider: a PR merged
    # *externally* (e.g. Gitea API + auto-merge label) leaves the local record
    # stale at 'open', which would otherwise reuse + force-push a merged branch
    # and open no new PR (#1163).
    recorded_active = record.active_pr() if record else None
    if (
        recorded_active is not None
        and not tracking._pr_is_terminal(recorded_active)
        and recorded_active.provider
        and recorded_active.provider != prcfg.provider
    ):
        return {
            **base,
            "error": (
                f"Tracked PR provider {recorded_active.provider!r} differs from "
                f"configured provider {prcfg.provider!r}; refusing provider "
                "access with mismatched credentials."
            ),
        }
    _reconcile_active_pr(record, config)
    active = record.active_pr() if record else None
    active_is_live = active is not None and not tracking._pr_is_terminal(active)

    # Second line of defense (#1984): the provider reconcile above can still
    # miss an externally-merged PR when its state query *races* the merge (the
    # PR is merged a beat later) or the provider is briefly unreachable -- the
    # record is then left stale at 'open'. Reusing that PR's feature branch
    # would force-push (with lease) onto a ref the host DELETED on merge, which
    # the lease check rejects, wedging tracking at 'creating' with no PR opened
    # (a regression/uncovered variant of #1163 / #1336). So when we would
    # otherwise reuse a "live" PR's branch, verify that branch still exists on
    # the remote: one that is *confirmed gone* (remote reachable, ref absent)
    # means the PR merged and its branch was auto-pruned -- mark it terminal so
    # the fresh-branch-from-title path is taken instead. Only "absent" is
    # authoritative; an unreachable remote ("unknown") keeps the prior
    # (reuse) behavior. Looping lets a stack of stale merged+pruned PRs all
    # reconcile down to the genuinely-live (or no) active PR.
    if not new and not branch and git_ops.has_remote(remote, cwd=worktree_path):
        while active_is_live and active is not None and active.branch:
            if git_ops.remote_branch_state(
                remote, active.branch, cwd=worktree_path
            ) != "absent":
                break
            active.state = "merged"
            if not active.closed_at:
                active.closed_at = tracking._now_iso()
            if record is not None:
                tracking.save_record(record)
            active = record.active_pr() if record else None
            active_is_live = active is not None and not tracking._pr_is_terminal(active)

    needs_body = new or not active_is_live or active.number is None
    if needs_body:
        missing_body = missing_required_body_sections(
            body,
            prcfg.required_body_sections,
        )
        if missing_body:
            return {
                **base,
                "error": (
                    "PR body is missing required non-empty section(s): "
                    + ", ".join(missing_body)
                    + ". Pass --body or --body-file before opening the PR."
                ),
            }

    topic_slug = slugify(topic) if (topic or "").strip() else ""
    topic_note = None

    # Resolve the feature branch name: explicit > live active PR > derived.
    if branch:
        feature_branch = branch
        if topic_slug:
            topic_note = "Ignoring --topic because --branch fully overrides the head name."
    elif active_is_live and not new and active.branch:
        feature_branch = active.branch
    else:
        _pattern, topic_note = _effective_head_pattern(prcfg, topic=topic_slug)
        feature_branch = pr_head_name(
            prcfg, eff_title, worktree_id,
            cwd=worktree_path, machine=config.machine, topic=topic_slug,
        )

    # Branch-name leak class (pr-attribution-codenames Phase 5): whichever way
    # the head above was resolved (explicit --branch, a reused existing-PR
    # branch, or a rendered head_pattern), it must never carry a private
    # identifier into a public branch name unless this repo has explicitly
    # opted into the full raw marker (source_attribution: true). This is a
    # hard, publish-blocking error -- never a warning -- and is checked before
    # the dry-run response too, so a dry run surfaces the same block.
    effective_attribution = (
        prcfg.source_attribution if attribution is None else attribution
    )
    try:
        from .providers.attribution import validate_effective_head
        # Check both the LIVE config machine and the worktree's originally
        # RECORDED machine (`record.machine`) -- a renamed/migrated machine
        # can otherwise leave an old identifying branch name (e.g. a reused
        # existing-PR head from before the rename) unchecked against the
        # live config alone.
        validate_effective_head(
            feature_branch,
            worktree_id=worktree_id,
            machine=(config.machine, record.machine if record else ""),
            source_attribution=effective_attribution,
        )
    except ValueError as exc:
        return {**base, "error": str(exc)}

    if dry_run:
        result = {
            **base, "success": True, "dry_run": True,
            "branch": feature_branch, "remote": remote,
            "provider": prcfg.provider, "default_branch": repo.default_branch,
            "draft": want_draft,
        }
        if topic_note:
            result["topic_note"] = topic_note
        return result

    # Resolve the PR's target repo as the hosting ``owner/name`` slug (what the
    # provider API needs), in order: explicit --repo > the remote's slug > a
    # previously-recorded value > the local project name (last-resort).
    # Resolved here (rather than just before it's first consumed, further
    # down) because the role-aware fork-PR resolution right below also needs
    # it, and re-deriving it twice risked drifting the two apart.
    host_slug = git_ops.remote_slug(remote, cwd=worktree_path)
    default_pr_repo = (
        target_repo or host_slug or (record.repo if record else "") or ""
    )

    # --- Role-aware PR flow resolution + fork-publish confirmation gate ----
    # (efforts/active/role-aware-fork-pr-flow, Phase 2b, GitHub-only). Only
    # touches anything when the repo opts in via `pr.roles` and/or
    # `pr.fork.enabled`; an unconfigured repo's `prcfg`/`publish_remote` are
    # unchanged from here on -- byte-for-byte today's behavior.
    publish_remote = remote
    fork_owner = ""
    if prcfg.roles:
        prcfg = cfg.resolve_role_pr_config(
            prcfg, _resolve_caller_role(prcfg, default_pr_repo),
        )
    if prcfg.fork.enabled:
        if not confirm_fork:
            return {
                **base, "success": False,
                "needs_confirmation": "fork_setup",
                "repo": default_pr_repo,
                "fork_remote": prcfg.fork.remote,
                "message": (
                    f"This repo's resolved PR flow publishes through a "
                    f"personal fork of '{default_pr_repo}' rather than a "
                    f"direct push. Ask the user to confirm forking it to "
                    f"their own GitHub account and pushing the branch "
                    f"there, then re-run create-pr with --confirm-fork "
                    f"(or confirm_fork=True) once they agree."
                ),
            }
        fork_setup = _ensure_fork_and_remote(worktree_path, default_pr_repo, prcfg)
        if fork_setup.get("error"):
            return {**base, "error": fork_setup["error"]}
        publish_remote = prcfg.fork.remote
        fork_owner = fork_setup["owner"]

    if not git_ops.is_clean(cwd=worktree_path):
        return {**base, "error": (
            "Working tree has uncommitted changes; commit or stash them "
            "before create-pr."
        )}

    # Best-effort fetch so the rebase targets current upstream.
    if git_ops.has_remote(remote, cwd=worktree_path):
        try:
            git_ops.fetch(remote, cwd=worktree_path)
        except git_ops.GitError:
            pass
        else:
            if record and record.repo:
                tracking.record_repo_fetch_confirmed(record.repo)

    head_branch = git_ops._get_current_branch_safe(worktree_path)

    # --- Re-run path: already on the feature branch -> (re)push + record. ---
    if head_branch == feature_branch:
        return _push_existing_feature(
            worktree_path, feature_branch, publish_remote, repo, prcfg, record,
            base, config=config, worktree_id=worktree_id, title=eff_title,
            body=body, open_pr=open_pr, draft=want_draft, attribution=attribution,
            pr_head=(f"{fork_owner}:{feature_branch}" if fork_owner else ""),
        )

    if head_branch != wt_branch:
        return {**base, "error": (
            f"Worktree HEAD is on '{head_branch}', expected '{wt_branch}'. "
            f"Checkout '{wt_branch}' before create-pr."
        )}

    reusing = bool(active_is_live and not new and active and active.branch == feature_branch)
    ahead = git_ops.get_commits_ahead(wt_branch, upstream, cwd=worktree_path)

    # --- Re-run fast path: a live PR whose head is already published and whose
    #     base has nothing new to squash. This is hit by (a) a legacy/migration
    #     worktree created under the old scheme that DID reset worktree/<id> to
    #     upstream (so it now sits at the tip, `not ahead`), and (b) any repo
    #     where the merged content has already synced back. In both cases the
    #     squashed work already lives on the (still-local) feature branch, so
    #     re-push that branch instead of tripping the "already exists" guard or
    #     the "nothing ahead" error below. Under the current scheme a *successful*
    #     create-pr leaves worktree/<id> ONE ahead (the squashed commit is kept
    #     in place, never reset), so a normal iterate/retry has `ahead` non-empty
    #     and falls through to re-squash + force-push onto the reused branch. ---
    if reusing and not ahead and git_ops.local_branch_exists(
        feature_branch, cwd=worktree_path
    ):
        return _push_existing_feature(
            worktree_path, feature_branch, publish_remote, repo, prcfg, record,
            base, config=config, worktree_id=worktree_id, title=eff_title,
            body=body, open_pr=open_pr, draft=want_draft, attribution=attribution,
            pr_head=(f"{fork_owner}:{feature_branch}" if fork_owner else ""),
        )

    if not reusing:
        if git_ops.local_branch_exists(feature_branch, cwd=worktree_path) or \
                git_ops.remote_branch_exists(publish_remote, feature_branch, cwd=worktree_path):
            return {**base, "error": (
                f"Feature branch '{feature_branch}' already exists locally or on "
                f"'{publish_remote}'. If this is a different mini-task within the same "
                f"effort/worktree, retry with --topic <token> or an explicit --branch "
                f"that adds a distinguishing suffix (for example '{feature_branch}-2' "
                f"or a short task token)."
            )}

    if not ahead:
        return {**base, "error": (
            f"No commits on {wt_branch} ahead of {upstream} -- nothing to "
            f"open a PR for."
        )}

    orig_sha = _rev(wt_branch, cwd=worktree_path)

    # Resolve the target PRRecord: reuse the live active PR, or append a fresh
    # one (serial re-PR / parallel / explicit --new).  Record the transitional
    # 'creating' state up front so a later failure is recoverable.
    target_pr: PRRecord | None = None
    if record is not None:
        if reusing and active is not None:
            target_pr = active
            target_pr.state = "creating"
            target_pr.branch = feature_branch
            if not target_pr.provider:
                target_pr.provider = prcfg.provider
            if target_repo:
                if target_repo != target_pr.repo:
                    target_pr.attribution_head = ""
                    target_pr.head_observed_at = ""
                    target_pr.head_observed_api_base = ""
                target_pr.repo = target_repo
            if not target_pr.opened_at:
                target_pr.opened_at = tracking._now_iso()
        else:
            target_pr = PRRecord(
                state="creating", branch=feature_branch,
                provider=prcfg.provider, repo=default_pr_repo,
                opened_at=tracking._now_iso(),
            )
            # codename-attribution-by-default: freeze this PR's attribution
            # decision ONCE, at this fresh-construction site -- the
            # EFFECTIVE (caller-override-or-config) value, stamped verbatim,
            # never re-derived from live config again for this PR's life.
            _want_attribution = (
                prcfg.source_attribution if attribution is None else attribution
            )
            tracking.stamp_frozen_attribution(
                target_pr, attribution=_want_attribution,
                explicit=(
                    attribution is not None
                    or prcfg.source_attribution_configured
                ),
            )
            record.prs.append(target_pr)
        tracking.save_record(record)

    # Use `eff_title` directly rather than re-reading `record.title` -- the
    # two are kept in lockstep above, but reading `eff_title` here can't
    # diverge even if that invariant ever changes. It is never the raw
    # worktree_id (see the fallback above), so no separate machine-name
    # guard is needed either.
    squash_msg = eff_title

    # 1. Rebase the worktree commits onto the upstream default branch FIRST,
    #    with the individual commits intact -- BEFORE squashing. This lets
    #    ``git rebase`` drop any commit already present on upstream by patch-id.
    #    The case that matters (#546): a REUSED worktree whose prior PR was
    #    already **squash-merged**. Because every agent-worktrees PR is a single
    #    squashed commit, that prior commit's patch-id matches the squash-merge
    #    on upstream, so the rebase drops it cleanly and only the new work
    #    survives. Squashing *first* (the old order) fused the already-merged
    #    commit with the new work into one patch that no longer matched
    #    upstream, forcing a spurious conflict that aborted create-pr.
    base_sha = ""
    if git_ops.ref_exists(upstream, cwd=worktree_path):
        if not git_ops.rebase(upstream, cwd=worktree_path):
            _rollback(worktree_path, wt_branch, orig_sha)
            return {**base, "error": (
                f"Rebase onto {upstream} hit genuine conflicts between the "
                f"worktree's new commits and {upstream} (commits already merged "
                f"upstream are dropped automatically). The rebase was aborted "
                f"and '{wt_branch}' was left unchanged -- there is nothing to "
                f"resolve in place. Rebase manually (git rebase {upstream}), fix "
                f"the conflicts, then re-run create-pr."
            )}
        base_sha = _rev(upstream, cwd=worktree_path)
        # Recompute what remains ahead of upstream: the rebase may have dropped
        # an already-merged commit, so the pre-rebase ``ahead`` is now stale.
        ahead = git_ops.get_commits_ahead(wt_branch, upstream, cwd=worktree_path)
        if not ahead:
            _rollback(worktree_path, wt_branch, orig_sha)
            return {**base, "error": (
                f"All commits on {wt_branch} are already present on {upstream} "
                f"(they were merged upstream) -- nothing new to open a PR for."
            )}

    # 2. Squash the surviving worktree commits into one. After the rebase the
    #    survivors are exactly the new work (any already-merged commit is gone).
    if len(ahead) > 1:
        squashed, squash_reason = git_ops.squash_branch(
            upstream, squash_msg, cwd=worktree_path
        )
        if not squashed:
            _rollback(worktree_path, wt_branch, orig_sha)
            detail = f" {squash_reason}" if squash_reason else ""
            return {**base, "error": f"Failed to squash worktree commits.{detail}"}

    head_sha = _rev("HEAD", cwd=worktree_path)
    # Squash-invariant reference for downstream recorders (#898): survives the
    # server-side squash-merge that rewrites the commit SHA.
    patch_id = _patch_id(base_sha, "HEAD", cwd=worktree_path)

    # Effective per-invocation head scheme. In a refspec repo, a *parallel* PR
    # (--new while another PR is still live) cannot use worktree/<id> as its
    # head -- that branch is the live refspec head of the other PR -- so it
    # falls back to snapshotting onto a separate feature branch, WITHOUT
    # resetting worktree/<id> (#1815 Phase 3). The single-PR serial flow (no
    # parallel) stays pure refspec.
    parallel_snapshot = bool(
        prcfg.head_scheme == "refspec" and new and active_is_live
    )
    use_refspec = prcfg.head_scheme == "refspec" and not parallel_snapshot

    if use_refspec:
        # Refspec mode (#1815): keep the squashed work ON worktree/<id> and push
        # it directly to the PR head ref. No local feature branch, no checkout
        # dance; HEAD never leaves wt_branch, and wt_branch is NOT reset to
        # upstream -- it legitimately sits ahead of master while the PR is open
        # (a later `git sync` fast-forwards it clean on merge).
        with hooks.allow_pr_push():
            pushed = git_ops.push(
                publish_remote, f"{wt_branch}:refs/heads/{feature_branch}",
                cwd=worktree_path, force_with_lease=reusing,
            )
        if not pushed:
            return {**base, "error": (
                f"Failed to push '{wt_branch}' to '{publish_remote}/{feature_branch}'. "
                f"The squashed work is on '{wt_branch}'; tracking state left as "
                f"'creating' for retry (re-run create-pr)."
                + (f"\ngit: {pushed.stderr.strip()}" if pushed.stderr else "")
            )}
    else:
        # Snapshot publish: the local worktree lands on the squashed commit
        # exactly as in refspec mode -- HEAD never leaves worktree/<id> and
        # worktree/<id> is NOT reset to upstream (it legitimately sits ahead of
        # master while the PR is open; a later `git sync` reconciles it on
        # merge, see finalize._reconcile_merged_pointers). The ONLY thing the
        # scheme changes is HOW the PR head is *published*: snapshot copies the
        # squashed commit onto a separately-named local ``feature/<slug>`` branch
        # (the older namespace) and pushes THAT, whereas refspec pushes
        # worktree/<id> straight to ``pr/<slug>`` via a refspec. "Land on the
        # squashed commit" is universal (#1804); ``head_scheme`` only selects the
        # PR-head NAME + push mechanism, never whether the worktree is reset.
        #
        # This path also serves a refspec repo's parallel --new PR
        # (``parallel_snapshot``): its head cannot be worktree/<id> (that is the
        # first PR's live refspec head), so it snapshots onto its own feature
        # branch -- and, crucially, still leaves worktree/<id> and HEAD alone.
        #
        # No checkout dance: `git push` publishes the named local ref while HEAD
        # stays on worktree/<id>.
        git_ops.git("branch", "-f", feature_branch, "HEAD", cwd=worktree_path, check=False)
        with hooks.allow_pr_push():
            pushed = git_ops.push(
                publish_remote, feature_branch, cwd=worktree_path, force_with_lease=reusing
            )
        if not pushed:
            return {**base, "error": (
                f"Failed to push '{feature_branch}' to '{publish_remote}'. The squashed "
                f"work is on '{wt_branch}' (and the local '{feature_branch}' "
                f"snapshot); tracking state left as 'creating' for retry "
                f"(re-run create-pr)."
                + (f"\ngit: {pushed.stderr.strip()}" if pushed.stderr else "")
            )}

    # 7. Record the open state on the target PR (preserving any url/number
    #    already recorded for a reused live PR).
    if record is not None and target_pr is not None:
        target_pr.state = "open"
        target_pr.branch = feature_branch
        target_pr.base_sha = base_sha
        target_pr.head_sha = head_sha
        target_pr.head_observed_at = ""
        target_pr.head_observed_api_base = ""
        target_pr.patch_id = patch_id
        if not target_pr.provider:
            target_pr.provider = prcfg.provider
        tracking.save_record(record)

    git_ops.delete_backup_ref(cwd=worktree_path)

    result = {
        **base, "success": True, "state": "open",
        "branch": feature_branch, "remote": publish_remote,
        "base_sha": base_sha, "head_sha": head_sha, "patch_id": patch_id,
        "provider": prcfg.provider, "default_branch": repo.default_branch,
        "repo": (target_pr.repo if target_pr else default_pr_repo),
        "pr_count": len(record.prs) if record else 0,
        "draft": want_draft,
    }
    if fork_owner:
        result["pr_head"] = f"{fork_owner}:{feature_branch}"
    if reusing:
        # This call iterated an existing *live* PR (re-squash + force-push onto
        # the reused head) rather than opening a fresh one -- flag it so callers
        # recognize the idempotent re-run and don't treat it as a new PR. Mirrors
        # the fast-path re-run signal in ``_push_existing_feature``.
        result["rerun"] = True

    # 8. Auto-open the PR via the configured provider plugin (Phase 2/3):
    #    open the PR, optionally embed the source-worktree attribution marker
    #    when the repo opts in, and auto-record the url/number on the worktree.
    #    Non-fatal on failure --
    #    the branch is already pushed, so the agent can fall back to a manual
    #    provider sub-agent + set-pr. If the target PR is already open on the
    #    provider, its number/url is surfaced (never re-created) so the caller
    #    does not open a duplicate.
    _finish_auto_open(
        result, config, record, target_pr, title=eff_title, body=body,
        worktree_id=worktree_id, head_sha=head_sha, open_pr=open_pr,
        draft=want_draft, attribution=attribution,
    )
    observation_error = refresh_head_observation(
        config, record, target_pr, head_sha
    )
    if observation_error:
        result["pr_head_observation_error"] = observation_error

    if topic_note:
        result["topic_note"] = topic_note
    return result


def self_merge_bypass_note(
    flow, provider, repo: str, number: int | None, *,
    api_base: str = "", token: str | None = None,
) -> str | None:
    """Explain a pr-self-merge repo's live self-merge availability, or None.

    Answers the question an agent otherwise has to infer by hand from a raw
    ``eligible: false`` / ``reason: "not yet approved"`` verdict: on a
    ``pr-self-merge`` repo, "no verdict yet" does NOT necessarily mean merge
    is blocked -- the acting identity may hold **Maintainer bypass rights**
    on the repo's own required-review rule (discovered landing a live
    ruleset requiring one approving review while granting the acting
    Maintainer bypass rights; see :meth:`GitHubProvider.pull_review_gate`).
    Surfacing this explicitly at both ``pr-status`` and ``create-pr``
    matters because agents have been observed hesitating or declaring "I
    can't self-merge" here, even as a Maintainer (#3296 follow-up).

    Only ever returns a note for the ``pr-self-merge`` profile, only when a
    provider exposes ``pull_review_gate`` (currently GitHub only, via
    ``hasattr`` -- other providers/flows are silently unaffected), and only
    when that live read confirms BOTH a required review AND actor
    bypassability -- never a guess. ``number is None`` (no PR yet) or any
    read failure returns ``None``, matching :func:`_live_pr_state`'s existing
    "omit rather than guess" contract.
    """
    from . import pr_contract as pc

    if flow.profile != pc.PROFILE_PR_SELF_MERGE:
        return None
    if number is None or not hasattr(provider, "pull_review_gate"):
        return None
    try:
        review_required, bypassable = provider.pull_review_gate(
            repo, number, api_base=api_base, token=token,
        )
    except Exception:
        return None
    if not (review_required and bypassable):
        return None
    return (
        "No verdict yet, but this identity holds Maintainer bypass rights on "
        "this repo's required-review rule -- `pr-merge <#> --now` self-merges "
        "directly rather than waiting for a review that may never come."
    )


def _open_via_provider(
    result: dict,
    config: Config,
    record: tracking.WorktreeRecord | None,
    target_pr: PRRecord,
    title: str,
    body: str | None,
    worktree_id: str,
    head_sha: str,
    *,
    draft: bool = False,
    attribution: SourceAttribution = False,
) -> None:
    """Open the PR through the provider plugin and auto-record it (best-effort)."""
    from . import providers
    from .providers import attribution as attr

    prcfg = config.default_repo.pr
    machine = record.machine if record else ""
    session = ""
    if record and record.sessions:
        live = [s for s in record.sessions if not s.ended_at]
        session = (live[-1] if live else record.sessions[-1]).session_id

    if attribution == "codename":
        # Public-safe mode: only the assigned codename, never the raw
        # worktree/machine/session identifiers -- and never a silent
        # downgrade to the full marker if the codename is missing or
        # malformed. Tracking records load from YAML without validation, so
        # a tampered/corrupted codename must never be interpolated into the
        # HTML comment as-is (it could break the marker or inject visible
        # PR-body content) -- `is_valid_handle` gates it the same as the
        # missing-codename case.
        #
        # A pre-Phase-2 (or newly-migrated) record may genuinely have NO
        # codename yet if it was never touched by `resolve`/`resume`/
        # `status --write` (each of which lazily backfills one). Backfill it
        # here too, on first use by `create-pr` itself, rather than silently
        # skipping the marker on this PR -- the same `ensure_codename`
        # first-touch path those other verbs use. Deliberately narrower than
        # "any invalid codename": only a genuinely MISSING (falsy) codename
        # is backfilled -- a present-but-MALFORMED one (tampered/corrupted
        # data) is never auto-regenerated/overwritten here, preserving the
        # existing skip-the-marker safety behavior for that case.
        from . import codename as codename_mod
        from . import codename_tracking
        codename = record.codename if record else None
        if record is not None and not codename:
            # `ensure_codename` mutates `record` in place and returns that
            # same object (never a different/reloaded one), so this is
            # simply "backfill record.codename, then re-read it" -- no
            # object-identity concern with `target_pr` (a separate
            # parameter this function mutates directly and appends onto
            # `record.prs` upstream).
            #
            # Best-effort: `ensure_codename` can raise (notably
            # `TimeoutError` if its cross-process allocation lock can't be
            # acquired in time). This whole path is opening a PR -- a
            # codename-backfill failure must degrade to "no marker on this
            # PR" (the pre-existing skip behavior), never crash the
            # provider-open flow and abort the PR entirely. A
            # `CodenameAttributionPolicyError` is the one exception NEVER
            # swallowed here (round-16 finding) -- re-raise it so `create_pr`
            # aborts outright rather than silently publishing no marker for
            # exactly the custom-wordlist/unconfigured-`source_attribution`
            # repo this policy exists to block. `create_pr` itself runs the
            # matching PREFLIGHT (round-22 finding) before any squash/push,
            # so this re-raise is a defense-in-depth backstop for the
            # narrow TOCTOU window between that preflight and this actual
            # backfill, not the primary enforcement mechanism.
            prcfg = config.default_repo.pr
            try:
                codename_tracking.ensure_codename(
                    record, cfg.tracking_dir(),
                    codename_tracking.wordlist_for_repo(config),
                    codename_source=codename_tracking.classify_codename_source(
                        config.default_repo.codename
                    ),
                    pr_enabled=prcfg.enabled,
                    source_attribution_configured=prcfg.source_attribution_configured,
                )
            except codename_tracking.CodenameAttributionPolicyError:
                raise
            except Exception:
                pass
            codename = record.codename
        marker_published = bool(
            isinstance(codename, str)
            and codename_mod.is_valid_handle(codename)
            and attr.may_publish_codename(
                codename_source=(record.codename_source if record else None),
                source_attribution_configured=target_pr.attribution_explicit,
            )
        )
        full_body = (
            attr.append_marker(body or "", attr.build_codename_marker(codename))
            if marker_published
            # Never leave a stale/caller-supplied source marker in place when
            # publication is skipped -- it could still carry raw identifiers
            # from some other source (a copy-pasted body, an older template).
            else attr.strip_marker(body or "")
        )
    elif attribution is True:
        marker = attr.build_marker(
            worktree_id, machine=machine, session=session, head=head_sha,
        )
        full_body = attr.append_marker(body or "", marker)
        marker_published = True
    else:
        # Attribution disabled entirely -- strip any marker that might
        # already be present in the caller-supplied body for the same
        # "never leave a stale one in place" reason.
        full_body = attr.strip_marker(body or "")
        marker_published = False
    scope = providers.scope_from_create_result(
        result, title=title, body=full_body, prcfg=prcfg, machine=machine,
    )
    if draft:
        scope.draft = True
    try:
        provider = providers.get_provider(prcfg.provider)
        token = providers.account_token_for_slug(scope.repo, prcfg)
        pull = provider.create_pull(scope, token=token)
    except (providers.ProviderError, OSError) as e:
        # A provider failure (or a spawn error that slipped past run_cli) must
        # degrade to a recorded pr_open_error, never crash create-pr -- the
        # feature branch is already pushed, so the agent can open the PR
        # manually from the surfaced error.
        result["pr_open_error"] = str(e)
        result["pr_opened"] = False
        result["draft"] = False  # nothing opened -> no draft was created
        return

    target_pr.url = pull.url
    target_pr.number = pull.number
    if pull.state:
        target_pr.state = pull.state
    if record is not None:
        # #1029 backfill: if the worktree never recorded an originating session
        # (e.g. created before this field existed), stamp the session that
        # produced this PR -- but never clobber an explicit one.
        if not record.parent_session and session:
            record.parent_session = session
        if marker_published:
            target_pr.attribution_head = head_sha
        tracking.save_record(record)
    result["pr_opened"] = True
    result["url"] = pull.url
    result["number"] = pull.number
    result["state"] = pull.state or result.get("state")
    # Reflect what THIS call actually did: a draft was created only when we asked
    # the provider to open one. (The caller pre-seeds result["draft"] with the
    # request intent for the dry-run/no-open paths; here we make it authoritative
    # for the opened PR so "opened as a DRAFT" can never be reported falsely.)
    result["draft"] = bool(draft)
    # The PR opened, but a required label (auto-merge / source:<machine>) may
    # have failed to apply. Surface it rather than swallowing -- the merge gate
    # and source attribution depend on these labels.
    if getattr(pull, "label_error", ""):
        result["pr_label_error"] = pull.label_error

    # Draft PRs never carry a live review verdict worth checking -- skip the
    # read rather than reporting a note for a state the PR isn't even in yet.
    if not draft:
        from . import pr_config

        flow = pr_config._pr_flow_profile(config.default_repo)
        note = self_merge_bypass_note(
            flow, provider, scope.repo, pull.number,
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
        if note:
            result["self_merge_note"] = note


def refresh_source_attribution(
    worktree_id: str,
    config: Config,
    record: tracking.WorktreeRecord,
    target_pr: PRRecord | None,
    head_sha: str,
) -> str:
    """Publish the pushed head as a dedicated managed attribution comment."""
    if target_pr is None:
        return "active PR has no provider repo/number"
    prcfg = config.default_repo.pr
    # codename-attribution-by-default (round-32 finding, corrected round-37,
    # sharpened by a PR #3037 review finding): a legacy PRRecord predating
    # the frozen attribution_mode/attribution_explicit fields is lazily
    # frozen on this, its FIRST post-migration touch -- computed from
    # whatever config is live AT THIS SINGLE MOMENT, persisted immediately.
    # This runs BEFORE any decision that depends on live config OR on this
    # PR's own metadata completeness (including the number/repo validation
    # below) -- a legacy PR with missing provider metadata (e.g. before
    # `set-pr` has supplied it) must still be frozen on this touch; gating
    # the freeze on that validation would let it stay unmigrated until
    # metadata arrives, at which point THAT later touch (not the true
    # first one) would be frozen instead, silently adopting whatever
    # policy is live by then. The number/repo check below gates
    # PUBLICATION only, never the freeze itself.
    if not target_pr.attribution_mode:
        tracking.stamp_frozen_attribution(
            target_pr, attribution=prcfg.source_attribution,
            explicit=prcfg.source_attribution_configured,
            # This entry is an EXISTING on-disk record touched outside the
            # record lock -- assign_pr_id=False defers pr_id minting
            # entirely to _save_record_unlocked's own inline, lock-
            # serialized backfill, so two concurrent legacy-freeze calls
            # for the same PR can never mint two different random ids
            # (fix-PR-#3037-review finding).
            assign_pr_id=False,
        )
        tracking.save_record(record)
    if target_pr.number is None or not target_pr.repo:
        return "active PR has no provider repo/number"
    if target_pr.attribution_head == head_sha:
        return ""
    from . import providers
    from .providers import attribution

    provider_name = target_pr.provider or prcfg.provider
    if provider_name != prcfg.provider:
        return (
            f"tracked PR provider {provider_name!r} differs from configured "
            f"provider {prcfg.provider!r}; credentials cannot be resolved safely"
        )
    session = ""
    if record.sessions:
        live = [item for item in record.sessions if not item.ended_at]
        session = (live[-1] if live else record.sessions[-1]).session_id
    # From here on, every publish decision uses the FROZEN pair, never live
    # `prcfg.source_attribution`/`source_attribution_configured` -- the live
    # config was consulted only once, above, at the freeze moment.
    if target_pr.attribution_mode == "codename":
        # Public-safe mode -- see `_open_via_provider`'s matching branch for
        # the same "skip, never downgrade to the raw marker" rule, and for
        # why the codename is validated (not just checked for presence)
        # before being interpolated into the HTML comment.
        from . import codename as codename_mod
        if not isinstance(record.codename, str) or not codename_mod.is_valid_handle(
            record.codename
        ):
            return ""
        if not attribution.may_publish_codename(
            codename_source=record.codename_source,
            source_attribution_configured=target_pr.attribution_explicit,
        ):
            return ""
        marker = attribution.build_codename_marker(record.codename)
    elif target_pr.attribution_mode == "true":
        marker = attribution.build_marker(
            worktree_id,
            machine=record.machine,
            session=session,
            head=head_sha,
        )
    else:
        # "false" (or an unrecognized value migrated to the empty sentinel
        # above) -- never publish the raw marker for anything other than a
        # frozen "true".
        return ""
    try:
        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_pr.repo, prcfg)
        error = provider.publish_source_marker(
            target_pr.repo,
            int(target_pr.number),
            marker,
            api_base=prcfg.api_base,
            token=token,
        )
        if not error:
            target_pr.attribution_head = head_sha
            tracking.save_record(record)
        return error
    except (providers.ProviderError, OSError, ValueError) as exc:
        return str(exc)


def refresh_head_observation(
    config: Config,
    record: tracking.WorktreeRecord | None,
    target_pr: PRRecord | None,
    head_sha: str,
) -> str:
    """Record provider-clock evidence that the exact pushed head is live."""
    if record is None or target_pr is None:
        return "worktree has no tracked PR record"
    if target_pr.number is None or not target_pr.repo:
        return "active PR has no provider repo/number"

    from . import providers

    prcfg = config.default_repo.pr
    provider_name = target_pr.provider or prcfg.provider
    if provider_name != prcfg.provider:
        return (
            f"tracked PR provider {provider_name!r} differs from configured "
            f"provider {prcfg.provider!r}; credentials cannot be resolved safely"
        )
    identity = (
        provider_name,
        target_pr.repo,
        int(target_pr.number),
        target_pr.branch,
    )
    observed_api_base = ""
    yaml_path = record.yaml_path

    def _matching_pr(current: tracking.WorktreeRecord) -> PRRecord | None:
        for candidate in current.prs:
            candidate_provider = candidate.provider or prcfg.provider
            if (
                candidate_provider,
                candidate.repo,
                candidate.number,
                candidate.branch,
            ) == identity:
                return candidate
        return None

    with tracking._RecordLock(yaml_path):
        current = tracking.load_record(yaml_path)
        current_pr = _matching_pr(current)
        if current_pr is None:
            return "tracked PR identity changed before provider observation"
        current_pr.head_sha = head_sha
        current_pr.head_observed_at = ""
        current_pr.head_observed_api_base = ""
        tracking.save_record(current)
    target_pr.head_sha = head_sha
    target_pr.head_observed_at = ""
    target_pr.head_observed_api_base = ""

    try:
        provider = providers.get_provider(provider_name)
        observed_api_base = provider.authority_endpoint(prcfg.api_base)
        token = providers.account_token_for_slug(target_pr.repo, prcfg)
        observed = provider.observe_head(
            target_pr.repo,
            int(target_pr.number),
            api_base=prcfg.api_base,
            token=token,
        )
    except (providers.ProviderError, OSError, ValueError, AttributeError) as exc:
        return str(exc)
    if observed.head_sha != head_sha:
        return (
            f"provider observed head {observed.head_sha or '(missing)'} instead "
            f"of pushed head {head_sha}"
        )
    if not observed.observed_at:
        return "provider observation had no server timestamp"
    with tracking._RecordLock(yaml_path):
        current = tracking.load_record(yaml_path)
        current_pr = _matching_pr(current)
        if current_pr is None or current_pr.head_sha != head_sha:
            return "tracked PR identity or head changed during provider observation"
        current_pr.head_observed_at = observed.observed_at
        current_pr.head_observed_api_base = observed_api_base
        tracking.save_record(current)
    target_pr.head_observed_at = observed.observed_at
    target_pr.head_observed_api_base = observed_api_base
    return ""


def _finish_auto_open(
    result: dict,
    config: Config,
    record: tracking.WorktreeRecord | None,
    target_pr: PRRecord | None,
    *,
    title: str,
    body: str | None,
    worktree_id: str,
    head_sha: str,
    open_pr: bool | None,
    draft: bool,
    attribution: SourceAttribution | None,
) -> None:
    """Open the PR (when pending) or surface an already-open PR's number/url.

    Shared by the first-run and the re-run paths so neither silently leaves a
    pushed branch without reporting its PR:

    * ``open_pr``/``pr.auto_open`` off -> no-op (manual flow).
    * target PR has no number yet      -> open it via the provider.
    * target PR already opened          -> surface its number/url on ``result``
                                           (never re-create -> no duplicate, #1167).
    """
    prcfg = config.default_repo.pr
    want_open = prcfg.auto_open if open_pr is None else open_pr
    if not want_open or target_pr is None:
        return
    if target_pr.number is None:
        # codename-attribution-by-default (fix-PR-#3037-review finding):
        # use the FROZEN pair for the initial-open decision too, not a
        # live-recomputed value -- a PR frozen on an earlier
        # create-pr --no-open/--no-attribution run (never yet opened, so
        # number is still None) must open under its ORIGINAL frozen
        # policy even if live config has since changed. Lazily freeze an
        # empty legacy pair (a target_pr that somehow reached this point
        # unstamped) before opening, from whatever is live right now --
        # the same one-time freeze-on-first-touch pattern
        # refresh_source_attribution uses.
        if not target_pr.attribution_mode:
            _want_attribution = (
                prcfg.source_attribution if attribution is None else attribution
            )
            tracking.stamp_frozen_attribution(
                target_pr, attribution=_want_attribution,
                explicit=(
                    attribution is not None
                    or prcfg.source_attribution_configured
                ),
                assign_pr_id=False,
            )
            if record is not None:
                tracking.save_record(record)
        want_attribution = tracking.attribution_from_frozen_mode(target_pr)
        _open_via_provider(
            result, config, record, target_pr, title, body, worktree_id,
            head_sha, draft=draft, attribution=want_attribution,
        )
        return
    # The PR is already open on the provider -- report it so the caller trusts
    # create-pr's result and does not open a second PR for the same branch.
    result["pr_opened"] = True
    result["number"] = target_pr.number
    if target_pr.url:
        result["url"] = target_pr.url
    if target_pr.state:
        result["state"] = target_pr.state
    # This call did not open a PR, so it created no draft -- never let a
    # re-run's ``--draft`` request masquerade as "opened as a DRAFT". Un-drafting
    # an already-open PR is pr-ready's job, not create-pr's.
    result["draft"] = False
    # codename-attribution-by-default (round-38 finding): always invoke
    # refresh_source_attribution when a record exists -- never gate the
    # call itself on a LIVE `want_attribution` snapshot. A PR frozen under
    # `attribution_mode="true"` whose repo's live `source_attribution`
    # later changes to `false` must still keep publishing per its ORIGINAL
    # frozen state; gating this call on live config would skip it entirely
    # and never give the frozen-pair logic INSIDE refresh_source_attribution
    # a chance to run. This is cheap when there's nothing to do:
    # refresh_source_attribution already short-circuits immediately once
    # `target_pr.attribution_head == head_sha` (no new push to react to).
    if record is not None:
        error = refresh_source_attribution(
            worktree_id,
            config,
            record,
            target_pr,
            head_sha,
        )
        if error:
            result["pr_attribution_error"] = error


def _reconcile_active_pr(
    record: tracking.WorktreeRecord | None,
    config: Config,
    *,
    best_effort: bool = False,
) -> None:
    """Refresh the active PR's state from the provider (best-effort).

    A PR merged or closed *externally* (e.g. via the Gitea API + the
    ``auto-merge`` label, bypassing ``finalize``/``pr-watch``) leaves the local
    record stale at ``open``.  Branch selection in :func:`create_pr` would then
    reuse and force-push that already-merged branch and open no new PR (#1163).
    Querying the provider and writing back a terminal state makes the active PR
    correctly *terminal* so the append-a-fresh-PR path is taken instead.

    Also self-heals a **zombie open PR** (#1375/#1703): when the provider still
    reports the PR open but its head is already *contained in the base branch*
    (its content merged, but the PR object was never flipped -- a Gitea
    non-atomic merge under load, or an AI-reviewer squash that didn't close the
    object), reconcile it to ``merged`` so the Picker stops showing a phantom
    open PR. Only a *definite* containment (0 commits ahead) heals; an unknown
    result leaves the state untouched.

    ``best_effort`` (set by the Picker's background reconcile sweep) persists the
    write under a NON-blocking record lock and **skips** on contention -- so an
    inconsequential sweep never blocks, nor is blocked by, a critical updater
    (#4547). The state transition is monotonic (open -> terminal), so a skipped
    persist is simply re-applied on the next sweep. Foreground callers use the
    default blocking persist.

    No-op (falls back to the local state) when there is no active PR, it has no
    number yet, it is already terminal, or the provider is unconfigured/
    unreachable.
    """
    if record is None:
        return
    active = record.active_pr()
    if active is None or active.number is None:
        return
    if tracking._pr_is_terminal(active):
        return
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    api_base = getattr(prcfg, "api_base", "") or ""
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, prcfg)
        pull = provider.get_pull(
            target_repo, active.number, api_base=api_base, token=token,
        )
    except Exception:
        # Provider unconfigured/unreachable -- keep the local state rather than
        # guessing.  (Conservative: an unverifiable open PR is still iterated.)
        return
    state = (pull.state or "").strip().lower()
    # ``merged`` is authoritative: a squash-merged PR reports state="closed" on
    # some providers, so prefer it -- the record should say the work actually
    # *landed* (not merely "closed"), which is what drives the post-merge
    # pull-forward recommendation in :func:`pr_status`.
    if pull.merged:
        resolved = "merged"
    elif state and state not in tracking._PR_NON_TERMINAL:
        resolved = state
    else:
        resolved = ""

    # Zombie self-heal (#1375/#1703): the provider says non-terminal, but if the
    # PR's head is already contained in the base branch its content has merged --
    # heal to ``merged``. Definite-only: an unknown/False result leaves it open.
    if not resolved and pull.head_sha and pull.base_ref:
        try:
            contained = provider.head_contained_in_base(
                target_repo, pull.base_ref, pull.head_sha,
                api_base=api_base, token=token,
            )
        except Exception:
            contained = None
        if contained is True:
            resolved = "merged"

    if resolved:
        active.state = resolved
        if not active.closed_at:
            active.closed_at = tracking._now_iso()
        if best_effort:
            # Skip the persist on contention -- the monotonic transition is
            # re-applied next sweep; never block a critical updater.
            with tracking._RecordLock(record.yaml_path, blocking=False) as lk:
                if lk.acquired:
                    tracking.save_record(record)
        else:
            tracking.save_record(record)


def _live_pr_state(
    record: tracking.WorktreeRecord | None,
    active: PRRecord | None,
    config: Config,
) -> dict | None:
    """Best-effort live verdict/conflict/merge read for the active PR.

    Folds ``pr-watch``'s snapshot + the shared classifier into ``pr-status`` so
    one command answers "where is my PR?" -- the review **verdict**, whether it
    has a **conflict**, its **merge state**, and whether merge **consent** is
    present/eligible -- alongside the tracked metadata.  Returns a ``{"live":
    {...}}`` dict, or ``None`` when there is no numbered active PR or the
    provider is unconfigured/unreachable (never fatal: pr-status still reports
    the tracked state).
    """
    if active is None or active.number is None:
        return None
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or ((record.repo if record else "") or "")
    try:
        from . import pr_contract as pc
        from . import providers

        provider = providers.get_provider(provider_name)
        authority_endpoint = provider.authority_endpoint(
            getattr(prcfg, "api_base", "") or ""
        )
        token = providers.account_token_for_slug(target_repo, prcfg)
        snap = provider.get_snapshot(
            target_repo, active.number,
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        # Provider unconfigured/unreachable/unsupported -- omit the live block
        # rather than guessing; the tracked state is still reported.
        return None
    evidence_matches_endpoint = (
        active.head_observed_api_base == authority_endpoint
    )
    st = pc.classify_state(
        snap,
        automerge_label=getattr(prcfg, "automerge_label", "") or "",
        hold_labels=tuple(getattr(prcfg, "hold_labels", ()) or ()),
        wip_title_prefixes=tuple(getattr(prcfg, "wip_title_prefixes", ()) or ()),
        approval_required=bool(getattr(prcfg, "approval_required", True)),
        allow_stale_approval=bool(getattr(prcfg, "allow_stale_approval", False)),
        stale_approval_head_sha=(
            active.head_sha if evidence_matches_endpoint else ""
        ),
        stale_approval_head_observed_at=(
            active.head_observed_at if evidence_matches_endpoint else ""
        ),
        review_blocking=bool(getattr(prcfg, "review_blocking", False)),
    )
    self_merge_note = None
    if st.merge_state not in ("merged", "closed") and not st.wip and not st.held:
        from . import pr_config

        flow = pr_config._pr_flow_profile(config.default_repo)
        self_merge_note = self_merge_bypass_note(
            flow, provider, target_repo, active.number,
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    live: dict = {
        "verdict": st.verdict,
        "approval_stale": st.approval_stale,
        "approval_stale_authorized": st.approval_stale_authorized,
        "merge_state": st.merge_state,
        "conflict": st.conflict,
        "mergeable": snap.mergeable,
        "consent_present": st.consent_present,
        "consent_action": st.consent_action,
        "eligible": st.eligible,
        "held": list(st.held),
        "wip": st.wip,
        "reviews": len(snap.reviews),
        "reason": st.reason,
    }
    if self_merge_note:
        live["self_merge_note"] = self_merge_note
    return {"live": live}


def _load_record_or_none(worktree_id: str) -> tracking.WorktreeRecord | None:
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return None
    try:
        return tracking.load_record(yaml_path)
    except Exception:
        return None


_VALID_PR_STATES = ("creating", "open", "merged", "closed")


def set_pr(
    worktree_id: str,
    *,
    url: str | None = None,
    number: int | None = None,
    state: str | None = None,
    provider: str | None = None,
    branch: str | None = None,
    select_number: int | None = None,
    select_branch: str | None = None,
    config: Config | None = None,
) -> dict:
    """Record PR metadata (URL/number/state/provider) on a worktree record.

    Called by the agent after a provider sub-agent creates the PR.  Updates
    the **active** PR by default, or a specific one selected by ``--pr`` /
    ``--branch``, so create-pr's branch/base/head SHAs are preserved.  When a
    state transition reaches a terminal state, ``closed_at`` is stamped.
    """
    base: dict = {"success": False, "worktree_id": worktree_id}

    if state is not None and state not in _VALID_PR_STATES:
        return {**base, "error": (
            f"Invalid PR state '{state}'. Expected one of: "
            f"{', '.join(_VALID_PR_STATES)}."
        )}

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return {**base, "error": f"No tracking record found for '{worktree_id}'."}

    # Foreground verb (#4547): a critical read-modify-write. Hold the blocking
    # cross-process record lock across the whole load -> mutate -> save so a
    # concurrent writer (another CLI verb, or a Picker best-effort sweep) can't
    # clobber this update last-writer-wins. The window below contains NO
    # network/git I/O, so the lock is held only briefly -- the guarantee the
    # criticality-aware lock relies on.
    with tracking._RecordLock(yaml_path):
        return _set_pr_locked(
            base, yaml_path, url=url, number=number, state=state,
            provider=provider, branch=branch,
            select_number=select_number, select_branch=select_branch,
            config=config,
        )


def _set_pr_locked(
    base: dict,
    yaml_path: Path,
    *,
    url: str | None,
    number: int | None,
    state: str | None,
    provider: str | None,
    branch: str | None,
    select_number: int | None,
    select_branch: str | None,
    config: Config | None = None,
) -> dict:
    """The load -> mutate -> save body of :func:`set_pr`, run under the record
    lock. Split out so the lock scope is exactly the RMW window."""
    worktree_id = base["worktree_id"]
    try:
        record = tracking.load_record(yaml_path)
    except Exception:
        return {**base, "error": f"No tracking record found for '{worktree_id}'."}

    # Select which PR to update: explicit selector > active > new.
    pr: PRRecord | None
    if select_number is not None:
        pr = next((p for p in record.prs if p.number == select_number), None)
        if pr is None:
            return {**base, "error": (
                f"No tracked PR #{select_number} for '{worktree_id}'."
            )}
    elif select_branch is not None:
        pr = next((p for p in record.prs if p.branch == select_branch), None)
        if pr is None:
            return {**base, "error": (
                f"No tracked PR on branch '{select_branch}' for '{worktree_id}'."
            )}
    else:
        pr = record.active_pr()
        if pr is None:
            pr = PRRecord()
            record.prs.append(pr)
            # codename-attribution-by-default: freeze this PR's attribution
            # decision ONCE, at this fresh-construction site -- manual
            # set-pr has no per-call override, so the effective value is
            # simply whatever is live in config AT THIS MOMENT (best-effort:
            # a config-load failure degrades to the safe "false" sentinel,
            # never crashes this RMW). Use the CALLER's already-resolved
            # config when supplied (a PR #3037 review finding: `cmd_set_pr`
            # resolves config honoring `--config`/project context, but this
            # freeze previously re-loaded AMBIENT config with neither --
            # from a neutral CWD, or when `--config` targets a different
            # project, the PR would be frozen under the wrong repo's
            # policy) -- only fall back to an ambient load for a caller
            # that genuinely has none to offer (e.g. a bare unit test of
            # this function).
            try:
                prcfg_for_stamp = (
                    config.default_repo.pr if config is not None
                    else cfg.load_config().default_repo.pr
                )
                tracking.stamp_frozen_attribution(
                    pr, attribution=prcfg_for_stamp.source_attribution,
                    explicit=prcfg_for_stamp.source_attribution_configured,
                )
            except Exception:
                tracking.stamp_frozen_attribution(
                    pr, attribution=False, explicit=False,
                )

    # PR #3037 review finding: backfill and PERSIST this entry's pr_id
    # BEFORE applying any branch/number correction below (see
    # tracking.ensure_pr_id's docstring for why this must be its own save,
    # not folded into the final one below).
    if tracking.ensure_pr_id(pr):
        tracking.save_record(record)

    identity_changed = (
        (number is not None and number != pr.number)
        or (provider is not None and provider != pr.provider)
    )
    if url is not None:
        pr.url = url
    if number is not None:
        pr.number = number
    if provider is not None:
        pr.provider = provider
    if identity_changed:
        pr.attribution_head = ""
        pr.head_observed_at = ""
        pr.head_observed_api_base = ""
    if branch is not None:
        pr.branch = branch
    if state is not None:
        pr.state = state
    elif not pr.state:
        # First time recording metadata with no explicit state -> open.
        pr.state = "open"
    if not pr.opened_at:
        pr.opened_at = tracking._now_iso()
    if tracking._pr_is_terminal(pr) and not pr.closed_at:
        pr.closed_at = tracking._now_iso()

    tracking.save_record(record)
    return {**base, "success": True, **_pr_to_dict(pr)}


def pr_ready(
    worktree_id: str,
    config: Config,
    *,
    target_repo: str | None = None,
    pr_number: int | None = None,
) -> dict:
    """Move a PR out of draft (draft -> ready-for-review).

    ``pr-ready`` is an **un-draft** verb: it clears the native draft state (a
    WIP-prefixed title on Gitea) so the PR becomes reviewable.  It does NOT grant
    merge consent -- that is ``pr-merge``'s separate job.

    Errors (``success: False``) when the action does not apply to the PR's
    current state, so a no-op never masquerades as success:

    * the PR is not in draft (and carries no legacy hold label) -> error;
    * the un-draft provider call fails -> error.

    Backward-compat: a PR opened under the retired ``--hold`` model carries the
    legacy ``do-not-merge`` label instead of draft state.  If such a PR is not a
    draft but does carry that hold label, it is removed (the equivalent
    transition) and reported as a legacy-hold release.
    """
    base: dict = {"success": False, "worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "error": f"No tracking record found for '{worktree_id}'."}

    _reconcile_active_pr(record, config)
    if pr_number is not None:
        pr = next((p for p in record.prs if p.number == pr_number), None)
        if pr is None:
            return {**base, "error": (
                f"No tracked PR #{pr_number} for '{worktree_id}'."
            )}
    else:
        pr = record.active_pr()
        if pr is None:
            return {**base, "error": f"No tracked PR for '{worktree_id}'."}

    if pr.number is None:
        return {**base, "error": (
            f"Tracked PR for '{worktree_id}' has no PR number recorded."
        )}

    prcfg = config.default_repo.pr
    provider_name = pr.provider or prcfg.provider
    repo = target_repo or pr.repo or record.repo or ""
    if not repo:
        return {**base, "error": (
            f"Tracked PR #{pr.number} for '{worktree_id}' has no target repo."
        )}

    api_base = getattr(prcfg, "api_base", "") or ""
    wip_prefixes = tuple(getattr(prcfg, "wip_title_prefixes", ()) or ())

    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(repo, prcfg)
        snap = provider.get_snapshot(
            repo, pr.number, api_base=api_base, token=token,
        )
    except Exception as exc:
        return {**base, **_pr_to_dict(pr), "repo": repo,
                "provider": provider_name, "error": str(exc)}

    common = {
        **base, **_pr_to_dict(pr), "repo": repo, "provider": provider_name,
    }

    if snap.draft:
        # The intended transition: strip the WIP prefix (un-draft).
        try:
            err = provider.mark_ready(
                repo, pr.number, api_base=api_base, token=token,
                title=snap.title, wip_title_prefixes=wip_prefixes,
            )
        except Exception as exc:
            return {**common, "error": str(exc)}
        if err:
            return {**common, "error": err}
        return {
            **common, "success": True, "transition": "undraft",
            "was_draft": True,
        }

    # Not a draft. Backward-compat: a PR opened under the retired --hold model
    # carries the legacy do-not-merge hold label; releasing it is the equivalent
    # transition. Otherwise this verb does not apply -> error (no false success).
    has_legacy_hold = any(
        lbl.lower() == HOLD_LABEL for lbl in snap.labels
    )
    if has_legacy_hold:
        try:
            label_error = provider.remove_label(
                repo, pr.number, HOLD_LABEL, api_base=api_base, token=token,
            )
        except Exception as exc:
            return {**common, "error": str(exc)}
        if label_error:
            return {**common, "error": label_error, "label_error": label_error}
        return {
            **common, "success": True, "transition": "release-legacy-hold",
            "removed": True, "label": HOLD_LABEL,
        }

    return {
        **common,
        "error": (
            f"PR #{pr.number} in {repo} is not in draft state (and carries no "
            f"legacy hold label); nothing to un-draft. pr-ready only moves a "
            f"draft PR to ready-for-review -- it does not grant merge consent "
            f"(use pr-merge for that)."
        ),
    }


def pr_status(worktree_id: str, *, all_prs: bool = False,
              live: bool = True, config: Config | None = None) -> dict:
    """Return the tracked PR metadata for a worktree (for pr-status).

    Returns the **active** PR by default.  With ``all_prs`` the full ``prs``
    history is included alongside the active one.  ``pr_count`` is always
    present so the orphan-detection probe can key on existence.

    Before reporting, the active PR is reconciled against the provider so a PR
    merged or closed *externally* (e.g. via the ``auto-merge`` label, bypassing
    ``finalize``/``pr-watch``) is reported with its true terminal state rather
    than a stale ``open``.  This is the agent's authoritative "did my PR land?"
    check; when it lands, the result also carries a **pull-forward
    recommendation** (``pull_forward_recommended`` + ``next_action``) directing
    the standard post-merge move -- ``agent-worktrees git sync`` to rebase the
    worktree onto the updated default branch.

    With ``live`` (default), a best-effort ``live`` block is added for the
    active PR carrying its review **verdict**, **conflict**, **merge state**, and
    merge-**consent** eligibility (from the shared ``pr_contract`` classifier),
    so one command answers "where is my PR?".  The block is omitted silently when
    the provider is unconfigured/unreachable.
    """
    base: dict = {"worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "has_pr": False, "pr_count": 0,
                "error": f"No tracking record found for '{worktree_id}'."}
    if config is None:
        config = cfg.load_config()
    _reconcile_active_pr(record, config)
    active = record.active_pr()
    result = {**base, "has_pr": active is not None, "pr_count": len(record.prs)}
    if active is not None:
        result.update(_pr_to_dict(active))
        rec = _pull_forward_recommendation(record, active, config)
        if rec:
            result.update(rec)
        if live:
            live_block = _live_pr_state(record, active, config)
            if live_block:
                result.update(live_block)
    if all_prs:
        result["prs"] = [_pr_to_dict(p) for p in record.prs]
    return result


def pr_threads(
    worktree_id: str,
    *,
    resolve: bool = False,
    config: Config | None = None,
) -> dict:
    """Read (and optionally resolve) the active PR's review comment threads.

    First-class comment-threading tied to the worktree flow: resolves the
    active PR's provider/repo, lists its threads via ``get_comment_threads``,
    and -- with ``resolve`` -- marks the active (unresolved) ones resolved
    (``resolve_threads``). Returns ``{has_pr, threads: [...], active_count, ...}``
    (never fatal: an unsupported/unreachable provider yields ``supported:
    False`` with a ``reason``).
    """
    base: dict = {"worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "has_pr": False,
                "error": f"No tracking record found for '{worktree_id}'."}
    if config is None:
        config = cfg.load_config()
    active = record.active_pr()
    if active is None or active.number is None:
        return {**base, "has_pr": False, "threads": [], "active_count": 0}
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    api_base = getattr(prcfg, "api_base", "") or ""
    out: dict = {**base, "has_pr": True, "number": active.number, "repo": target_repo}
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, prcfg)
        listing = provider.get_comment_threads(
            target_repo, active.number, api_base=api_base, token=token,
        )
    except Exception as exc:
        return {**out, "supported": False, "reason": str(exc), "threads": [],
                "active_count": 0}
    out["supported"] = listing.supported
    if not listing.supported:
        out["reason"] = listing.error
        out["threads"] = []
        out["active_count"] = 0
        return out
    if listing.error:
        out["reason"] = listing.error
    out["threads"] = [
        {
            "id": t.id, "status": t.status, "file_path": t.file_path,
            "active": t.is_active,
            "comments": [{"author": c.author, "content": c.content}
                         for c in t.comments],
        }
        for t in listing.threads
    ]
    out["active_count"] = len(listing.active)
    if resolve and listing.active:
        try:
            err = provider.resolve_threads(
                target_repo, active.number, api_base=api_base, token=token,
            )
        except Exception as exc:
            err = str(exc)
        out["resolved"] = not err
        if err:
            out["resolve_error"] = err
    return out


def _pull_forward_recommendation(
    record: tracking.WorktreeRecord,
    active: PRRecord,
    config: Config,
) -> dict | None:
    """Recommend the post-merge pull-forward when the active PR has merged.

    Returns recommendation fields, or ``None`` when no nudge is warranted.
    Fires only when the active PR is **merged** and the worktree branch is not
    already rebased on top of the updated default branch -- i.e. there is real
    pull-forward work to do.  Best-effort and side-effect-free (a single
    upstream fetch aside): any git hiccup falls back to recommending, since the
    agent's ``git sync`` is a safe no-op when already current.
    """
    if active.state != "merged":
        return None
    path = record.worktree_path
    if not (path and Path(path).exists()):
        return None
    repo = config.default_repo
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"
    # Refresh the upstream ref so "behind" reflects the just-landed merge.
    if git_ops.has_remote(remote, cwd=path):
        try:
            git_ops.fetch(remote, cwd=path)
        except Exception:
            pass
        else:
            # worktree-finality-and-obligations Phase 9: a merged PR's
            # pull-forward check is exactly the "pr-merge" freshness trigger
            # -- share this fetch with every other worktree of the repo.
            if record.repo:
                tracking.record_repo_fetch_confirmed(record.repo)
    behind: int | None = None
    branch = git_ops._get_current_branch_safe(path)
    if branch and git_ops.ref_exists(upstream, cwd=path):
        out = git_ops.git(
            "rev-list", "--count", f"{branch}..{upstream}",
            cwd=path, check=False,
        ).stdout.strip()
        try:
            behind = int(out)
        except ValueError:
            behind = None
    # Already on top of the updated default branch -- nothing to pull forward.
    if behind == 0:
        return None
    rec: dict = {
        "pull_forward_recommended": True,
        "pull_forward_command": "agent-worktrees git sync",
    }
    if behind:
        rec["behind"] = behind
    if not git_ops.is_clean(cwd=path):
        rec["pull_forward_blocked"] = "dirty"
        rec["next_action"] = (
            f"Active PR #{active.number} is merged, but this worktree has "
            "uncommitted changes. Commit or stash them, then run "
            f"`agent-worktrees git sync` to pull forward (rebase onto {upstream})."
        )
    else:
        rec["next_action"] = (
            f"Active PR #{active.number} is merged. Pull this worktree forward: "
            f"`agent-worktrees git sync` (rebase onto {upstream}; the merged "
            "commits drop as already-applied)."
        )
    return rec


def _pr_to_dict(pr: PRRecord) -> dict:
    return {
        "state": pr.state,
        "branch": pr.branch,
        "base_sha": pr.base_sha,
        "head_sha": pr.head_sha,
        "patch_id": pr.patch_id,
        "url": pr.url,
        "number": pr.number,
        "provider": pr.provider,
        "repo": pr.repo,
        "opened_at": pr.opened_at,
        "closed_at": pr.closed_at,
    }


def _push_existing_feature(
    worktree_path: str,
    feature_branch: str,
    remote: str,
    repo,
    prcfg,
    record: tracking.WorktreeRecord | None,
    base: dict,
    *,
    config: Config,
    worktree_id: str,
    title: str,
    body: str | None,
    open_pr: bool | None,
    draft: bool,
    attribution: SourceAttribution | None,
    pr_head: str = "",
) -> dict:
    """Re-run helper: push an already-created feature branch and record state.

    Also completes auto-open: if the matched PR has not been opened yet it is
    opened now; if it is already open its number/url is surfaced.  This keeps a
    re-run from leaving a pushed branch with no reported PR -- which otherwise
    leads the agent to open a duplicate (#1167).

    ``pr_head``, when non-empty, is an explicit ``<owner>:<branch>`` PR head
    (the role-aware fork-PR flow's publish step, when ``remote`` here is
    actually the caller's fork remote rather than the repo's own) -- carried
    onto the result for ``_finish_auto_open``/``scope_from_create_result`` to
    prefer over the plain branch name.
    """
    # Resolve the head from the feature branch ref, not HEAD: a #1804 re-run
    # from the worktree base branch leaves HEAD off the feature branch, so
    # reading HEAD would record the wrong commit. Invoked from the legacy
    # on-feature-branch path these are identical.
    head_sha = _rev(feature_branch, cwd=worktree_path)
    with hooks.allow_pr_push():
        pushed = git_ops.push(remote, feature_branch, cwd=worktree_path, force_with_lease=True)
    if not pushed:
        return {**base, "error": (
            f"Failed to (re)push '{feature_branch}' to '{remote}'."
        )}
    # Match the PRRecord for this branch (a worktree may track several); update
    # it in place rather than clobbering an unrelated active PR. A *terminal*
    # PR for this branch (merged/closed externally, e.g. via the auto-merge
    # label) must NOT be reused -- surfacing it would report the merged PR as if
    # freshly opened and open no PR for the new commits (#1336). In that case we
    # append a FRESH record so the auto-open tail opens a new PR for the push.
    target: PRRecord | None = None
    if record is not None:
        target = next(
            (p for p in record.prs
             if p.branch == feature_branch and not tracking._pr_is_terminal(p)),
            None,
        )
        if target is None:
            target = PRRecord(
                branch=feature_branch, provider=prcfg.provider,
                repo=record.repo or "", opened_at=tracking._now_iso(),
            )
            # codename-attribution-by-default: freeze this PR's attribution
            # decision ONCE, at this fresh-construction site -- same
            # effective-value/explicitness rule as create_pr's own fresh
            # construction.
            _want_attribution = (
                prcfg.source_attribution if attribution is None else attribution
            )
            tracking.stamp_frozen_attribution(
                target, attribution=_want_attribution,
                explicit=(
                    attribution is not None
                    or prcfg.source_attribution_configured
                ),
            )
            record.prs.append(target)
        # target is always non-terminal here (a live match or a fresh record).
        target.state = "open"
        target.head_sha = head_sha
        target.head_observed_at = ""
        target.head_observed_api_base = ""
        # Refresh the squash-invariant patch-id after the re-squash (#898).
        target.patch_id = _patch_id(
            target.base_sha, feature_branch, cwd=worktree_path)
        if not target.provider:
            target.provider = prcfg.provider
        tracking.save_record(record)
    base_sha = target.base_sha if target else ""
    patch_id = target.patch_id if target else ""
    result = {
        **base, "success": True, "state": "open", "rerun": True,
        "branch": feature_branch, "remote": remote,
        "base_sha": base_sha, "head_sha": head_sha, "patch_id": patch_id,
        "provider": prcfg.provider, "default_branch": repo.default_branch,
        "repo": (target.repo if target else ""),
        "pr_count": len(record.prs) if record else 0,
        "draft": draft,
    }
    if pr_head:
        result["pr_head"] = pr_head
    _finish_auto_open(
        result, config, record, target, title=title, body=body,
        worktree_id=worktree_id, head_sha=head_sha, open_pr=open_pr,
        draft=draft, attribution=attribution,
    )
    observation_error = refresh_head_observation(config, record, target, head_sha)
    if observation_error:
        result["pr_head_observation_error"] = observation_error
    return result
