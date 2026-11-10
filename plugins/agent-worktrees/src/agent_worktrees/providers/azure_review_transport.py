"""Optional native review factory; no host enrollment or ambient authentication."""

from __future__ import annotations

import base64
import copy
import json
import math
import re
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from http.client import HTTPException
from urllib.parse import quote, urlencode, urlsplit

from .azure_observation_reads import MAX_RESPONSE_BYTES
from .azure_owned_review import AdoReviewContext, NativeReviewResponse, ReviewContextUnavailable


def _unavailable():
    return ReviewContextUnavailable("selected native review transport is unavailable")


def ado_review_context_factory(
    scope, *, review, credential_source, source, account, audience, checkpoint, review_check,
    cafile=None,
):
    """Acquire once, then retain that exact material for every context from this factory.

    The trusted custodian supplies bounded acquisition and a current-owner check
    accepting the actual material reference (including revocation/generation).
    Review policy must raise on denial; there is deliberately no default policy.
    """
    try:
        from fleet_manager.gateway_client import CredentialMaterial
        from fleet_manager.gateway_contract import ReviewScope
        from fleet_manager.gateway_transport import DeadlineHTTPSConnection
    except ImportError:
        raise _unavailable() from None
    if (
        not isinstance(review, ReviewScope)
        or scope != {
            "provider": "azure-devops",
            "endpoint": review.api_base, "project_id": review.project_id,
            "repository_id": review.repository_id, "number": review.number,
            "principal_id": review.principal_id,
        }
        or not all(callable(value) for value in (credential_source, checkpoint, review_check))
        or any(not isinstance(value, str) or not value.strip() for value in (source, account, audience))
    ):
        raise _unavailable()
    scope = copy.deepcopy(scope)
    try:
        tls = ssl.create_default_context(cafile=cafile)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        material = credential_source()
    except (OSError, RuntimeError, ValueError):
        raise _unavailable() from None

    def check():
        if (
            not isinstance(material, CredentialMaterial) or material.review != review
            or material.forge is not None
            or (material.source, material.account, material.audience) != (source, account, audience)
            or type(material.expires_at) not in (int, float)
            or not math.isfinite(material.expires_at) or material.expires_at <= time.time()
            or not isinstance(material.token, str) or not 0 < len(material.token) <= 16384
            or not material.token.isascii()
            or any(c.isspace() or not c.isprintable() for c in material.token)
        ):
            raise _unavailable()
        try:
            checkpoint(material)
        except PermissionError:
            raise
        except (OSError, ValueError, RuntimeError):
            raise _unavailable() from None

    check()
    address = urlsplit(review.api_base)
    base = (
        review.api_base + "/" + quote(review.project_id, safe="")
        + "/_apis/git/repositories/" + quote(review.repository_id, safe="")
        + f"/pullRequests/{review.number}"
    )
    policy = review.api_base + "/" + quote(review.project_id, safe="") + "/_apis/policy/evaluations?" + urlencode({
        "artifactId": f"vstfs:///CodeReview/CodeReviewId/{review.project_id}/{review.number}",
        "includeNotApplicable": "true", "api-version": "7.1-preview.1",
    })
    reads = {
        review.api_base + "/_apis/connectionData?api-version=7.1-preview.1",
        base + "?api-version=7.1", policy,
        *(base + "/" + name + "?api-version=7.1" for name in ("iterations", "threads", "statuses")),
    }

    class NativeContext(AdoReviewContext):
        def __init__(self):
            super().__init__(
                scope, request=self._request, credential_binding=lambda: material,
                checkpoint=check, review_check=review_check,
            )
            self._selected = None

        def check_review(self, operation: dict, action: dict, current: dict) -> None:
            self.require_review_permissions(current)
            super().check_review(operation, action, current)

        def perform(self, operation, action):
            if (
                self._selected is not None or action["kind"] not in review.actions
                or operation.get("intent", {}).get("scope") != self.scope
            ):
                raise _unavailable()
            current = self.read_state()
            self.check_review(copy.deepcopy(operation), copy.deepcopy(action), current)
            self._selected = (copy.deepcopy(operation), copy.deepcopy(action))
            try:
                return super().perform(operation, action)
            finally:
                self._selected = None

        def _write_matches(self, method, url, body):
            if self._selected is None:
                return False
            operation, action = self._selected
            kind = action["kind"]
            if kind == "comment":
                expected = {
                    "comments": [{"parentCommentId": 0, "content": action["body"], "commentType": 1}],
                    "status": 1, "properties": {"agent-pull-requests.action": {
                        "$type": "System.String", "$value": self.marker(operation, action),
                    }},
                }
                return (method, url, body) == ("POST", base + "/threads?api-version=7.1", expected)
            if kind in ("reply", "edit_owned_comment"):
                if type(action["thread_id"]) is not int or action["thread_id"] <= 0:
                    return False
                route = base + f"/threads/{action['thread_id']}/comments"
                expected = {"content": action["body"]}
                verb = "POST"
                if kind == "reply":
                    expected.update(parentCommentId=0, commentType=1)
                else:
                    if type(action["comment_id"]) is not int or action["comment_id"] <= 0:
                        return False
                    route += f"/{action['comment_id']}"
                    verb = "PATCH"
                return (method, url, body) == (verb, route + "?api-version=7.1", expected)
            return (method, url, body) == (
                "PUT", base + "/reviewers/" + quote(review.principal_id, safe="") + "?api-version=7.1",
                {"vote": {"APPROVED": 10, "CHANGES_REQUESTED": -10}[action["verdict"]]},
            )

        def _request(self, method, url, body, *, query, expected_binding, remaining):
            check()
            if expected_binding is not material:
                raise _unavailable()
            if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
                raise _unavailable()
            if method == "GET":
                changes = re.fullmatch(re.escape(base) + r"/iterations/[1-9][0-9]*/changes\?api-version=7\.1", url)
                keys = {"$compareTo", "$top", "$skip"} if changes else {"$top", "$skip"}
                permission_read = url == self._permission_url
                if (url not in reads and not changes and not permission_read) or body is not None or (
                    query is not None and ((url != policy and not changes) or set(query) != keys
                                           or any(type(v) is not int or v < 0 for v in query.values()))
                ):
                    raise _unavailable()
            elif query is not None or not self._write_matches(method, url, body):
                raise _unavailable()
            deadline = time.monotonic() + min(remaining, material.expires_at - time.time())
            raw = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
            if raw is not None and len(raw) > MAX_RESPONSE_BYTES:
                raise _unavailable()
            if review.authentication == "basic":
                scheme = "Bas" + "ic "
                value = base64.b64encode((":" + material.token).encode()).decode("ascii")
            else:
                scheme = "Bear" + "er "
                value = material.token
            headers = {"Authorization": scheme + value, "Accept": "application/json",
                       "Content-Type": "application/json", "Connection": "close"}
            target = urlsplit(url)
            route = target.path + "?" + target.query
            if query:
                route += "&" + urlencode(query)
            connection = DeadlineHTTPSConnection(
                address.hostname, address.port or 443, context=tls, deadline=deadline,
            )
            transport = None

            def expire():
                if transport is not None:
                    try:
                        transport.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
            timer.start()
            try:
                connection.connect()
                transport = connection.sock
                check()
                if method != "GET":
                    operation, action = self._selected
                    remaining_before_dispatch = self.remaining
                    self.remaining = lambda: min(remaining_before_dispatch(), deadline - time.monotonic())
                    try:
                        current = self.read_state()
                        self.check_review(copy.deepcopy(operation), copy.deepcopy(action), current)
                    finally:
                        self.remaining = remaining_before_dispatch
                    check()
                if time.monotonic() >= deadline:
                    raise _unavailable()
                connection.request(method, route, body=raw, headers=headers)
                with connection.getresponse() as response:
                    if not 200 <= response.status < 300:
                        raise _unavailable()
                    received = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(received) > MAX_RESPONSE_BYTES or response.length not in (None, 0):
                        raise _unavailable()
                    result = json.loads(received)
                    if not isinstance(result, dict):
                        raise _unavailable()
                    continuation = response.getheader("x-ms-continuationtoken", "")
                    return NativeReviewResponse(
                        response.status, result, continuation, late=time.monotonic() >= deadline,
                    )
            except PermissionError:
                raise
            except (HTTPException, OSError, ValueError, RuntimeError):
                raise _unavailable() from None
            finally:
                timer.cancel()
                timer.join()
                connection.close()

    @contextmanager
    def factory():
        check()
        yield NativeContext()
        check()

    return factory
