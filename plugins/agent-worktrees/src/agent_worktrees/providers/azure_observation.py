"""Native ADO evidence for the existing provider-neutral PR observation surface."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from ..pr_checks import CheckFailure
from ..pr_observation import ApprovalEvent, Discussion, Gate, Observation, Policy, Vote, fingerprint
from ..pr_observation_config import ObservationConfig
from ..pr_snapshot import PRSnapshot, Review
from .azure_observation_reads import AdoReader, Collection
from .base import ProviderError
from .observation_credentials import CredentialSource, ObservationCredentialError


# Built-in type IDs from the public Policy Configurations REST examples.
MINIMUM_APPROVAL_POLICY = "fa4e907d-c16b-4a4c-9dfa-4906e5d171dd"
REQUIRED_REVIEWERS_POLICY = "fd2167ab-b0be-447a-8ec8-39368250530e"
BUILD_POLICY = "0609b952-1397-4640-95ec-e00a01b2c241"


def instant(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def property_value(properties, key):
    value = properties.get(key) if isinstance(properties, dict) else None
    return value.get("$value") if isinstance(value, dict) else value


def identity(value):
    if not isinstance(value, str) or not value:
        return ""
    try:
        return str(UUID(value))
    except ValueError:
        return value


def votes_from_pr(pr) -> tuple[Vote, ...]:
    rows = pr.get("reviewers")
    if not isinstance(rows, list):
        raise ValueError("reviewer evidence is missing")
    votes = []
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                or type(row.get("vote")) is not int or row["vote"] not in (-10, -5, 0, 5, 10)
                or identity(row["id"]) in seen
                or any(key in row and type(row[key]) is not bool for key in ("isRequired", "isContainer"))):
            raise ValueError("reviewer evidence is invalid or duplicated")
        seen.add(identity(row["id"]))
        groups = row.get("votedFor") or []
        if not isinstance(groups, list) or any(not isinstance(g, dict) or not identity(g.get("id")) for g in groups):
            raise ValueError("reviewer group evidence is invalid")
        votes.append(Vote(
            identity(row["id"]), str(row.get("displayName") or row["id"]), row["vote"],
            row.get("isRequired") is True, row.get("isContainer") is True,
            tuple(sorted(identity(group["id"]) for group in groups)),
        ))
    return tuple(sorted(votes, key=lambda vote: vote.actor_id))


def thread_evidence(rows, author_id) -> tuple[tuple[Discussion, ...], tuple[ApprovalEvent, ...]]:
    discussions, events = [], []
    for row in rows:
        if "isDeleted" in row and type(row["isDeleted"]) is not bool:
            raise ValueError("discussion deletion evidence is invalid")
        if row.get("isDeleted"):
            continue
        props = row.get("properties") or {}
        comments = row.get("comments") or []
        if not isinstance(comments, list) or any(not isinstance(c, dict) for c in comments):
            raise ValueError("discussion comment evidence is invalid")
        if any(comment.get("commentType") not in ("text", "system", "codeChange", 1, 2, 3)
               or ("isDeleted" in comment and type(comment["isDeleted"]) is not bool)
               for comment in comments):
            raise ValueError("discussion comment type or deletion evidence is unavailable")
        if property_value(props, "CodeReviewThreadType") == "VoteUpdate":
            value = property_value(props, "CodeReviewVoteResult")
            actor = property_value(props, "CodeReviewVotedByTfId")
            try:
                vote = int(value)
            except (ValueError, TypeError) as exc:
                raise ValueError("vote event is invalid") from exc
            if (vote not in (-10, -5, 0, 5, 10) or not isinstance(actor, str)
                    or not actor or type(row.get("id")) is not int or not instant(row.get("publishedDate"))):
                raise ValueError("vote event identity or timestamp is unavailable")
            events.append(ApprovalEvent(str(row["id"]), identity(actor), vote, row["publishedDate"]))
            continue
        text = [comment for comment in comments if not comment.get("isDeleted")
                and comment.get("commentType") in ("text", 1)]
        if not text:
            continue
        if type(row.get("id")) is not int:
            raise ValueError("discussion identity is unavailable")
        last = text[-1]
        context = row.get("threadContext") or {}
        iteration = (row.get("pullRequestThreadContext") or {}).get("iterationContext") or {}
        author = identity((last.get("author") or {}).get("id"))
        discussions.append(Discussion(
            str(row["id"]), str(row.get("status", "")).casefold(),
            iteration.get("secondComparingIteration"),
            str(last.get("id") or ""), str(last.get("lastUpdatedDate") or row.get("lastUpdatedDate") or ""),
            author, str(context.get("filePath") or ""),
            (context.get("rightFileStart") or {}).get("line"),
            str(row.get("status", "")).casefold() in ("active", "pending") and author != identity(author_id),
            revision_fingerprint=fingerprint(row),
        ))
    return (tuple(sorted(discussions, key=lambda thread: int(thread.id))),
            tuple(sorted(events, key=lambda event: (instant(event.at), int(event.id)))))


def source_iteration(rows, head) -> tuple[int | None, str]:
    if any(type(row.get("id")) is not int for row in rows):
        raise ValueError("source iteration identity is invalid")
    ordered = sorted(rows, key=lambda row: row["id"])
    if not ordered or (ordered[-1].get("sourceRefCommit") or {}).get("commitId") != head:
        raise ValueError("source iteration does not describe the current head")
    matching = [row for row in ordered
                if (row.get("sourceRefCommit") or {}).get("commitId") == head
                and row.get("reason") != "retarget"]
    if not matching:
        raise ValueError("source push evidence is unavailable")
    latest = matching[-1]
    posted = (latest.get("push") or {}).get("date") or latest.get("createdDate")
    if not instant(posted):
        raise ValueError("source push timestamp is unavailable")
    return ordered[-1]["id"], posted


def review_gates(config, votes, events, discussions, author_id, posted, *, reviews_known, threads_known):
    author_id = identity(author_id)
    gates = []
    humans = [vote for vote in votes if not vote.is_group]
    approvals = [vote for vote in humans if vote.vote >= 5
                 and (not config.non_author or vote.actor_id != author_id)]
    if config.minimum_approvals:
        known = reviews_known and (bool(author_id) or not config.non_author)
        state = ("satisfied" if len(approvals) >= config.minimum_approvals else "blocked") if known else "unknown"
        gates.append(Gate("minimum_approvals", "review", state, "human",
                          f"{len(approvals)} of {config.minimum_approvals} qualifying approvals"))
    if config.no_blocking_votes:
        blocked = [vote for vote in votes if vote.vote < 0]
        state = ("blocked" if blocked else "satisfied") if reviews_known else "unknown"
        gates.append(Gate("no_blocking_votes", "review", state, "agent",
                          f"{len(blocked)} blocking or rejected reviewer votes"))
    if config.required_reviewers:
        missing = [vote for vote in votes if vote.required and vote.vote < 5]
        state = ("blocked" if missing else "satisfied") if reviews_known else "unknown"
        gates.append(Gate("required_reviewers", "review", state, "human",
                          "Required reviewers awaiting approval: " + ", ".join(v.user for v in missing)
                          if missing else "Every currently required reviewer set is approved"))
    if config.approval_after_push:
        last_by_actor = {}
        for event in events:
            last_by_actor[event.actor_id] = event
        current = {vote.actor_id: vote for vote in humans}
        qualifying = [event for actor, event in last_by_actor.items()
                      if actor in current and current[actor].vote >= 5 and event.vote >= 5
                      and instant(posted) is not None and instant(event.at) >= instant(posted)]
        known = reviews_known and threads_known and instant(posted) is not None
        state = ("satisfied" if qualifying else "blocked") if known else "unknown"
        gates.append(Gate("latest_push_approval", "review", state, "human",
                          "A current approval was posted after the latest source push" if qualifying
                          else "An approval after the latest source push is required"))
    if config.resolved_comments:
        unresolved = [thread for thread in discussions if thread.status in ("active", "pending")]
        unknown = [thread for thread in discussions
                   if thread.status not in ("active", "pending", "fixed", "closed", "wontfix", "bydesign")]
        state = ("blocked" if unresolved else "satisfied") if threads_known and not unknown else "unknown"
        gates.append(Gate("comments_resolved", "discussion", state, "agent",
                          f"{len(unresolved)} unresolved threads; {len(unknown)} unknown resolution states"))
    return gates


def _policy_kind(configuration, config):
    settings = configuration.get("settings") or {}
    native_type = (configuration.get("type") or {}).get("displayName", "")
    type_id = str((configuration.get("type") or {}).get("id") or "").casefold()
    row = {
        "id": str(configuration.get("id", "")),
        "name": str(settings.get("displayName") or settings.get("statusName") or native_type),
        "context_name": str(settings.get("statusName") or ""),
        "context_genre": str(settings.get("statusGenre") or ""),
    }
    matches = [rule.kind for rule in config.policy_rules if rule.matches(row)]
    if len(set(matches)) > 1:
        raise ValueError("contradictory policy classifications")
    if matches:
        kind = matches[0]
    elif type_id in (MINIMUM_APPROVAL_POLICY, REQUIRED_REVIEWERS_POLICY):
        kind = "human"
    elif type_id == BUILD_POLICY or str(native_type).casefold() == "build":
        kind = "build"
    elif "statusName" in settings:
        kind = "status"
    else:
        kind = "agent"
    return row, kind


def latest_timestamped(rows, field, label):
    ranked = [(instant(row.get(field)), row) for row in rows]
    if any(at is None for at, _ in ranked):
        raise ValueError(label + " timestamp is unavailable")
    latest = max(at for at, _ in ranked)
    candidates = [row for at, row in ranked if at == latest]
    if any(row != candidates[0] for row in candidates[1:]):
        raise ValueError(label + " chronology is ambiguous")
    return candidates[0]


def current_evaluations(rows):
    grouped = {}
    for row in rows:
        cfg = row.get("configuration") or {}
        if any(key in cfg and type(cfg[key]) is not bool for key in ("isDeleted", "isEnabled")):
            raise ValueError("policy activation evidence is invalid")
        if cfg.get("isDeleted") or cfg.get("isEnabled") is False:
            continue
        if type(cfg.get("id")) is not int or cfg["id"] <= 0 or not isinstance(cfg.get("isBlocking"), bool):
            raise ValueError("policy identity or requiredness is unavailable")
        key = str(cfg["id"])
        grouped.setdefault(key, []).append(row)
    return tuple(latest_timestamped(group, "startedDate", "policy evaluation")
                 for _, group in sorted(grouped.items()))


def policies_from_evaluations(rows, statuses, config, head, iteration):
    result = []
    for row in current_evaluations(rows):
        cfg, context = row["configuration"], row.get("context") or {}
        key = str(cfg["id"])
        if "isExpired" in context and type(context["isExpired"]) is not bool:
            raise ValueError("policy expiry evidence is invalid")
        settings = cfg.get("settings") or {}
        identity, kind = _policy_kind(cfg, config)
        source = str(context.get("lastMergeSourceCommitId") or context.get("sourceCommitId") or "")
        native_iteration = context.get("iterationId")
        stale = bool((source and source != head)
                     or (native_iteration is not None and native_iteration != iteration))
        state = str(row.get("status", "")).casefold()
        if context.get("isExpired") is True:
            state = "expired"
        matched = [status for status in statuses
                   if (status.get("context") or {}).get("name") == identity["context_name"]
                   and (status.get("context") or {}).get("genre", "") == identity["context_genre"]]
        if matched and identity["context_name"]:
            status = latest_timestamped(matched, "updatedDate", "native status")
            if settings.get("invalidateOnSourceUpdate") is True:
                stale = stale or status.get("iterationId") != iteration
        build_id = context.get("buildId")
        if build_id is not None and (type(build_id) is not int or build_id <= 0):
            raise ValueError("policy build identity is invalid")
        result.append(Policy(
            key, identity["name"], state, cfg["isBlocking"],
            str((cfg.get("type") or {}).get("id") or ""), kind,
            identity["context_name"], identity["context_genre"], source,
            native_iteration, stale, build_id,
            str(((row.get("_links") or {}).get("html") or {}).get("href") or ""),
        ))
    return tuple(result)


def policy_gates(policies, *, known, existing):
    gates = []
    for policy in policies:
        if policy.stale:
            state = "pending"
        elif policy.state in ("approved", "notapplicable"):
            state = "satisfied"
        elif policy.state in ("rejected", "broken", "expired"):
            state = "blocked"
        elif policy.state in ("queued", "running"):
            state = "pending"
        else:
            state = "unknown"
        gates.append(Gate("policy:" + policy.id, policy.kind, state,
                          "human" if policy.kind == "human" else "agent",
                          policy.name + ": " + ("stale evaluation" if policy.stale else policy.state),
                          policy.required, url=policy.url))
    if not known:
        gates.append(Gate("policy_evidence", "policy", "unknown", "external",
                          "Required policy evidence is unavailable or incomplete"))
    prerequisites = [gate for gate in [*existing, *gates]
                     if gate.required and gate.kind != "composite" and gate.state != "satisfied"]
    return [replace(gate, state="waiting", owner="external",
                    waiting_for=tuple(item.id for item in prerequisites),
                    summary=gate.summary + "; waiting for prerequisite gates")
            if gate.kind == "composite" and gate.state in ("blocked", "pending") and prerequisites
            else gate for gate in gates]


def latest_push_policy(rows, head, iteration, posted):
    for row in current_evaluations(rows):
        cfg, context = row.get("configuration") or {}, row.get("context") or {}
        settings = cfg.get("settings") or {}
        if (cfg.get("isEnabled") is False or cfg.get("isDeleted")
                or str((cfg.get("type") or {}).get("id") or "").casefold() != MINIMUM_APPROVAL_POLICY
                or str(row.get("status", "")).casefold() != "approved" or context.get("isExpired")
                or not (settings.get("requireVoteOnLastIteration") is True
                        or settings.get("requireVoteOnEachIteration") is True)):
            continue
        source, round_id = context.get("lastMergeSourceCommitId"), context.get("iterationId")
        if (source and source != head) or (round_id is not None and round_id != iteration):
            continue
        evaluated = instant(row.get("startedDate"))
        fresh = round_id == iteration if round_id is not None else (
            evaluated is not None and instant(posted) is not None and evaluated >= instant(posted))
        if fresh:
            return str(cfg.get("id"))
    return ""


def missing_policy_evidence(configurations, evaluations):
    evaluated = {str(row["configuration"]["id"]): row["configuration"]
                 for row in current_evaluations(evaluations)}
    missing = []
    for cfg in configurations:
        if (type(cfg.get("id")) is not int or type(cfg.get("isBlocking")) is not bool
                or any(key in cfg and type(cfg[key]) is not bool for key in ("isEnabled", "isDeleted"))):
            raise ValueError("target policy configuration evidence is invalid")
        if cfg.get("isDeleted") or cfg.get("isEnabled") is False or not cfg["isBlocking"]:
            continue
        current = evaluated.get(str(cfg["id"]))
        settings = cfg.get("settings") or {}
        if not isinstance(settings, dict):
            raise ValueError("target policy settings are invalid")
        scope = settings.get("scope")
        patterns = settings.get("filenamePatterns")
        if (scope is not None and (not isinstance(scope, list)
                                   or any(not isinstance(item, dict) for item in scope))):
            raise ValueError("target policy scope is invalid")
        if patterns is not None and (not isinstance(patterns, list)
                                     or any(not isinstance(item, str) or not item for item in patterns)):
            raise ValueError("target policy path filters are invalid")
        repository_only = bool(scope) and all(
            not item.get("refName") and not item.get("matchKind") for item in scope)
        # The branch roster also contains repository settings and path-scoped
        # policies. Only the PR evaluation service decides conditional applicability.
        if current is None and (repository_only or patterns):
            continue
        if current is None or (cfg.get("revision") is not None and current.get("revision") != cfg["revision"]):
            missing.append(str(cfg["id"]))
    return missing


def validate_target(pr, provider, repo):
    project, name = provider._split_repo(repo)
    native_repo = pr.get("repository") or {}
    if name.casefold() not in {str(native_repo.get(key) or "").casefold() for key in ("name", "id")}:
        raise ProviderError("ADO PR metadata does not match the addressed repository")
    native_project = native_repo.get("project") or {}
    if project.casefold() not in {str(native_project.get(key) or "").casefold() for key in ("name", "id")}:
        raise ProviderError("ADO PR metadata does not match the addressed project")
    return native_project


def get_snapshot(
    provider, repo, number, *, api_base, token, observation=None, reader_factory=AdoReader,
    credential_source: CredentialSource | None = None,
):
    config = observation or ObservationConfig()
    if not config.enabled:
        if credential_source is not None:
            raise ProviderError("Renewable credentials require native ADO observation to be enabled")
        return provider.get_snapshot(repo, number, api_base=api_base, token=token)
    options = {"credential_source": credential_source} if credential_source is not None else {}
    reader = reader_factory(provider, repo, number, api_base, token, **options)
    pr = reader.pr()
    native_project = validate_target(pr, provider, repo)
    status = str(pr.get("status") or "").casefold()
    head = str((pr.get("lastMergeSourceCommit") or {}).get("commitId") or "")
    if status in ("completed", "abandoned"):
        return PRSnapshot(pr_state="closed", merged=status == "completed", head_sha=head)
    if status != "active":
        raise ProviderError("ADO PR lifecycle state is unavailable")
    if not head:
        raise ProviderError("ADO current source head is unavailable",
                            transient=str(pr.get("mergeStatus", "")).casefold() in ("queued", "notset"))
    errors = []
    votes, discussions, events, policies = (), (), (), ()
    iteration, posted = None, ""
    reviews_known = config.reviews
    try:
        if config.reviews:
            votes = votes_from_pr(pr)
    except ValueError as exc:
        reviews_known = False
        errors.append(str(exc))
    iterations = reader.iterations()
    if iterations.complete:
        try:
            iteration, posted = source_iteration(iterations.records, head)
        except ValueError as exc:
            errors.append(str(exc))
    else:
        errors.append(iterations.error)
    threads = reader.threads() if config.comments or config.approval_after_push else Collection(error="Discussion observation disabled")
    threads_known = threads.complete
    if threads.complete:
        try:
            discussions, events = thread_evidence(threads.records, (pr.get("createdBy") or {}).get("id"))
        except ValueError as exc:
            threads_known = False
            errors.append(str(exc))
    elif config.comments or config.approval_after_push:
        errors.append(threads.error)
    statuses = reader.statuses() if config.policies else Collection()
    configurations = (reader.configurations((pr.get("repository") or {}).get("id"), pr.get("targetRefName"))
                      if config.policies else Collection(error="Policy observation disabled"))
    evaluations = (reader.evaluations(native_project["id"], number)
                   if config.policies and native_project.get("id")
                   else Collection(error="Policy observation disabled or project identity unavailable"))
    policies_known = statuses.complete and evaluations.complete and configurations.complete
    if policies_known:
        try:
            policies = policies_from_evaluations(evaluations.records, statuses.records, config, head, iteration)
            missing = missing_policy_evidence(configurations.records, evaluations.records)
            if missing:
                policies_known = False
                errors.append("Required policy evaluations missing or stale: " + ", ".join(missing))
        except ValueError as exc:
            policies_known = False
            errors.append(str(exc))
    elif config.policies:
        errors.extend(error for error in (statuses.error, evaluations.error, configurations.error) if error)
    enriched = []
    for policy in policies:
        if policy.build_id and policy.required and config.builds and not policy.stale:
            try:
                build = reader.build(policy.build_id)
                source = str(build.get("sourceVersion") or "")
                current_merge = str((pr.get("lastMergeCommit") or {}).get("commitId") or "")
                if source and source not in (head, current_merge):
                    policy = replace(policy, stale=True)
                if policy.state in ("rejected", "broken"):
                    policy = replace(policy, failed_tasks=reader.failed_tasks(policy.build_id))
            except ObservationCredentialError:
                raise
            except ProviderError as exc:
                errors.append("build diagnostics: " + str(exc))
        enriched.append(policy)
    policies = tuple(enriched)
    gates = review_gates(config, votes, events, discussions, (pr.get("createdBy") or {}).get("id"),
                         posted, reviews_known=reviews_known, threads_known=threads_known)
    proof = latest_push_policy(evaluations.records, head, iteration, posted) if policies_known else ""
    if proof and reviews_known and any(vote.vote >= 5 and not vote.is_group for vote in votes):
        gates = [replace(gate, state="satisfied",
                         summary="Current native latest-iteration approval policy " + proof + " passed")
                 if gate.id == "latest_push_approval" else gate for gate in gates]
    merge = str(pr.get("mergeStatus") or "").casefold()
    merge_state = "blocked" if merge in ("conflicts", "failure") else (
        "satisfied" if merge in ("succeeded", "rejectedbypolicy") else "unknown")
    gates.append(Gate("mergeable", "merge", merge_state, "agent", "Native merge computation: " + merge))
    gates.extend(policy_gates(policies, known=policies_known, existing=gates))
    if errors:
        gates.append(Gate("read_evidence", "evidence", "unknown", "external",
                          "One or more observation reads are incomplete"))
    after = reader.pr()
    if reviews_known:
        try:
            if votes_from_pr(after) != votes:
                raise ProviderError("ADO reviewers changed during observation; retry the complete read",
                                    transient=True)
        except ValueError as exc:
            raise ProviderError("ADO final reviewer evidence is unavailable", transient=True) from exc
    if ((after.get("lastMergeSourceCommit") or {}).get("commitId") != head
            or (after.get("lastMergeCommit") or {}).get("commitId") != (pr.get("lastMergeCommit") or {}).get("commitId")):
        raise ProviderError("ADO source or merge candidate moved during observation; retry the complete read",
                            transient=True)
    result = Observation(
        "azure-devops", repo, number, api_base, head, iteration, posted, votes, events,
        discussions, policies, tuple(gates), not errors, tuple(errors), config.guidance,
        votes_known=reviews_known,
    )
    required = [p for p in policies if p.required and p.kind in ("build", "status")]
    checks = ("none" if policies_known and not required
              else "failure" if any(p.state in ("rejected", "broken", "expired") and not p.stale for p in required)
              else "pending" if any(p.stale or p.state in ("queued", "running") for p in required)
              else "success" if policies_known and all(p.state in ("approved", "notapplicable") for p in required)
              else "")
    reviews = tuple(Review(index + 1, "APPROVED" if vote.vote > 0 else "CHANGES_REQUESTED",
                           vote.actor_id)
                    for index, vote in enumerate(sorted((v for v in votes if v.vote),
                                                       key=lambda v: (v.vote < 0, v.actor_id))))
    return PRSnapshot(
        pr_state="closed" if status in ("completed", "abandoned") else "open",
        merged=status == "completed", head_sha=head,
        base_ref=str(pr.get("targetRefName") or "").removeprefix("refs/heads/"),
        reviews=reviews, author=identity((pr.get("createdBy") or {}).get("id")),
        mergeable=False if merge == "conflicts" else True if merge_state == "satisfied" else None,
        checks_state=checks, checks_complete=policies_known, title=str(pr.get("title") or ""),
        draft=pr.get("isDraft") is True,
        labels=tuple(str(label["name"]) for label in pr.get("labels", []) if label.get("name"))
        + (("auto-complete",) if pr.get("autoCompleteSetBy") else ()),
        check_failures=tuple(CheckFailure(p.name, p.state, "ado-policy", p.url)
                             for p in required if not p.stale and p.state in ("rejected", "broken", "expired")),
        observation=result,
    )
