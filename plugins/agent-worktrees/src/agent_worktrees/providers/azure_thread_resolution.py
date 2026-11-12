"""One consumed native status PATCH, with preflight/readback rather than atomic CAS."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from ..pr_ado_remediation import RemediationError, digest
from ..pr_thread_resolution import outcome, validate
from .azure_remediation import AdoMutator, AdoWriteRejected, Remediator, thread_fingerprint
from .azure_resolution_evidence import candidate, discussions, native_id
from .azure_review_permissions import require_review_permissions


def _content(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in ("status", "lastUpdatedDate")}


class Resolver:
    """Compose the existing remediation transport and locked durable ledger."""

    def __init__(self, provider: Any, repo: str, number: int, thread: int, *, api_base: str,
                 token: str | None, operation_id: str, expected_candidate: str, expected_thread: str,
                 state_dir: Path | None = None, reader_factory: Callable = AdoMutator,
                 reopening: bool = False) -> None:
        validate(repo, number, thread, operation_id, expected_candidate, expected_thread)
        self.remediator = Remediator(provider, repo, number, api_base=api_base, token=token,
                                     state_dir=state_dir, reader_factory=reader_factory)
        self.reader = self.remediator.reader
        self.fields = (repo, number, thread, operation_id, expected_candidate, expected_thread)
        self.thread = thread
        self.expected_candidate, self.expected_thread = expected_candidate, expected_thread
        self.actor: str | None = None
        self.observed_candidate: dict | None = None
        self.observed_thread: dict | None = None
        self.consumed_write = False
        self.reopening = reopening
        self.operation = "thread-reopen" if reopening else "thread-resolve"
        self.desired_status = "active" if reopening else "closed"
        self.desired_values = ("active", 1) if reopening else ("closed", 4)

    def result(self, **kwargs: Any) -> dict:
        return outcome(*self.fields, provider="azure-devops", actor=self.actor,
                       observed_candidate=self.observed_candidate,
                       observed_thread=self.observed_thread, reopening=self.reopening, **kwargs)

    def principal(self) -> str:
        identity = self.reader.identity().get("authorizedUser")
        if not isinstance(identity, dict):
            raise RemediationError("Actual authenticated native principal is unavailable")
        return native_id(identity.get("id"))

    def snapshot(self) -> tuple[str, dict, dict]:
        """Bracket complete discussion acquisition; permissions use this same credential."""
        pr, target, _ = self.remediator.active()
        before, iterations = candidate(
            self.reader, pr, self.remediator.provider, self.remediator.repo, self.remediator.number,
        )
        self.observed_candidate = before
        rows = discussions(self.reader, iterations)
        matches = [row for row in rows if row["id"] == self.thread]
        if len(matches) != 1:
            raise RemediationError("Selected discussion is unavailable")
        row = matches[0]
        fingerprint = thread_fingerprint(row)
        self.observed_thread = {"id": self.thread, "fingerprint": fingerprint, "status": row["status"]}
        actor = self.principal()
        if actor != self.actor:
            raise RemediationError("Actual authenticated native principal changed")

        def read(url: str) -> dict:
            body, continuation = self.reader.json(url)
            if continuation:
                raise RemediationError("Native permission information is incomplete")
            return body

        require_review_permissions(read, scope=before, pr=pr)
        after_pr, after_target, _ = self.remediator.active()
        after, _ = candidate(
            self.reader, after_pr, self.remediator.provider, self.remediator.repo, self.remediator.number,
        )
        self.observed_candidate = after
        if target != after_target or before != after:
            raise RemediationError("Native candidate changed during discussion acquisition")
        return target, after, row

    def admission(self, current: tuple[str, dict, dict]) -> None:
        _, observed, row = current
        if observed["fingerprint"] != self.expected_candidate:
            raise RemediationError("Full candidate fingerprint changed")
        if thread_fingerprint(row) != self.expected_thread:
            raise RemediationError("Selected thread fingerprint changed")
        allowed = ("fixed", "wontFix", "closed", "byDesign", 2, 3, 4, 5) if self.reopening else ("active", 1)
        if type(row.get("status")) is bool or row.get("status") not in allowed:
            raise RemediationError(
                "Only a resolved native discussion can be reopened" if self.reopening
                else "Only an active native discussion can be resolved"
            )
        if not isinstance((row.get("pullRequestThreadContext") or {}).get("iterationContext"), dict):
            raise RemediationError("Selected discussion lacks native iteration association")

    def consumed(self, prior: dict, request_hash: str) -> dict:
        if prior["request_hash"] != request_hash:
            result = self.result(reason="operation-conflict",
                                 error="Operation ID already binds another target, actor or revision")
            result["original_operation"] = deepcopy(prior.get("result"))
            return result
        original = prior.get("result")
        if not isinstance(original, dict):
            original = self.result(status="unknown", reason="operation-consumed",
                                   error="Operation was consumed before interruption; no automatic replay")
        result = deepcopy(original)
        result["reconciled"] = True
        # A later state is independent observation, not operation attribution.
        try:
            self.snapshot()
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError):
            pass
        result["reconciliation_observation"] = {
            "candidate": self.observed_candidate, "thread": self.observed_thread,
        }
        return result

    def run(self) -> dict:
        pr, target, _ = self.remediator.target()
        if type(pr.get("pullRequestId")) is not int or pr["pullRequestId"] != self.fields[1]:
            raise RemediationError("Native PR identity differs from the selected target")
        self.actor = self.principal()
        key = self.operation + ":" + digest(self.fields[3])
        request_hash = digest([target, self.actor, self.operation, self.thread,
                               self.expected_candidate, self.expected_thread, self.desired_status])
        with self.remediator.ledger(target).locked() as ledger:
            prior = ledger.records.get(key)
            if prior is not None:
                return self.consumed(prior, request_hash)
            current = self.snapshot()
            self.admission(current)
            if current[0] != target:
                raise RemediationError("Canonical native target changed before admission")
            latest = self.snapshot()
            self.admission(latest)
            if current != latest:
                raise RemediationError("Native discussion changed immediately before dispatch")
            original_row = latest[2]
            pending = self.result(status="unknown", reason="operation-consumed",
                                  error="Operation consumed; write completion is not yet confirmed")
            self.consumed_write = True
            ledger.consume(key, request_hash, thread_id=self.thread, actor=self.actor,
                           candidate=latest[1], expected_thread=self.expected_thread, result=pending)
            try:
                response = (
                    self.reader.reopen_thread(self.thread, authoritative_rejection=True)
                    if self.reopening else self.reader.thread_status(
                        self.thread, "closed", authoritative_rejection=True,
                    )
                )
                thread_fingerprint(response)
                if (response.get("id") != self.thread or type(response.get("status")) is bool
                        or response.get("status") not in self.desired_values
                        or _content(response) != _content(original_row)):
                    raise RemediationError("Native status response does not confirm the selected discussion")
                _, observed, row = self.snapshot()
                if (observed != latest[1] or type(row.get("status")) is bool
                        or row.get("status") not in self.desired_values
                        or _content(row) != _content(original_row)):
                    raise RemediationError("Native candidate or discussion changed after dispatch")
                result = self.result(status="confirmed", reason="reopened" if self.reopening else "resolved")
            except AdoWriteRejected as exc:
                result = self.result(status="failed", reason="native-rejected", error=str(exc))
            except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as exc:
                result = self.result(status="unknown", reason="write-unconfirmed", error=str(exc))
            ledger.update(key, state="verified" if result["ok"] else "unknown", result=result)
            return result


def resolve(provider: Any, repo: str, number: int, thread: int, *, operation_id: str,
            expected_candidate: str, expected_thread: str, api_base: str, token: str | None,
            state_dir: Path | None = None, reader_factory: Callable = AdoMutator,
            reopening: bool = False) -> dict:
    """Refuse before dispatch, or retain an honest consumed operation outcome."""
    fields = (repo, number, thread, operation_id, expected_candidate, expected_thread)
    owner = None
    try:
        owner = Resolver(
            provider, repo, number, thread, api_base=api_base, token=token,
            operation_id=operation_id, expected_candidate=expected_candidate,
            expected_thread=expected_thread, state_dir=state_dir, reader_factory=reader_factory,
            reopening=reopening,
        )
        return owner.run()
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as exc:
        if owner is not None:
            if owner.consumed_write:
                return owner.result(status="unknown", reason="write-unconfirmed", error=str(exc))
            return owner.result(reason="preflight-refused", error=str(exc))
        return outcome(*fields, provider="azure-devops", reason="invalid-input", error=str(exc),
                       reopening=reopening)
