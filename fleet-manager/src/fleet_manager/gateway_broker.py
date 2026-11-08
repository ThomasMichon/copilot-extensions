"""Ephemeral subscribed-controller delivery. No credential plaintext or second ledger."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field

from .gateway_auth import AuthContext, Signer, thumbprint, verify
from .gateway_contract import (
    CREDENTIAL_WORKERS,
    REVIEW_CREDENTIAL_CONTRACT,
    GatewayConfig,
    correlation,
    credential,
    fields,
    request,
    require,
    text,
)
from .planning import digest


@dataclass
class Pending:
    binding: dict
    worker: str
    controller: str
    deadline: int
    state: str = "PENDING"
    delivery: str | None = field(default=None, repr=False)


class Broker:
    def __init__(self, config: GatewayConfig, signer: Signer, epoch: str):
        self.config, self.signer, self.epoch = config, signer, epoch
        self._items: dict[tuple[str, str], Pending] = {}
        self._subscriptions: dict[str, tuple[str, int]] = {}
        self._review_subscriptions: set[str] = set()
        self._lock = threading.RLock()

    def _live(self, principal: str) -> bool:
        peer = next((p for p in self.config.enrollments if p.principal == principal), None)
        return (peer is not None and principal not in self.config.revoked and peer.expires_at > time.time()
                and thumbprint(peer.proof_jwk) not in self.config.revoked
                and (peer.receipt_jwk is None or thumbprint(peer.receipt_jwk) not in self.config.revoked)
                and (peer.recipient_jwk is None or thumbprint(peer.recipient_jwk) not in self.config.revoked))

    def _expire(self) -> None:
        now = int(time.time())
        for item in self._items.values():
            if item.state in ("PENDING", "DISPATCHED", "READY", "DELIVERED"):
                subscribed = self._subscriptions.get(item.controller)
                if (item.deadline <= now or not self._live(item.worker) or not self._live(item.controller)
                        or subscribed is None or subscribed[1] <= now):
                    item.state = "UNKNOWN" if item.state != "PENDING" else "UNAVAILABLE"
                    item.delivery = None

    def subscribe(self, context: AuthContext, payload: dict) -> dict:
        fields(payload, {"lease"}, {"subscription", "contracts"}
               if self.config.protected_review else {"subscription"})
        require(context.peer.role == "credential-controller", "role-denied")
        from .gateway_contract import number

        lease = number(payload["lease"], 2, self.config.lease_seconds)
        review = "contracts" in payload
        if review:
            require(payload["contracts"] == [REVIEW_CREDENTIAL_CONTRACT], "unsupported-operation")
        with self._lock:
            self._expire()
            old = self._subscriptions.get(context.peer.principal)
            if "subscription" in payload:
                require(old is not None and old[0] == payload["subscription"] and old[1] > time.time(),
                        "subscription-unavailable")
                require(review == (context.peer.principal in self._review_subscriptions),
                        "subscription-unavailable")
                subscription = old[0]
            else:
                require(old is None or old[1] <= time.time(), "subscription-already-held")
                subscription = secrets.token_hex(16)
            expires = min(int(time.time()) + lease, context.expires_at)
            self._subscriptions[context.peer.principal] = (subscription, expires)
            self._review_subscriptions.discard(context.peer.principal)
            if review:
                self._review_subscriptions.add(context.peer.principal)
            return {"state": "SUBSCRIBED", "subscription": subscription, "expires_at": expires}

    def supports_review(self, principal: str) -> bool:
        """Current controller support, not a package/configuration-only promise."""
        with self._lock:
            return self.config.protected_review and self._live(principal) and any(
                rule["worker"] == principal and "review" in rule
                and rule["controller"] in self._review_subscriptions
                and self._live(rule["controller"])
                and self._subscriptions.get(rule["controller"], ("", 0))[1] > time.time()
                for rule in self.config.credential_rules
            )

    def disconnect(self, context: AuthContext, payload: dict) -> dict:
        fields(payload, {"subscription"})
        require(context.peer.role == "credential-controller", "role-denied")
        with self._lock:
            old = self._subscriptions.get(context.peer.principal)
            require(old is not None and old[0] == payload["subscription"], "subscription-unavailable")
            del self._subscriptions[context.peer.principal]
            self._review_subscriptions.discard(context.peer.principal)
            self._expire()
        return {"state": "UNAVAILABLE"}

    def submit(self, context: AuthContext, payload: dict) -> dict:
        value = request(payload)
        peer = context.peer
        require(peer.role in CREDENTIAL_WORKERS, "role-denied")
        review = value["operation"] == "credential.review"
        require(value["operation"] == "credential.get"
                or review and self.config.protected_review, "unsupported-operation")
        require(value["service_id"] == peer.service and value["installation_id"] == peer.installation
                and value["generation"] == peer.generation, "worker-binding-mismatch")
        parameters = credential(value["parameters"], protected_review=review)
        require((peer.role == "pr-worker") == ("forge" in parameters or review), "credential-scope-denied")
        require(parameters["recipient"] == peer.principal and peer.recipient_jwk is not None,
                "recipient-denied")
        require(parameters["source"] in peer.sources and parameters["account"] in peer.accounts
                and parameters["audience"] in peer.audiences
                and parameters["job"] in peer.jobs
                and set(parameters["scopes"]) <= set(peer.scopes), "credential-scope-denied")
        rule = next((r for r in self.config.credential_rules
                     if r["worker"] == peer.principal and r.get("forge") == parameters.get("forge")
                     and r.get("review") == parameters.get("review")
                     and all(r[k] == parameters[k] for k in
                             ("source", "account", "audience", "action", "scopes"))), None)
        require(rule is not None, "provider-policy-denied")
        key = (peer.principal, value["request_id"])
        with self._lock:
            self._expire()
            require(key not in self._items, "duplicate-request")
            require(len(self._items) < 1024, "session-capacity-exhausted")
            require(sum(i.state in ("PENDING", "DISPATCHED", "READY", "DELIVERED")
                        for i in self._items.values()) < self.config.pending_limit, "broker-unavailable")
            subscribed = self._subscriptions.get(rule["controller"])
            require(subscribed is not None and subscribed[1] > time.time()
                    and self._live(rule["controller"]), "controller-unavailable")
            require(not review or rule["controller"] in self._review_subscriptions,
                    "controller-unavailable")
            binding = correlation(value) | {
                "epoch": self.epoch, "fleet": self.config.fleet, "worker": peer.principal,
                "controller": rule["controller"], "parameters": parameters,
                "recipient_jkt": thumbprint(peer.recipient_jwk),
                "controller_generation": next(e.generation for e in self.config.enrollments
                                               if e.principal == rule["controller"]),
            }
            deadline = min(int(time.time()) + self.config.lease_seconds,
                           context.expires_at, subscribed[1])
            self._items[key] = Pending(binding, peer.principal, rule["controller"], deadline)
            return {"state": "PENDING", "request_id": value["request_id"],
                    "request_digest": digest(value), "expires_at": deadline}

    def poll(self, context: AuthContext, payload: dict) -> dict:
        fields(payload, {"subscription"})
        peer = context.peer
        require(peer.role == "credential-controller", "role-denied")
        with self._lock:
            self._expire()
            subscribed = self._subscriptions.get(peer.principal)
            require(subscribed is not None and subscribed[0] == payload["subscription"]
                    and subscribed[1] > time.time(), "subscription-unavailable")
            for item in self._items.values():
                if item.controller == peer.principal and item.state == "PENDING":
                    item.state = "DISPATCHED"
                    recipient = next(e.recipient_jwk for e in self.config.enrollments
                                     if e.principal == item.worker)
                    dispatch = item.binding | {"recipient_jwk": recipient,
                                               "deadline": item.deadline,
                                               "subscription": subscribed[0]}
                    return {"state": "DISPATCHED", "dispatch": self.signer.sign(
                        dispatch, peer.principal, max(1, item.deadline - int(time.time())),
                        expires_at=item.deadline)}
        return {"state": "IDLE"}

    def reply(self, context: AuthContext, payload: dict) -> dict:
        fields(payload, {"subscription", "worker", "request_id", "receipt"})
        require(context.peer.role == "credential-controller", "role-denied")
        text(payload["worker"])
        text(payload["request_id"])
        with self._lock:
            self._expire()
            item = self._items.get((payload["worker"], payload["request_id"]))
            require(item is not None and item.controller == context.peer.principal, "unsolicited-reply")
            subscribed = self._subscriptions.get(item.controller)
            require(subscribed is not None and subscribed[0] == payload["subscription"]
                    and subscribed[1] > time.time(), "subscription-unavailable")
            require(item.state == "DISPATCHED", "reply-out-of-order")
            key = context.peer.receipt_jwk
            require(key is not None, "controller-signing-key-required")
            reply = verify(payload["receipt"], {"keys": [key]}, item.controller, item.worker,
                           lifetime=60, revoked=self.config.revoked)
            require(reply.get("binding") == item.binding and reply["exp"] <= item.deadline,
                    "reply-binding-mismatch")
            state = reply.get("state")
            require(state in ("READY", "UNSUPPORTED", "UNAVAILABLE"), "invalid-reply")
            if state == "READY":
                sealed = text(reply.get("sealed"), 32768)
                require(reply.get("sealed_digest") == hashlib.sha256(sealed.encode()).hexdigest(),
                        "reply-content-mismatch")
                item.delivery = payload["receipt"]
            item.state = state
            return {"state": state}

    def result(self, context: AuthContext, payload: dict) -> dict:
        fields(payload, {"request_id", "request_digest"})
        require(context.peer.role in CREDENTIAL_WORKERS, "role-denied")
        with self._lock:
            self._expire()
            item = self._items.get((context.peer.principal, payload["request_id"]))
            if item is None:
                return {"state": "UNKNOWN"}
            require(item.binding["request_digest"] == payload["request_digest"], "correlation-mismatch")
            state = item.state
            if state == "READY":
                item.state = "DELIVERED"
                return {"state": "READY", "receipt": item.delivery, "binding": item.binding}
            if state == "DELIVERED":
                return {"state": "UNKNOWN"}
            return {"state": state}

    def ack(self, context: AuthContext, payload: dict) -> dict:
        fields(payload, {"request_id", "request_digest"})
        require(context.peer.role in CREDENTIAL_WORKERS, "role-denied")
        with self._lock:
            self._expire()
            item = self._items.get((context.peer.principal, payload["request_id"]))
            require(item is not None and item.binding["request_digest"] == payload["request_digest"],
                    "correlation-mismatch")
            require(item.state == "DELIVERED", "ack-uncertain")
            item.state, item.delivery = "ACKED", None
            return {"state": "ACKED"}
