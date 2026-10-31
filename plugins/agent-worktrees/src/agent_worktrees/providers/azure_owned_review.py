"""Native review operations over one existing protected, credential-pinned transport."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import dataclass
import hashlib
import json
import math
from urllib.parse import quote, urlencode

from .azure_observation_reads import AdoReader, MAX_RESPONSE_BYTES


class ReviewContextUnavailable(RuntimeError):
    """The selected owner's current write binding is unavailable, not a new grant."""


@dataclass(frozen=True)
class NativeReviewResponse:
    status: int
    body: dict
    continuation: str = ""
    late: bool = False


@dataclass(frozen=True)
class NativeActionOutcome:
    receipt: dict
    confirmed: bool


def _native_id(value, *, numeric=False):
    if type(value) is int:
        return 0 < value <= 2**63 - 1
    return (
        not numeric and isinstance(value, str) and 0 < len(value) <= 256
        and all(ord(character) >= 32 for character in value)
    )


class _BoundReader(AdoReader):
    def __init__(self, context):
        self.context = context
        scope = context.scope
        self.api_base = scope["endpoint"].rstrip("/")
        self.base = (
            self.api_base + "/" + quote(scope["project_id"], safe="")
            + "/_apis/git/repositories/" + quote(scope["repository_id"], safe="")
            + f"/pullRequests/{scope['number']}"
        )
        self.project_base = self.api_base + "/" + quote(scope["project_id"], safe="") + "/_apis"

    def json(self, url, *, query=None):
        result = self.context.call("GET", url, None, query=query)
        if result.status != 200:
            raise ReviewContextUnavailable("native review read did not succeed")
        if result.continuation and url in (
            self.base + "?api-version=7.1",
            self.api_base + "/_apis/connectionData?api-version=7.1-preview.1",
        ):
            raise ReviewContextUnavailable("singleton native review read has unexpected continuation")
        return copy.deepcopy(result.body), result.continuation


class AdoReviewContext:
    """Construct only inside the normal owner's actual pinned write-context factory.

    ``request`` is the existing protected transport, shared by identity reads,
    candidate reads and writes. ``credential_binding`` returns its actual private
    material reference, never a caller/forge label; request must enforce that same
    expected reference at dispatch. No auth/HTTP client or credential is created.
    """

    def __init__(self, scope, *, request, credential_binding, checkpoint, review_check):
        if not all(callable(value) for value in (request, credential_binding, checkpoint, review_check)):
            raise ValueError("existing protected transport/binding/checks required")
        self._scope = copy.deepcopy(scope)
        self.request, self.credential_binding = request, credential_binding
        self.checkpoint, self.review_check = checkpoint, review_check
        self.pinned = credential_binding()
        if self.pinned is None or isinstance(self.pinned, (str, int, float, bool, dict, list, tuple)):
            raise ValueError("an actual private credential binding reference is required")
        self.reader = _BoundReader(self)
        self.remaining = None

    @property
    def scope(self):
        return copy.deepcopy(self._scope)

    def check(self):
        self.checkpoint()
        if self.credential_binding() is not self.pinned:
            raise ReviewContextUnavailable("write credential binding changed")

    def call(self, method, url, body, *, query=None):
        self.check()
        if self.remaining is None:
            raise ReviewContextUnavailable("write context lacks a finite operation budget")
        remaining = self.remaining()
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
            raise TimeoutError("review operation budget exhausted")
        policy_url = self.reader.project_base + "/policy/evaluations?" + urlencode({
            "artifactId": f"vstfs:///CodeReview/CodeReviewId/{self.scope['project_id']}/{self.scope['number']}",
            "includeNotApplicable": "true", "api-version": "7.1-preview.1",
        })
        if url != self.reader.api_base + "/_apis/connectionData?api-version=7.1-preview.1" and not (
            url == self.reader.base + "?api-version=7.1"
            or url.startswith(self.reader.base + "/")
            or method == "GET" and url == policy_url
        ):
            raise ValueError("review request is outside the selected native PR")
        result = self.request(
            method, url, copy.deepcopy(body), query=copy.deepcopy(query),
            expected_binding=self.pinned, remaining=remaining,
        )
        if not isinstance(result, NativeReviewResponse) or type(result.status) is not int or not isinstance(result.body, dict):
            raise ReviewContextUnavailable("protected transport returned an invalid native response")
        if len(json.dumps(result.body, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
            raise ReviewContextUnavailable("native review response exceeds its byte bound")
        self.check()
        try:
            self.remaining()
        except TimeoutError:
            if method == "GET":
                raise
            return NativeReviewResponse(result.status, result.body, result.continuation, late=True)
        return result

    def read_state(self):
        identity = self.reader.identity().get("authorizedUser")
        if not isinstance(identity, dict) or identity.get("id") != self.scope["principal_id"]:
            raise ReviewContextUnavailable("actual write principal differs from selected native principal")
        pr = self.reader.pr()
        repository = pr.get("repository") or {}
        if not isinstance(repository, dict):
            raise ReviewContextUnavailable("native repository identity is malformed")
        project = repository.get("project") or {}
        if not isinstance(project, dict):
            raise ReviewContextUnavailable("native project identity is malformed")
        if (
            pr.get("pullRequestId") != self.scope["number"] or repository.get("id") != self.scope["repository_id"]
            or project.get("id") != self.scope["project_id"]
        ):
            raise ReviewContextUnavailable("native PR response differs from selected repository")
        iterations = self.reader.iterations()
        discussions = self.reader.threads()
        if not iterations.complete or not discussions.complete or not iterations.records:
            raise ReviewContextUnavailable("native review evidence is incomplete")
        if any(type(row.get("id")) is not int or row["id"] <= 0 for row in iterations.records):
            raise ReviewContextUnavailable("native source iteration identity is malformed")
        if len({row["id"] for row in iterations.records}) != len(iterations.records):
            raise ReviewContextUnavailable("native source iteration identity is duplicated")
        latest = max(iterations.records, key=lambda row: row.get("id", 0))
        source = latest.get("sourceRefCommit")
        common = latest.get("commonRefCommit")
        merged = pr.get("lastMergeSourceCommit")
        if any(not isinstance(value, dict) for value in (source, common, merged)):
            raise ReviewContextUnavailable("native source commit evidence is malformed")
        head = merged.get("commitId")
        if source.get("commitId") != head:
            raise ReviewContextUnavailable("native head/iteration evidence drifted")
        revision = {
            "head_sha": head, "iteration": latest.get("id"),
            "common_sha": common.get("commitId"),
        }
        return {"revision": revision, "discussions": copy.deepcopy(list(discussions.records)), "pr": pr}

    def read_decision_inputs(self):
        """Existing caller policy opts into fresh bound statuses/evaluations, not configurations."""
        statuses = self.reader.statuses()
        evaluations = self.reader.evaluations(self.scope["project_id"], self.scope["number"])
        return {
            "statuses": {"complete": statuses.complete, "data": list(statuses.records), "error": statuses.error},
            "policy_evaluations": {
                "complete": evaluations.complete, "data": list(evaluations.records), "error": evaluations.error,
            },
        }

    @staticmethod
    def marker(operation, action):
        value = {
            "scope": operation["intent"]["scope"], "operation_id": operation["intent"]["operation_id"],
            "intent_id": operation["intent_id"], "action_id": action["id"],
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def perform(self, operation, action):
        kind = action["kind"]
        base = self.reader.base
        if kind == "comment":
            body = {
                "comments": [{"parentCommentId": 0, "content": action["body"], "commentType": 1}],
                "status": 1,
                "properties": {"agent-pull-requests.action": {
                    "$type": "System.String", "$value": self.marker(operation, action),
                }},
            }
            result = self.call("POST", base + "/threads?api-version=7.1", body)
            comments = result.body.get("comments") or []
            if not isinstance(comments, list):
                comments = []
            ids = {"thread_id": result.body.get("id")}
            verified = False
            if comments and isinstance(comments[0], dict):
                ids["comment_id"] = comments[0].get("id")
                author = comments[0].get("author")
                verified = (
                    len(comments) == 1 and comments[0].get("content") == action["body"]
                    and isinstance(author, dict) and author.get("id") == self.scope["principal_id"]
                    and not comments[0].get("isDeleted")
                )
        elif kind in ("reply", "edit_owned_comment"):
            suffix = f"/threads/{action['thread_id']}/comments"
            method = "POST" if kind == "reply" else "PATCH"
            if kind == "edit_owned_comment":
                suffix += f"/{action['comment_id']}"
            body = {"content": action["body"]}
            if kind == "reply":
                body.update(parentCommentId=0, commentType=1)
            result = self.call(method, base + suffix + "?api-version=7.1", body)
            ids = {"thread_id": action["thread_id"], "comment_id": result.body.get("id")}
            author = result.body.get("author")
            verified = (
                result.body.get("content") == action["body"]
                and isinstance(author, dict) and author.get("id") == self.scope["principal_id"]
                and not result.body.get("isDeleted")
            )
        else:
            vote = {"APPROVED": 10, "CHANGES_REQUESTED": -10}[action["verdict"]]
            result = self.call(
                "PUT", base + "/reviewers/" + quote(self.scope["principal_id"], safe="") + "?api-version=7.1",
                {"vote": vote},
            )
            ids = {"reviewer_id": result.body.get("id")}
            verified = (
                type(result.body.get("vote")) is int and result.body["vote"] == vote
                and result.body.get("id") == self.scope["principal_id"]
            )
        if result.status not in (200, 201):
            raise ReviewContextUnavailable("native review effect reply is not confirmed")
        usable = {key: value for key, value in ids.items() if _native_id(value, numeric=key != "reviewer_id")}
        verified = verified and len(usable) == len(ids)
        ids = usable
        return NativeActionOutcome({"origin": "response", "platform_ids": ids, "proof": None}, verified and not result.late)

    def reconcile(self, operation, action):
        """Native create-thread markers only; current vote/content is never proof."""
        if action["kind"] != "comment":
            return None
        threads = self.reader.threads()
        if not threads.complete:
            return None
        matches = []
        for thread in threads.records:
            properties = thread.get("properties") or {}
            if not isinstance(properties, dict):
                return None
            marker = properties.get("agent-pull-requests.action") or {}
            comments = thread.get("comments") or []
            if not isinstance(comments, list) or any(not isinstance(comment, dict) for comment in comments):
                return None
            if isinstance(marker, dict) and marker.get("$value") == self.marker(operation, action):
                if (
                    not thread.get("isDeleted") and len(comments) == 1
                    and _native_id(thread.get("id"), numeric=True)
                    and _native_id(comments[0].get("id"), numeric=True)
                    and comments[0].get("content") == action["body"]
                    and isinstance(comments[0].get("author"), dict)
                    and comments[0]["author"].get("id") == self.scope["principal_id"]
                    and not comments[0].get("isDeleted")
                ):
                    matches.append((thread, comments[0]))
        if len(matches) != 1:
            return None
        thread, comment = matches[0]
        return {
            "origin": "attributable_readback",
            "platform_ids": {"thread_id": thread["id"], "comment_id": comment["id"],
                             "action_marker": self.marker(operation, action)},
            "proof": {"operation_id": operation["intent"]["operation_id"], "action_id": action["id"],
                      "scope_id": hashlib.sha256(json.dumps(self.scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                      "intent_id": operation["intent_id"]},
        }


@contextmanager
def protected_review_context(factory, scope, remaining):
    """Owner-supplied normal context; capability/type checks never authenticate it."""
    with factory() as context:
        if not isinstance(context, AdoReviewContext) or context.scope != scope:
            raise ReviewContextUnavailable("selected protected review context is unsupported")
        context.remaining = remaining
        context.check()
        yield context
        context.check()
