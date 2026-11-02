"""Explicit public-identity decisions inside the directory owner's transaction."""

from __future__ import annotations

import base64
import hashlib

from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy
from fleet_manager.gateway_service_channel import BODY_BYTES
from fleet_manager.planning import canonical, decode, identifier

from .config import check


class PeerApproval:
    def __init__(self, store):
        self.store = store

    def contract(self) -> dict:
        return {
            "schema": "agent-ssh.enrollment-contract", "version": 1,
            "policy": self.store.config.enrollment_mode,
            "peer_approval": self.store.config.enrollment_mode == "peer-approval",
        }

    def _require_policy(self) -> None:
        check(self.store.config.enrollment_mode == "peer-approval",
              "peer-approval-not-selected")

    def _candidate(self, principal: str, request: dict) -> dict:
        binding = self.store._validate_enrollment(principal, request)
        selected = self.store.config.to_dict()
        return {
            "schema": "agent-ssh.enrollment-candidate", "version": 1,
            **{name: selected[name] for name in ("fleet", "gateway", "slot", "controller")},
            "requester": principal, "binding": binding,
            "policy_digest": GatewaySSHPolicy.from_dict(selected["policy"]).policy_digest,
            "public_key": request["public_key"], "host_key": request["host_key"],
            "relay_public_key": request.get("relay_public_key"),
        }

    @staticmethod
    def _fingerprint(candidate: dict) -> str:
        digest = hashlib.sha256(canonical(candidate)).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")

    def _active(self, db, principal: str) -> tuple | None:
        binding = self.store.config.binding(principal)
        policy = GatewaySSHPolicy.from_dict(self.store.config.to_dict()["policy"])
        if policy.resolve(binding["machine"], binding["environment"]).to_dict()["state"] != "configured":
            return None
        return db.execute(
            "SELECT e.public_key,e.host_key,r.public_key FROM enrollments e "
            "LEFT JOIN relay_keys r ON e.machine=r.machine AND e.environment=r.environment "
            "AND e.generation=r.generation "
            "WHERE e.machine=? AND e.environment=? AND e.generation=?",
            (binding["machine"], binding["environment"], binding["generation"]),
        ).fetchone()

    def _load(self, db, principal: str, request_id: str, *, current=True) -> tuple:
        identifier(principal, "SSH enrollment requester")
        identifier(request_id, "SSH enrollment request")
        row = db.execute(
            "SELECT request,candidate,fingerprint,decision FROM enrollment_pending "
            "WHERE principal=? AND request_id=?", (principal, request_id),
        ).fetchone()
        check(row is not None, "pending-enrollment-unavailable")
        request, candidate = decode(row[0]), decode(row[1])
        decision = decode(row[3]) if row[3] is not None else None
        if current or decision is None:
            check(candidate == self._candidate(principal, request), "stale-pending-enrollment")
        return request, candidate, row[2], decision

    def _retain(self, db, principal: str, fingerprint: str) -> dict:
        prior = db.execute(
            "SELECT decision FROM enrollment_pending "
            "WHERE principal=? AND fingerprint=? AND decision IS NOT NULL ORDER BY rowid LIMIT 1",
            (principal, fingerprint),
        ).fetchone()
        decision = decode(prior[0]) if prior is not None else {
            "method": "retained", "principal": None,
            "directory_revision": db.execute("SELECT revision FROM metadata WHERE id=1").fetchone()[0],
        }
        db.execute(
            "UPDATE enrollment_pending SET decision=? "
            "WHERE principal=? AND fingerprint=? AND decision IS NULL",
            (canonical(decision), principal, fingerprint),
        )
        return decision

    def _result(self, db, principal: str, request_id: str, *, current=True) -> dict:
        request, candidate, fingerprint, decision = self._load(
            db, principal, request_id, current=current)
        active = self._active(db, principal)
        admitted = active == (
            request["public_key"], request["host_key"], request.get("relay_public_key"),
        ) and candidate["binding"] == self.store.config.binding(principal)
        check(active is None or admitted or decision is not None,
              "pending-identity-superseded")
        if admitted and decision is None:
            decision = self._retain(db, principal, fingerprint)
        result = {
            "schema": "agent-ssh.enrollment-result", "version": 1,
            "status": "active" if admitted else "superseded" if decision else "pending",
            "request_id": request_id, "candidate": candidate,
            "fingerprint": fingerprint, "decision": decision,
        }
        check(len(canonical(result)) <= BODY_BYTES, "enrollment-response-limit")
        return result

    def submit(self, db, principal: str, request: dict) -> tuple[dict, bool]:
        self._require_policy()
        self.store._snapshot(db)
        candidate = self._candidate(principal, request)
        active = self._active(db, principal)
        check(active is None or active == (
            request["public_key"], request["host_key"], request.get("relay_public_key"),
        ), "key-change-requires-new-generation")
        self.store._remember_request(db, principal, request["request_id"], canonical(request))
        row = db.execute(
            "SELECT 1 FROM enrollment_pending WHERE principal=? AND request_id=?",
            (principal, request["request_id"]),
        ).fetchone()
        if row is None:
            db.execute("INSERT INTO enrollment_pending VALUES (?,?,?,?,?,NULL)", (
                principal, request["request_id"], canonical(request), canonical(candidate),
                self._fingerprint(candidate),
            ))
        return self._result(db, principal, request["request_id"]), False

    def status(self, principal: str, request_id: str, *, checkpoint=lambda: None) -> dict:
        self._require_policy()
        self.store.config.binding(principal)
        with self.store._transaction() as db:
            checkpoint()
            self.store._snapshot(db)
            result = self._result(db, principal, request_id, current=False)
            checkpoint()
            return result

    def pending(self, principal: str, *, after: str | None = None) -> dict:
        self._require_policy()
        if after is not None:
            identifier(after, "SSH pending cursor")
        with self.store._transaction() as db:
            self.store._snapshot(db)
            privileged = principal in self.store.config.recovery_principals
            if not privileged:
                privileged = self._active(db, principal) is not None
            rows = db.execute(
                "SELECT principal,request_id,request,candidate,fingerprint "
                "FROM enrollment_pending WHERE decision IS NULL ORDER BY rowid DESC"
            ).fetchall()
            pending, seen = {}, set()
            for requester, request_id, encoded, candidate, fingerprint in rows:
                if requester in seen or (not privileged and requester != principal):
                    continue
                request = decode(encoded)
                try:
                    current = self._candidate(requester, request)
                except ValueError:
                    continue
                if canonical(current) != candidate or self._active(db, requester) is not None:
                    continue
                seen.add(requester)
                pending[requester] = {
                    "request_id": request_id, "candidate": current, "fingerprint": fingerprint,
                }
            page = []

            def response(cursor):
                return {
                    "schema": "agent-ssh.enrollment-pending", "version": 1,
                    "requests": page, "next": cursor,
                }

            for requester, item in sorted(pending.items()):
                if after is not None and requester <= after:
                    continue
                page.append(item)
                if max(len(canonical(response(requester))),
                       len(canonical(response(None)))) > BODY_BYTES:
                    page.pop()
                    check(bool(page), "pending-item-response-limit")
                    return response(page[-1]["candidate"]["requester"])
            return response(None)

    def decide(self, principal: str, value: dict, *, recovery=False,
               checkpoint=lambda: None) -> tuple[dict, bool]:
        self._require_policy()
        fields = {"requester", "request_id", "fingerprint"}
        if recovery:
            fields |= {"confirm", "reason"}
        check(isinstance(value, dict) and set(value) == fields, "invalid-enrollment-decision")
        check(isinstance(value["fingerprint"], str) and len(value["fingerprint"]) == 50,
              "invalid-enrollment-fingerprint")
        if recovery:
            check(principal in self.store.config.recovery_principals,
                  "recovery-principal-not-admitted")
            check(value["confirm"] is True and isinstance(value["reason"], str)
                  and bool(value["reason"].strip()) and len(value["reason"]) <= 512
                  and not any(ord(char) < 32 for char in value["reason"]),
                  "explicit-recovery-confirmation-required")
        with self.store._transaction() as db:
            checkpoint()
            self.store._snapshot(db)
            if not recovery:
                check(self._active(db, principal) is not None, "enrolled-approver-required")
                check(principal != value["requester"], "self-approval-refused")
            requester, request_id = value["requester"], value["request_id"]
            request, _, fingerprint, decision = self._load(db, requester, request_id)
            check(value["fingerprint"] == fingerprint, "enrollment-fingerprint-mismatch")
            if decision is not None:
                result, changed = self._result(db, requester, request_id), False
            else:
                snapshot, changed = self.store._activate(db, requester, request)
                decision = {
                    "method": "recovery" if recovery else "peer", "principal": principal,
                    "directory_revision": snapshot["directory"]["revision"],
                }
                if recovery:
                    decision["reason"] = value["reason"]
                db.execute(
                    "UPDATE enrollment_pending SET decision=? "
                    "WHERE principal=? AND fingerprint=? AND decision IS NULL",
                    (canonical(decision), requester, fingerprint),
                )
                result = self._result(db, requester, request_id)
            checkpoint()
            return result, changed
