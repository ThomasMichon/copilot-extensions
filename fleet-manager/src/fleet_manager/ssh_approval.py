"""Explicit peer admission through the selected running SSH service."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib

from .gateway_service_channel import check
from .gateway_ssh_policy import ENVIRONMENTS
from .gateway_ssh_provider import public_key
from .planning import canonical, decode, identifier, integer, record
from .ssh_directory import MAX_RESPONSE, SSHDirectoryClient


def candidate_fingerprint(candidate: dict) -> str:
    record(candidate, "agent-ssh.enrollment-candidate", {
        "fleet", "gateway", "slot", "controller", "requester", "binding",
        "policy_digest", "public_key", "host_key", "relay_public_key",
    })
    for name in ("fleet", "gateway", "slot", "controller", "requester"):
        identifier(candidate[name], "SSH enrollment identity")
    binding = candidate["binding"]
    check(isinstance(binding, dict) and set(binding) == {
        "machine", "environment", "principal", "generation", "alias", "user", "reverse_port",
    }, "invalid-ssh-enrollment-binding")
    for name in ("machine", "principal", "alias"):
        identifier(binding[name], "SSH enrollment binding")
    integer(binding["generation"], "SSH enrollment generation", minimum=1)
    check(binding["principal"] == candidate["requester"]
          and binding["environment"] in ENVIRONMENTS
          and isinstance(binding["user"], str) and 1 <= len(binding["user"]) <= 64,
          "invalid-ssh-enrollment-binding")
    if binding["reverse_port"] is not None:
        integer(binding["reverse_port"], "SSH reverse port", minimum=1, maximum=65535)
    check(isinstance(candidate["policy_digest"], str)
          and len(candidate["policy_digest"]) == 64, "invalid-ssh-enrollment-policy-digest")
    public_key(candidate["public_key"])
    for name in ("host_key", "relay_public_key"):
        if candidate[name] is not None:
            public_key(candidate[name])
    digest = hashlib.sha256(canonical(candidate)).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class EnrollmentResult:
    encoded: bytes

    @classmethod
    def parse(cls, raw: bytes) -> EnrollmentResult:
        value = decode(raw)
        record(value, "agent-ssh.enrollment-result", {
            "status", "request_id", "candidate", "fingerprint", "decision",
        })
        identifier(value["request_id"], "SSH enrollment request")
        check(value["fingerprint"] == candidate_fingerprint(value["candidate"]),
              "invalid-ssh-enrollment-fingerprint")
        check(value["status"] in ("pending", "active", "superseded"),
              "invalid-ssh-enrollment-status")
        if value["status"] == "pending":
            check(value["decision"] is None,
                  "pending-ssh-enrollment-has-no-admission")
        else:
            decision = value["decision"]
            check(isinstance(decision, dict)
                  and decision.get("method") in ("peer", "recovery", "retained"),
                  "invalid-ssh-enrollment-decision")
            fields = {"method", "principal", "directory_revision"}
            integer(decision.get("directory_revision"), "SSH decision directory revision", minimum=1)
            if decision["method"] == "recovery":
                fields.add("reason")
                check(isinstance(decision.get("reason"), str)
                      and bool(decision["reason"].strip()) and len(decision["reason"]) <= 512,
                      "invalid-ssh-recovery-reason")
            check(set(decision) == fields, "invalid-ssh-enrollment-decision")
            if decision["method"] == "retained":
                check(decision["principal"] is None, "invalid-retained-ssh-enrollment")
            else:
                identifier(decision["principal"], "SSH enrollment approver")
        return cls(canonical(value))

    def to_dict(self) -> dict:
        return decode(self.encoded)


class PeerEnrollmentClient:
    """No implicit approval, recovery, key acquisition or compatibility replay."""

    def __init__(self, caller, *, namespace="/ssh"):
        directory = SSHDirectoryClient(caller, namespace=namespace)
        self.caller, self.namespace = directory.caller, directory.namespace

    async def _require_policy(self) -> None:
        response = await self.caller.request(
            "GET", self.namespace + "/enrollment", response_limit=MAX_RESPONSE,
        )
        check(response.status == 200, "ssh-peer-approval-unsupported", response.status)
        value = decode(response.body)
        record(value, "agent-ssh.enrollment-contract", {"policy", "peer_approval"})
        check(value["policy"] == "peer-approval" and value["peer_approval"] is True,
              "ssh-peer-approval-not-selected")

    async def submit(self, request: dict) -> EnrollmentResult:
        check(isinstance(request, dict) and request.get("enrollment_policy", "peer-approval")
              == "peer-approval", "ssh-peer-approval-required")
        selected = {**request, "enrollment_policy": "peer-approval"}
        await self._require_policy()
        response = await self.caller.request(
            "POST", self.namespace + "/enroll", body=canonical(selected),
            response_limit=MAX_RESPONSE,
        )
        check(response.status in (200, 202), "ssh-enrollment-refused", response.status)
        result = EnrollmentResult.parse(response.body)
        value, candidate = result.to_dict(), result.to_dict()["candidate"]
        check(value["request_id"] == request["request_id"]
              and all(candidate["binding"][name] == request[name] for name in (
                  "machine", "environment", "generation"))
              and all(candidate[name] == request.get(name) for name in (
                  "public_key", "host_key", "relay_public_key")),
              "ssh-enrollment-result-mismatch")
        check((response.status == 202) == (value["status"] == "pending"),
              "ssh-enrollment-status-mismatch")
        return result

    async def pending(self, *, after: str | None = None) -> dict:
        if after is not None:
            identifier(after, "SSH pending cursor")
        await self._require_policy()
        response = await self.caller.request(
            "GET", self.namespace + "/enrollment/pending"
            + ("?after=" + after if after is not None else ""),
            response_limit=MAX_RESPONSE,
        )
        check(response.status == 200, "ssh-pending-enrollment-refused", response.status)
        value = decode(response.body)
        record(value, "agent-ssh.enrollment-pending", {"requests", "next"})
        check(isinstance(value["requests"], list) and len(value["requests"]) <= 32,
              "invalid-ssh-pending-enrollments")
        prior = after
        for item in value["requests"]:
            check(isinstance(item, dict) and set(item) == {
                "request_id", "candidate", "fingerprint",
            }, "invalid-ssh-pending-enrollment")
            identifier(item["request_id"], "SSH enrollment request")
            check(item["fingerprint"] == candidate_fingerprint(item["candidate"]),
                  "invalid-ssh-enrollment-fingerprint")
            requester = item["candidate"]["requester"]
            check(prior is None or requester > prior, "invalid-ssh-pending-order")
            prior = requester
        if value["next"] is not None:
            identifier(value["next"], "SSH pending cursor")
            check(bool(value["requests"]) and value["next"] == prior,
                  "invalid-ssh-pending-cursor")
        return value

    async def status(self, request_id: str) -> EnrollmentResult:
        identifier(request_id, "SSH enrollment request")
        await self._require_policy()
        response = await self.caller.request(
            "GET", self.namespace + "/enrollment/requests/" + request_id,
            response_limit=MAX_RESPONSE,
        )
        check(response.status == 200, "ssh-enrollment-status-refused", response.status)
        result = EnrollmentResult.parse(response.body)
        check(result.to_dict()["request_id"] == request_id, "ssh-enrollment-result-mismatch")
        return result

    async def approve(self, requester: str, request_id: str,
                      fingerprint: str) -> EnrollmentResult:
        return await self._decide("approve", requester, request_id, fingerprint)

    async def recover(self, requester: str, request_id: str, fingerprint: str, *,
                      confirm: bool, reason: str) -> EnrollmentResult:
        check(confirm is True and isinstance(reason, str)
              and bool(reason.strip()) and len(reason) <= 512
              and not any(ord(char) < 32 for char in reason),
              "explicit-ssh-recovery-confirmation-required")
        return await self._decide(
            "recover", requester, request_id, fingerprint, confirm=True, reason=reason,
        )

    async def _decide(self, operation: str, requester: str, request_id: str,
                      fingerprint: str, **extra) -> EnrollmentResult:
        identifier(requester, "SSH enrollment requester")
        identifier(request_id, "SSH enrollment request")
        check(isinstance(fingerprint, str) and len(fingerprint) == 50,
              "invalid-ssh-enrollment-fingerprint")
        await self._require_policy()
        response = await self.caller.request(
            "POST", self.namespace + "/enrollment/" + operation,
            body=canonical({
                "requester": requester, "request_id": request_id,
                "fingerprint": fingerprint, **extra,
            }), response_limit=MAX_RESPONSE,
        )
        check(response.status == 200, "ssh-enrollment-decision-refused", response.status)
        result = EnrollmentResult.parse(response.body)
        value = result.to_dict()
        check(value["status"] == "active" and value["request_id"] == request_id
              and value["candidate"]["requester"] == requester
              and value["fingerprint"] == fingerprint, "ssh-enrollment-result-mismatch")
        return result
