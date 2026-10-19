"""Fixed native ADO comment/status and existing-evaluation mutations only."""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import UUID

from ..pr_ado_remediation import Ledger, RemediationError, digest
from .azure_observation import BUILD_POLICY, current_evaluations, instant, property_value, validate_target
from .azure_observation_reads import AdoReader, MAX_RESPONSE_BYTES
from .base import ProviderError


STATUSES = ("fixed", "wontFix", "byDesign", "closed", "pending")
STATUS_VALUES = {"fixed": 2, "wontFix": 3, "closed": 4, "byDesign": 5, "pending": 6}


def positive(value, label):
    if type(value) is not int or value <= 0:
        raise RemediationError(label + " is unavailable")
    return value


def full_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise RemediationError("A full source commit SHA is required")
    return value.lower()


def complete(collection):
    if not collection.complete:
        raise RemediationError("Complete native read required: " + collection.error)
    return collection.records


def thread_fingerprint(thread):
    """Full native thread revision, including native iteration and author evidence."""
    positive(thread.get("id"), "Thread identity")
    if property_value(thread.get("properties") or {}, "CodeReviewThreadType") == "VoteUpdate":
        raise RemediationError("Vote threads are not discussion remediation targets")
    comments = thread.get("comments")
    if (not isinstance(comments, list) or not comments
            or type(thread.get("isDeleted", False)) is not bool
            or thread.get("isDeleted") is True or not instant(thread.get("lastUpdatedDate"))):
        raise RemediationError("Thread freshness evidence is unavailable")
    if thread.get("status") not in ("active", "fixed", "wontFix", "closed", "byDesign", "pending",
                                    1, 2, 3, 4, 5, 6):
        raise RemediationError("Thread status evidence is unavailable")
    seen = set()
    for comment in comments:
        if not isinstance(comment, dict):
            raise RemediationError("Comment evidence is invalid")
        cid = positive(comment.get("id"), "Comment identity")
        if (cid in seen or not isinstance((comment.get("author") or {}).get("id"), str)
                or not (comment.get("author") or {}).get("id")
                or not instant(comment.get("publishedDate"))
                or not instant(comment.get("lastUpdatedDate"))
                or type(comment.get("parentCommentId")) is not int
                or comment["parentCommentId"] < 0
                or comment.get("commentType") not in ("text", "system", "codeChange", 1, 2, 3)
                or type(comment.get("isDeleted", False)) is not bool
                or (not comment.get("isDeleted") and not isinstance(comment.get("content"), str))):
            raise RemediationError("Comment identity, author or freshness evidence is unavailable")
        seen.add(cid)
    if not any(not c.get("isDeleted") and c.get("commentType") in ("text", 1) for c in comments):
        raise RemediationError("No live native discussion comment")
    iteration = (thread.get("pullRequestThreadContext") or {}).get("iterationContext")
    if iteration is not None:
        if not isinstance(iteration, dict):
            raise RemediationError("Discussion round evidence is invalid")
        positive(iteration.get("secondComparingIteration"), "Discussion round")
        positive(iteration.get("firstComparingIteration"), "Discussion comparison round")
    return "sha256:" + digest(thread)


class AdoMutator(AdoReader):
    def _write(self, method, url, payload):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ProviderError("ADO remediation budget exhausted; outcome must be reconciled")
        request = Request(url, data=json.dumps(payload).encode("utf-8"), method=method,
                          headers={"Authorization": self._authorization,
                                   "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=min(20, remaining)) as response:
                if response.status not in (200, 201):
                    raise ProviderError("ADO remediation returned an unexpected HTTP status; reconcile outcome")
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise ProviderError("ADO remediation write outcome is unknown; no automatic retry") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise ProviderError("ADO remediation response exceeds its byte limit; reconcile outcome")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("ADO remediation response is invalid; reconcile outcome") from exc
        if not isinstance(value, dict):
            raise ProviderError("ADO remediation response has invalid shape; reconcile outcome")
        return value

    def requeue_evaluation(self, evaluation):
        evaluation = str(UUID(evaluation))
        return self._write("PATCH", self.project_base + "/policy/evaluations/"
                           + evaluation + "?api-version=7.1-preview.1", {"status": "queued"})

    def evidence_thread(self, text):
        return self._write("POST", self.base + "/threads?api-version=7.1", {
            "status": "closed",
            "comments": [{"parentCommentId": 0, "content": text, "commentType": 1}],
        })

    def reply_comment(self, thread, parent, text):
        positive(thread, "Thread identity")
        positive(parent, "Parent comment identity")
        return self._write("POST", self.base + f"/threads/{thread}/comments?api-version=7.1", {
            "parentCommentId": parent, "content": text, "commentType": 1,
        })

    def thread_status(self, thread, status):
        positive(thread, "Thread identity")
        if status not in STATUSES:
            raise RemediationError("Unsupported native thread status")
        return self._write("PATCH", self.base + f"/threads/{thread}?api-version=7.1",
                           {"status": status})


class Remediator:
    def __init__(self, provider, repo, number, *, api_base, token, state_dir=None,
                 reader_factory=AdoMutator):
        positive(number, "PR number")
        self.provider, self.repo, self.number = provider, repo, number
        self.reader = reader_factory(provider, repo, number, api_base, token)
        self.state_dir = state_dir
        self.endpoint = api_base.rstrip("/").casefold()
        parsed = urlsplit(api_base)
        if parsed.username or parsed.password:
            raise RemediationError("Endpoint must not contain credentials")

    def active(self):
        pr = self.reader.pr()
        project = validate_target(pr, self.provider, self.repo)
        if str(pr.get("status", "")).casefold() != "active":
            raise RemediationError("PR is not active")
        repository = pr.get("repository") or {}
        if not project.get("id") or not repository.get("id"):
            raise RemediationError("Canonical project/repository identity is unavailable")
        target = digest([self.endpoint, str(project["id"]).casefold(),
                         str(repository["id"]).casefold(), self.number])
        return pr, target, project["id"]

    def ledger(self, target):
        return Ledger(target, state_dir=self.state_dir)

    def threads(self):
        rows = complete(self.reader.threads())
        ids = [positive(row.get("id"), "Thread identity") for row in rows]
        if len(set(ids)) != len(ids):
            raise RemediationError("Native thread identities are ambiguous")
        return rows

    def policy(self, selector, expected_head, reason):
        pr, target, project = self.active()
        head = full_sha((pr.get("lastMergeSourceCommit") or {}).get("commitId"))
        if head != full_sha(expected_head):
            raise RemediationError("Source head moved")
        rows = current_evaluations(complete(self.reader.evaluations(project, self.number)))
        matches = []
        for row in rows:
            cfg = row["configuration"]
            settings = cfg.get("settings") or {}
            name = str(settings.get("displayName") or (cfg.get("type") or {}).get("displayName") or "")
            if str(cfg["id"]) == str(selector) or name.casefold() == str(selector).casefold():
                matches.append(row)
        if len(matches) != 1:
            raise RemediationError("Policy selector is missing or ambiguous")
        row = matches[0]
        cfg = row["configuration"]
        repository = pr["repository"]
        configurations = complete(self.reader.configurations(repository["id"], pr.get("targetRefName")))
        native = [c for c in configurations if c.get("id") == cfg["id"]]
        if (len(native) != 1 or native[0].get("isEnabled") is not True
                or native[0].get("isBlocking") is not True or native[0].get("isDeleted", False) is not False
                or type(cfg.get("revision")) is not int or cfg["revision"] <= 0
                or native[0].get("revision") != cfg["revision"]
                or native[0].get("settings") != cfg.get("settings")
                or native[0].get("type") != cfg.get("type")):
            raise RemediationError("Live policy configuration is missing, disabled, changed or ambiguous")
        native_type = cfg.get("type") or {}
        if (cfg.get("isEnabled") is not True or cfg.get("isBlocking") is not True
                or cfg.get("isDeleted", False) is not False
                or not (str(native_type.get("id", "")).casefold() == BUILD_POLICY
                        or str(native_type.get("displayName", "")).casefold() == "build")):
            raise RemediationError("Only a current enabled blocking native Build policy can be requeued")
        context = row.get("context") or {}
        sources = [context[key] for key in ("lastMergeSourceCommitId", "sourceCommitId")
                   if context.get(key)]
        if not sources or any(full_sha(source) != head for source in sources):
            raise RemediationError("Policy source commit is missing, ambiguous or stale")
        try:
            evaluation = str(UUID(row["evaluationId"]))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise RemediationError("Native evaluation identity is unavailable") from exc
        if "isExpired" in context and type(context["isExpired"]) is not bool:
            raise RemediationError("Policy expiry evidence is invalid")
        expired = context.get("isExpired") is True
        status = str(row.get("status", "")).casefold()
        if reason == "expired":
            if not expired and status != "expired":
                raise RemediationError("Policy is no longer expired")
        elif reason == "flaky":
            if expired or status != "rejected":
                raise RemediationError("Flaky recovery requires a rejected, non-expired evaluation")
        else:
            raise RemediationError("Unsupported requeue reason")
        # Bracket the policy/configuration reads; do not PATCH a PR which moved
        # while those collections were being retrieved.
        latest, latest_target, _ = self.active()
        if (latest_target != target
                or full_sha((latest.get("lastMergeSourceCommit") or {}).get("commitId")) != head
                or latest.get("targetRefName") != pr.get("targetRefName")):
            raise RemediationError("PR lifecycle, target or source moved during policy read")
        return target, head, str(cfg["id"]), evaluation, digest(cfg)

    @staticmethod
    def marker_threads(rows, marker):
        return sorted(row["id"] for row in rows if any(
            marker in str(comment.get("content") or "")
            for comment in row.get("comments") or []))

    def requeue(self, selector, *, expected_head, reason, evidence=""):
        if reason == "flaky" and (len(evidence.strip()) < 40
                                  or len(re.sub(r"\W", "", evidence)) < 30):
            raise RemediationError("Flaky recovery requires at least 40 characters of substantive evidence")
        selected = self.policy(selector, expected_head, reason)
        target, head, policy, evaluation, _ = selected
        key = "requeue:" + digest([head, policy])
        marker = f"<!-- agent-worktrees:requeue {target} {policy}@{head} -->"
        legacy = f"<!-- watch_pr:requeue {policy}@{head[:12]} -->"
        with self.ledger(target).locked() as ledger:
            if key in ledger.records:
                raise RemediationError("Requeue consumed locally; outcome requires read-only reconciliation")
            rows = self.threads()
            if self.marker_threads(rows, marker) or self.marker_threads(rows, legacy):
                raise RemediationError("Cross-venue requeue marker already exists")
            ledger.consume(key, digest([selected, reason, evidence]), evaluation_id=evaluation)
            try:
                if reason == "flaky":
                    posted = self.reader.evidence_thread(evidence + "\n\n" + marker + "\n" + legacy)
                    own = positive(posted.get("id"), "Evidence thread identity")
                    rows = self.threads()
                    winners = sorted(set(self.marker_threads(rows, marker)
                                         + self.marker_threads(rows, legacy)))
                    if not winners or winners[0] != own:
                        raise RemediationError("Earlier cross-venue reservation won; no requeue")
                    note = next(row for row in rows if row["id"] == own)
                    if note.get("status") not in ("closed", 4):
                        raise RemediationError("Evidence thread must be closed before requeue")
                if self.policy(selector, expected_head, reason) != selected:
                    raise RemediationError("Policy configuration or evaluation moved")
                self.reader.requeue_evaluation(evaluation)
                pr, observed_target, project = self.active()
                rows = current_evaluations(complete(self.reader.evaluations(project, self.number)))
                verified = [row for row in rows if str((row.get("configuration") or {}).get("id")) == policy]
                if (observed_target != target
                        or full_sha((pr.get("lastMergeSourceCommit") or {}).get("commitId")) != head
                        or len(verified) != 1 or verified[0].get("evaluationId") != evaluation
                        or str(verified[0].get("status", "")).casefold() != "queued"):
                    raise RemediationError("Queued evaluation readback is unverified; no automatic retry")
                ledger.update(key, state="verified")
                return {"state": "verified", "policy_id": policy, "evaluation_id": evaluation}
            except BaseException:
                ledger.update(key, state="unknown")
                raise

    def fresh_thread(self, thread):
        rows = self.threads()
        matches = [row for row in rows if row["id"] == thread]
        if len(matches) != 1:
            raise RemediationError("Discussion thread is unavailable or ambiguous")
        row = matches[0]
        thread_fingerprint(row)
        context = (row.get("pullRequestThreadContext") or {}).get("iterationContext")
        if context is not None:
            iterations = complete(self.reader.iterations())
            ids = [positive(it.get("id"), "Iteration identity") for it in iterations]
            if (len(ids) != len(set(ids))
                    or any(context[key] not in ids for key in
                           ("firstComparingIteration", "secondComparingIteration"))):
                raise RemediationError("Discussion revision round is unavailable")
        return row

    def reply(self, thread, *, text, expected_thread, idempotency_key, status=None):
        positive(thread, "Thread identity")
        if (not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 65536
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_thread)
                or status not in (None, *STATUSES)):
            raise RemediationError("Invalid bounded reply text, fingerprint, key or status")
        _, target, _ = self.active()
        keyhash = digest(idempotency_key)
        key = "reply:" + keyhash
        request_hash = digest([thread, text, status])
        prefix = f"<!-- agent-worktrees:reply {target} {keyhash} "
        marker = prefix + request_hash + " -->"
        content = text + "\n\n" + marker
        with self.ledger(target).locked() as ledger:
            prior = ledger.records.get(key)
            if prior and prior["request_hash"] != request_hash:
                raise RemediationError("Idempotency key already binds a different reply")
            row = self.fresh_thread(thread)
            marked = [(native["id"], comment) for native in self.threads()
                      for comment in native.get("comments") or []
                      if prefix in str(comment.get("content") or "")]
            if marked:
                if (len(marked) != 1 or marked[0][0] != thread
                        or marked[0][1].get("content") != content
                        or marked[0][1].get("isDeleted") is True):
                    raise RemediationError("Cross-venue idempotency marker collision")
                comment = marked[0][1]
                positive(comment.get("id"), "Reply comment identity")
                row = self.fresh_thread(thread)
                if not any(c.get("id") == comment["id"] and c.get("content") == content
                           and not c.get("isDeleted") for c in row["comments"]):
                    raise RemediationError("Reply changed during reconciliation")
                if not prior:
                    ledger.consume(key, request_hash, thread_id=thread, expected_thread=expected_thread)
                satisfied = status is None or row.get("status") in (status, STATUS_VALUES.get(status))
                ledger.update(key, state="verified" if satisfied else "unknown",
                              comment_id=comment["id"])
                return {"state": "verified" if satisfied else "partial", "thread_id": thread,
                        "comment_id": comment["id"], "reconciled": True,
                        "status_verified": satisfied}
            if prior:
                raise RemediationError("Reply consumed but readback absent; no blind POST or retry")
            if thread_fingerprint(row) != expected_thread:
                raise RemediationError("Thread fingerprint changed; fetch a fresh discussion report")
            parent = positive(row["comments"][0].get("id"), "Parent comment identity")
            if row["comments"][0].get("isDeleted") is True:
                raise RemediationError("Parent comment is deleted")
            # PR head is deliberately not a precondition for a discussion reply.
            _, observed_target, _ = self.active()
            if observed_target != target or thread_fingerprint(self.fresh_thread(thread)) != expected_thread:
                raise RemediationError("Discussion changed immediately before reply")
            ledger.consume(key, request_hash, thread_id=thread, expected_thread=expected_thread)
            try:
                self.reader.reply_comment(thread, parent, content)
                after = self.fresh_thread(thread)
                matching = [c for c in after["comments"] if c.get("content") == content and not c.get("isDeleted")]
                if len(matching) != 1 or matching[0].get("parentCommentId") != parent:
                    raise RemediationError("Reply readback is unverified")
                cid = positive(matching[0].get("id"), "Reply comment identity")
                ledger.update(key, comment_id=cid)
                if status is not None:
                    before_patch = self.fresh_thread(thread)
                    _, current_target, _ = self.active()
                    original = {k: v for k, v in row.items() if k != "lastUpdatedDate"}
                    projected = {k: v for k, v in after.items() if k != "lastUpdatedDate"}
                    projected["comments"] = [c for c in after["comments"] if c["id"] != cid]
                    if (current_target != target or original != projected
                            or thread_fingerprint(before_patch) != thread_fingerprint(after)):
                        raise RemediationError("Discussion changed before status mutation; reply preserved")
                    self.reader.thread_status(thread, status)
                    after = self.fresh_thread(thread)
                    if after.get("status") not in (status, STATUS_VALUES[status]):
                        raise RemediationError("Native thread status readback is unverified")
                    if not any(c.get("id") == cid and c.get("content") == content
                               and c.get("parentCommentId") == parent and not c.get("isDeleted")
                               for c in after["comments"]):
                        raise RemediationError("Reply changed during native status readback")
                ledger.update(key, state="verified")
                return {"state": "verified", "thread_id": thread, "comment_id": cid}
            except BaseException:
                ledger.update(key, state="unknown")
                raise
