"""Contained native TLS requests, separate credentials and one-generation lifetime."""

import base64
import copy
import json
import socket
import ssl
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fleet_manager.gateway_client import CredentialMaterial
from fleet_manager.gateway_contract import ForgeScope, ReviewScope
from agent_worktrees.providers import azure_review_transport as native
from agent_worktrees.providers.azure_owned_review import protected_review_context, ReviewContextUnavailable
from test_pr_observation_credential_transports import certificate


PROJECT = "11111111-1111-4111-8111-111111111111"
REPOSITORY = "22222222-2222-4222-8222-222222222222"
NAMESPACE = "33333333-3333-4333-8333-333333333333"


def namespaces():
    return {"count": 1, "value": [{
        "name": "Git Repositories", "namespaceId": NAMESPACE, "separatorValue": "/",
        "structureValue": 1, "useTokenTranslator": True, "readPermission": 2,
        "actions": [
            {"name": name, "bit": bit, "namespaceId": NAMESPACE}
            for name, bit in (("GenericRead", 2), ("PullRequestContribute", 8))
        ],
    }]}


@pytest.fixture
def backend(tmp_path):
    observed = []
    control = {"failure": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            observed.append((self.command, self.path, self.headers.get("Authorization"), None))
            if "connectionData?" in self.path:
                body = {"authorizedUser": {"id": "principal"}}
            elif "/_apis/securitynamespaces?" in self.path:
                body = copy.deepcopy(control.get("namespaces", namespaces()))
            elif "/_apis/permissions/" in self.path:
                body = copy.deepcopy(control.get("permissions", {"count": 2, "value": [True, True]}))
            elif "/iterations?" in self.path:
                body = {"value": [{"id": 1, "sourceRefCommit": {"commitId": control.get("head", "a" * 40)},
                                   "commonRefCommit": {"commitId": "b" * 40}}]}
            elif "/threads?" in self.path:
                body = {"value": copy.deepcopy(control.get("threads", []))}
            elif any(route in self.path for route in ("/statuses?", "/policy/evaluations?")):
                body = {"value": []}
            else:
                body = {"pullRequestId": 42, "repository": {"id": REPOSITORY, "project": {"id": PROJECT}},
                        "targetRefName": control.get("target", "refs/heads/main"),
                        "lastMergeSourceCommit": {"commitId": control.get("head", "a" * 40)}}
            if callable(control.get("on_read")):
                control["on_read"]()
            if callable(control.get("on_permission_read")) and "/_apis/permissions/" in self.path:
                control["permission_reads"] = control.get("permission_reads", 0) + 1
                if control["permission_reads"] == 2:
                    control["on_permission_read"]()
            if control.get("identity"):
                body = {"authorizedUser": {"id": control["identity"]}}
            self.respond(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append((self.command, self.path, self.headers.get("Authorization"), body))
            if control["failure"] == "loss":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.command == "PUT":
                result = {"id": "principal", "vote": body["vote"]}
            elif "comments" in body:
                result = {"id": 3, "comments": [{"id": 5, "content": body["comments"][0]["content"],
                                               "author": {"id": "principal"}}]}
            else:
                result = {"id": 5, "content": body["content"], "author": {"id": "principal"}}
            self.respond(result)

        do_PATCH = do_POST
        do_PUT = do_POST

        def respond(self, body):
            failure = control["failure"]
            status = 302 if failure == "redirect" else 401 if failure == "auth" else 200
            raw = b"not-json" if failure == "malformed" else json.dumps(body).encode()
            if failure == "oversize":
                raw = b"x" * 2048
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                if failure == "stall":
                    self.wfile.write(raw[:1])
                    time.sleep(0.3)
                else:
                    self.wfile.write(raw)
            except OSError:
                pass

    cert, key = certificate(tmp_path)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}/org", str(cert), observed, control
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def selected(backend, *, authentication="bearer", actions=("comment", "reply", "edit_owned_comment", "verdict")):
    base, ca, _, _ = backend
    review = ReviewScope(base, f"{PROJECT}/{REPOSITORY}", authentication, PROJECT, REPOSITORY, 42, "principal", actions)
    scope = {"provider": "azure-devops", "endpoint": base, "project_id": PROJECT, "repository_id": REPOSITORY,
             "number": 42, "principal_id": "principal"}
    material = CredentialMaterial("synthetic-secret", time.time() + 60, "source", "account",
                                  "audience", ("review",), review=review)
    checks, acquisitions, policies = [], [], []

    def acquire():
        acquisitions.append(True)
        return material

    def policy(operation, action, current, *, context):
        policies.append(action["kind"])
        assert current["revision"]["head_sha"] == "a" * 40
        assert context.pinned is material

    kwargs = dict(
        review=review, credential_source=acquire, source="source", account="account",
        audience="audience", checkpoint=lambda actual: checks.append(actual),
        review_check=policy, cafile=ca,
    )
    return scope, material, kwargs, acquisitions, policies, checks


@pytest.mark.parametrize("authentication", ["bearer", "basic"])
def test_real_transport_keeps_private_generation_through_reads_and_all_actions(backend, authentication):
    scope, material, kwargs, acquisitions, policies, checks = selected(backend, authentication=authentication)
    factory = native.ado_review_context_factory(scope, **kwargs)
    operation = {"intent": {"scope": scope, "operation_id": "op"}, "intent_id": "intent"}
    actions = [
        {"id": "c", "kind": "comment", "body": "selected"},
        {"id": "r", "kind": "reply", "body": "selected", "thread_id": 3},
        {"id": "e", "kind": "edit_owned_comment", "body": "selected", "thread_id": 3, "comment_id": 5},
        {"id": "v", "kind": "verdict", "verdict": "APPROVED"},
    ]
    for action in actions:
        with protected_review_context(factory, scope, lambda: 5) as context:
            assert context.read_state()["revision"]["head_sha"] == "a" * 40
            assert context.perform(operation, action).confirmed
    assert len(acquisitions) == 1
    assert policies == [action["kind"] for action in actions for _ in range(2)]
    assert checks and all(actual is material for actual in checks)
    observed = backend[2]
    expected = material.token if authentication == "bearer" else base64.b64encode(
        (":" + material.token).encode()).decode()
    for _, _, header, _ in observed:
        scheme, value = header.split(" ", 1)
        assert scheme == ("Bear" + "er" if authentication == "bearer" else "Bas" + "ic")
        assert value == expected
    assert [row[0] for row in observed if row[0] != "GET"] == ["POST", "POST", "PATCH", "PUT"]
    permission_reads = [row for row in observed if "/_apis/permissions/" in row[1]]
    assert len(permission_reads) == 16
    assert all(f"/_apis/permissions/{NAMESPACE}/{bit}?" in row[1]
               for row, bit in zip(permission_reads, [2, 8] * 8))


@pytest.mark.parametrize("wrong", ["observe", "endpoint", "repository", "principal", "pr", "action", "expired",
                                 "source", "account", "audience", "token"])
def test_material_substitution_refuses_without_native_request(backend, wrong):
    scope, material, kwargs, _, _, _ = selected(backend)
    changes = {
        "endpoint": {"api_base": "https://other.invalid/org"}, "repository": {"repository_id": "other"},
        "principal": {"principal_id": "other"}, "pr": {"number": 43}, "action": {"actions": ("comment",)},
    }
    if wrong == "observe":
        material = replace(material, review=None, forge=ForgeScope(
            "azure-devops", scope["endpoint"], "project/repository", "observe", "bearer"))
    elif wrong in changes:
        material = replace(material, review=replace(material.review, **changes[wrong]))
    else:
        material = replace(material, **{
            {"expired": "expires_at", "token": "token"}.get(wrong, wrong):
            time.time() - 1 if wrong == "expired" else "bad\nsecret" if wrong == "token" else "other",
        })
    kwargs["credential_source"] = lambda: material
    with pytest.raises(ReviewContextUnavailable):
        native.ado_review_context_factory(scope, **kwargs)
    assert backend[2] == []


def test_invalid_selection_or_missing_policy_refuses_before_acquisition(backend):
    scope, _, kwargs, acquired, _, _ = selected(backend)
    for options, target in [(dict(kwargs, review_check=None), scope), (kwargs, dict(scope, number=43))]:
        with pytest.raises(ReviewContextUnavailable):
            native.ado_review_context_factory(target, **options)
    assert acquired == [] and backend[2] == []


def test_rotation_revocation_and_policy_refuse_before_native_write(backend):
    scope, _, kwargs, acquired, _, _ = selected(backend)
    revoked = [False]

    def check(_):
        if revoked[0]:
            raise PermissionError("current owner withdrawn")

    kwargs["checkpoint"] = check
    factory = native.ado_review_context_factory(scope, **kwargs)
    operation = {"intent": {"scope": scope, "operation_id": "op"}, "intent_id": "intent"}
    with protected_review_context(factory, scope, lambda: 5) as context:
        context.read_state()
        revoked[0] = True
        with pytest.raises(PermissionError):
            context.perform(operation, {"kind": "comment", "body": "no"})
        revoked[0] = False

        def deny(*args, **kwargs):
            raise PermissionError("action policy withdrawn")

        context.review_check = deny
        with pytest.raises(PermissionError):
            context.perform(operation, {"kind": "comment", "body": "no"})
    assert len(acquired) == 1 and all(row[0] == "GET" for row in backend[2])


def test_no_arbitrary_route_or_unguarded_write(backend):
    scope, _, kwargs, _, _, _ = selected(backend)
    factory = native.ado_review_context_factory(scope, **kwargs)
    with protected_review_context(factory, scope, lambda: 5) as context:
        with pytest.raises(ReviewContextUnavailable):
            context.call("POST", context.reader.base + "/threads?api-version=7.1", {})
        with pytest.raises((ValueError, ReviewContextUnavailable)):
            context.call("GET", context.reader.base + "/../other", None)
    assert backend[2] == []


def test_current_dispatch_policy_and_native_principal_cannot_be_bypassed(backend):
    scope, _, kwargs, acquired, _, _ = selected(backend)
    calls = []

    def deny_dispatch(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise PermissionError("dispatch policy withdrawn")

    kwargs["review_check"] = deny_dispatch
    factory = native.ado_review_context_factory(scope, **kwargs)
    operation = {"intent": {"scope": scope, "operation_id": "op"}, "intent_id": "intent"}
    action = {"id": "c", "kind": "comment", "body": "selected"}
    with protected_review_context(factory, scope, lambda: 5) as context:
        with pytest.raises(PermissionError):
            context.perform(operation, action)
        backend[3]["identity"] = "other"
        with pytest.raises(ReviewContextUnavailable):
            context.perform(operation, action)
    assert len(calls) == 2 and len(acquired) == 1
    assert all(row[0] == "GET" for row in backend[2])


def test_actual_action_policy_reads_share_the_pinned_transport(backend):
    scope, material, kwargs, _, _, _ = selected(backend)
    observed_policy = []

    def policy(operation, action, current, *, context):
        inputs = context.read_decision_inputs()
        assert inputs["statuses"]["complete"] and inputs["policy_evaluations"]["complete"]
        observed_policy.append(inputs)

    kwargs["review_check"] = policy
    factory = native.ado_review_context_factory(scope, **kwargs)
    operation = {"intent": {"scope": scope, "operation_id": "op"}, "intent_id": "intent"}
    with protected_review_context(factory, scope, lambda: 5) as context:
        assert context.perform(operation, {"id": "c", "kind": "comment", "body": "selected"}).confirmed
    assert len(observed_policy) == 2
    assert sum("/policy/evaluations?" in row[1] for row in backend[2]) == 2
    assert all(row[2].split(" ", 1)[1] == material.token for row in backend[2])


@pytest.mark.parametrize("when", ["preflight", "dispatch"])
def test_effective_branch_denial_refuses_without_write_or_reacquisition(backend, when):
    scope, _, kwargs, acquired, policies, _ = selected(backend)
    if when == "preflight":
        backend[3]["permissions"] = {"count": 2, "value": [True, False]}
    else:
        backend[3]["on_permission_read"] = lambda: backend[3].update(
            permissions={"count": 2, "value": [True, False]},
        )
    factory = native.ado_review_context_factory(scope, **kwargs)
    with protected_review_context(factory, scope, lambda: 5) as context:
        with pytest.raises(PermissionError):
            context.perform({"intent": {"scope": scope, "operation_id": "op"}, "intent_id": "intent"},
                            {"id": "c", "kind": "comment", "body": "selected"})
    assert len(acquired) == 1 and len(policies) == int(when == "dispatch")
    assert all(row[0] == "GET" for row in backend[2])


def test_incomplete_native_permissions_are_unavailable_not_policy_success(backend):
    scope, _, kwargs, acquired, policies, _ = selected(backend)
    backend[3]["permissions"] = {"count": 1, "value": [True]}
    factory = native.ado_review_context_factory(scope, **kwargs)
    with protected_review_context(factory, scope, lambda: 5) as context:
        with pytest.raises(ReviewContextUnavailable):
            context.perform({"intent": {"scope": scope, "operation_id": "op"}, "intent_id": "intent"},
                            {"id": "c", "kind": "comment", "body": "selected"})
    assert len(acquired) == 1 and not policies and all(row[0] == "GET" for row in backend[2])


def test_expired_pinned_generation_is_not_reacquired(backend, monkeypatch):
    from types import SimpleNamespace

    scope, material, kwargs, acquired, _, _ = selected(backend)
    factory = native.ado_review_context_factory(scope, **kwargs)
    monkeypatch.setattr(native, "time", SimpleNamespace(time=lambda: material.expires_at + 1))
    with pytest.raises(ReviewContextUnavailable):
        with factory():
            pytest.fail("expired factory entered")
    assert len(acquired) == 1 and backend[2] == []


def test_missing_optional_provider_refuses_before_acquisition(backend, monkeypatch):
    import sys

    scope, _, kwargs, acquired, _, _ = selected(backend)
    monkeypatch.setitem(sys.modules, "fleet_manager.gateway_transport", None)
    with pytest.raises(ReviewContextUnavailable):
        native.ado_review_context_factory(scope, **kwargs)
    assert acquired == [] and backend[2] == []


@pytest.mark.parametrize("failure", ["redirect", "auth", "malformed", "oversize", "stall", "loss"])
def test_native_failures_are_finite_secret_free_and_never_retried(backend, monkeypatch, failure):
    scope, material, kwargs, acquired, _, _ = selected(backend)
    factory = native.ado_review_context_factory(scope, **kwargs)
    backend[3]["failure"] = failure
    if failure == "oversize":
        monkeypatch.setattr(native, "MAX_RESPONSE_BYTES", 1024)
    start = time.monotonic()
    with protected_review_context(factory, scope, lambda: 0.1 if failure == "stall" else 5) as context:
        if failure == "loss":
            # Reach the real effect request without making the identity fixture fail first.
            backend[3]["failure"] = None
            original = context.review_check

            def policy(*args, **kwargs):
                original(*args, **kwargs)
                backend[3]["failure"] = "loss"

            context.review_check = policy
            with pytest.raises(ReviewContextUnavailable) as error:
                context.perform({"intent": {"scope": scope, "operation_id": "op"}, "intent_id": "intent"},
                                {"id": "c", "kind": "comment", "body": "selected"})
            assert sum(row[0] == "POST" for row in backend[2]) == 1
        else:
            with pytest.raises(ReviewContextUnavailable) as error:
                context.reader.identity()
            assert len(backend[2]) == 1
        assert material.token not in str(error.value)
    assert time.monotonic() - start < 3 and len(acquired) == 1


@pytest.fixture
def native_execution(backend, tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-pull-requests" / "src"))
    from agent_pull_requests.review_workspace import _digest, build_generation
    from agent_pull_requests.watch_replay_store import ReplayStore

    scope, _, kwargs, acquired, _, _ = selected(backend)
    thread = {"id": 3, "status": 1, "comments": [{
        "id": 5, "content": "original", "author": {"id": "principal"},
        "commentType": 1, "parentCommentId": 0,
    }]}
    backend[3]["threads"] = [copy.deepcopy(thread)]
    evidence = json.loads((root / "plugins" / "agent-pull-requests" / "docs" /
                           "review-evidence.example.json").read_text())
    evidence["scope"] = copy.deepcopy(scope)
    pr = evidence["sections"]["pull_request"]["data"]
    pr["repository"] = {"id": REPOSITORY, "project": {"id": PROJECT}}
    iteration = evidence["sections"]["iterations"]["data"][0]
    iteration["id"] = 1
    evidence["sections"]["changes"]["revision"]["iteration"] = 1
    evidence["sections"]["discussions"] = {
        "observed_from": "2026-10-30T00:00:03Z", "observed_to": "2026-10-30T00:00:04Z",
        "data": [thread], "complete": True, "errors": [],
    }
    evidence["sections"]["changes"]["data"] = []
    generation = build_generation(evidence)["generation"]
    source = {
        "schema": "agent-pull-requests.local-source", "version": 1, "scope": scope,
        "revision": generation["revision"], "complete": True, "errors": [], "files": [],
        "workspace_id": "synthetic-source",
    }
    source["id"] = _digest(source)
    attachment = {"state": "ready", "scope": scope, "generation_id": generation["id"], "source": source}
    intent = {
        "schema": "agent-pull-requests.review-intent", "version": 1, "operation_id": "lost-native-reply",
        "scope": scope, "generation_id": generation["id"], "source_id": source["id"],
        "revision": {key: generation["revision"][key] for key in ("head_sha", "common_sha", "iteration")},
        "actions": [{"id": "c", "kind": "comment", "body": "selected"}],
    }
    expiry = int(time.time()) + 600
    store = ReplayStore(tmp_path / "replay", "native-owner")
    store.provision()
    store.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=expiry)
    return store, intent, generation, attachment, kwargs, acquired, expiry


def test_real_executor_retains_lost_native_effect_across_reopen_without_resend(backend, tmp_path, native_execution):
    from agent_pull_requests.review_executor import execute_review
    from agent_pull_requests.watch_replay_store import ReplayStore

    store, intent, generation, attachment, kwargs, acquired, expiry = native_execution
    scope = intent["scope"]
    original = kwargs["review_check"]

    def policy(operation, *args, **options):
        original(operation, *args, **options)
        retained = store.inspect_review(intent["operation_id"], scope, expires_at=expiry)
        if retained["actions"][0]["state"] == "unknown":
            assert retained["actions"][0]["attempt_id"]
            backend[3]["failure"] = "loss"

    kwargs["review_check"] = policy
    factory = native.ado_review_context_factory(scope, **kwargs)

    def execute(owner):
        return execute_review(
            owner, intent, generation, attachment, context_factory=factory,
            checkpoint=lambda: None, expires_at=expiry, budget_seconds=10,
        )

    result = execute(store)
    assert result["status"] == "unknown" and result["entered_writes"] == 1
    attempt = result["actions"][0]["attempt_id"]
    assert result["actions"][0]["state"] == "unknown"
    backend[3]["failure"] = None
    reopened = ReplayStore(tmp_path / "replay", "native-owner")
    result = execute(reopened)
    assert result["status"] == "unknown" and result["entered_writes"] == 0
    assert result["actions"][0]["attempt_id"] == attempt
    assert sum(row[0] == "POST" for row in backend[2]) == 1 and len(acquired) == 1


@pytest.mark.parametrize("after_fence", [False, True])
def test_executor_native_permission_denial_preserves_attempt_boundary(backend, native_execution, after_fence):
    from agent_pull_requests.review_executor import execute_review

    store, intent, generation, attachment, kwargs, acquired, expiry = native_execution
    if after_fence:
        backend[3]["on_permission_read"] = lambda: backend[3].update(
            permissions={"count": 2, "value": [True, False]},
        )
    else:
        backend[3]["permissions"] = {"count": 2, "value": [True, False]}
    factory = native.ado_review_context_factory(intent["scope"], **kwargs)
    with pytest.raises(PermissionError):
        execute_review(
            store, intent, generation, attachment, context_factory=factory,
            checkpoint=lambda: None, expires_at=expiry, budget_seconds=10,
        )
    retained = store.inspect_review(intent["operation_id"], intent["scope"], expires_at=expiry)
    action = retained["actions"][0]
    assert action["state"] == ("unknown" if after_fence else "unattempted")
    assert bool(action["attempt_id"]) is after_fence
    assert all(row[0] == "GET" for row in backend[2]) and len(acquired) == 1


@pytest.mark.parametrize("drift", ["head", "owned_content"])
def test_executor_rechecks_observed_connect_time_drift_before_dispatch(backend, native_execution, drift):
    from agent_pull_requests.review_executor import execute_review
    from agent_pull_requests.review_workspace import _digest

    store, intent, generation, attachment, kwargs, _, expiry = native_execution
    if drift == "owned_content":
        intent["actions"] = [{
            "id": "edit", "kind": "edit_owned_comment", "thread_id": 3, "comment_id": 5,
            "body": "selected", "expected_content_id": _digest("original"),
        }]
    calls = []

    def policy(*args, **kwargs):
        calls.append(True)
        if len(calls) == 3:
            if drift == "head":
                backend[3]["head"] = "d" * 40
            else:
                backend[3]["threads"][0]["comments"][0]["content"] = "newer native edit"

    kwargs["review_check"] = policy
    factory = native.ado_review_context_factory(intent["scope"], **kwargs)
    result = execute_review(
        store, intent, generation, attachment, context_factory=factory,
        checkpoint=lambda: None, expires_at=expiry, budget_seconds=10,
    )
    assert result["status"] in ("unknown", "candidate_changed")
    assert result["actions"][0]["state"] == "unknown" and result["actions"][0]["attempt_id"]
    assert len(calls) == 3 and all(row[0] == "GET" for row in backend[2])
