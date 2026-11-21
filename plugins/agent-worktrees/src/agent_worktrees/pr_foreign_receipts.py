"""Unclaimed foreign-create attempts, fenced independently of checkout PRs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config as cfg, pr_authority, pr_publish, record_machine_identity, tracking
from .providers.base import ProviderError, PullResult
from .providers.github_pull_source import endpoint_identity
from .receiving_allocation import serialized_receipt


def failure(detail: str, pull: PullResult | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False, "claimed": False, "pr_identity_verified": False,
        "publication_state": "unknown", "state": "unknown",
    }
    known = ""
    if pull is not None:
        result.update(number=pull.number, url=pull.url, pr_opened=True)
        known = f" Retained unverified PR #{pull.number} ({pull.url!r})."
    result["error"] = (
        detail + " Foreign creation outcome is unknown." + known
        + " Reconcile the retained receipt before retrying; do not force a new PR."
    )
    return result


def _read_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ProviderError("Foreign creation receipt path is redirected.")
    try:
        if path.stat().st_size > 65536:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("binding"), dict)
            or set(value["binding"]) != {"provider", "endpoint", "target", "source", "branch"}
            or any(not isinstance(item, str) or not item for item in value["binding"].values())
            or value.get("phase") not in ("publishing", "unknown", "verified", "refused")
            or type(value.get("revision")) is not int or value["revision"] < 1
            or not isinstance(value.get("owner"), dict)
            or not isinstance(value.get("history"), list)
            or (value.get("provider_number") is not None and (
                type(value["provider_number"]) is not int or value["provider_number"] <= 0
            ))
            or not isinstance(value.get("provider_url", ""), str)
        ):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise ProviderError("Foreign creation receipt is unavailable; reconcile it before publication.") from exc


def _receipts(ledger: Path) -> Iterator[Path]:
    ledger = ledger.resolve()
    try:
        directories = list(ledger.iterdir())
    except FileNotFoundError:
        return
    for folder in sorted(directories):
        if not folder.name.endswith(".foreign-pr-receipts"):
            continue
        if folder.is_symlink() or folder.resolve() != folder or not folder.is_dir():
            raise ProviderError("Foreign creation receipt directory is unavailable.")
        for path in sorted(folder.iterdir()):
            if path.name.endswith(".json"):
                yield path


def owner_receipts(record: tracking.WorktreeRecord) -> tuple[bool, bool]:
    """Report retained and pending evidence while the native owner lock is held."""
    folder = record.yaml_path.parent.resolve() / record.yaml_path.with_suffix(".foreign-pr-receipts").name
    try:
        if folder.is_symlink():
            return True, True
        if not folder.exists():
            return False, False
        if folder.resolve() != folder or not folder.is_dir():
            return True, True
        paths = [path for path in folder.iterdir() if path.name.endswith(".json")]
        return bool(paths), any(_read_receipt(path)["phase"] not in ("verified", "refused") for path in paths)
    except (ProviderError, OSError, ValueError):
        return True, True


def require_settled(record: tracking.WorktreeRecord) -> None:
    if owner_receipts(record)[1]:
        raise ValueError("Foreign PR creation is unverified; reconcile it before finalization or cleanup.")


class Attempt:
    def __init__(
        self, config: cfg.Config, path: Path, record: tracking.WorktreeRecord | None,
        *, provider: str, endpoint: str, target: str, source: str, branch: str,
    ) -> None:
        self.config, self.owner_path, self.record = config, path, record
        self.binding = {
            "provider": provider, "endpoint": endpoint_identity(endpoint),
            "target": target.casefold(), "source": source.casefold(), "branch": branch,
        }
        key = hashlib.sha256(json.dumps(self.binding, sort_keys=True).encode()).hexdigest()
        self.path = path.parent.resolve() / path.with_suffix(".foreign-pr-receipts").name / (key + ".json")
        self.expected = self._read()
        self.lookup_receipts: tuple[tuple[str, str], ...] | None = None

    def _read(self) -> dict[str, Any] | None:
        if self.path.is_symlink() or self.path.parent.resolve() != self.path.parent:
            raise ProviderError("Foreign creation receipt path is redirected.")
        if not self.path.exists():
            return None
        value = _read_receipt(self.path)
        if {**value["binding"], "endpoint": endpoint_identity(value["binding"]["endpoint"])} != self.binding:
            raise ProviderError("Foreign creation receipt binding changed; reconcile before publication.")
        return value

    def _entries(self) -> list[tuple[Path, dict[str, Any]]]:
        entries = []
        ledgers = set(pr_authority._ledgers(self.config.repo_name)) | {self.owner_path.parent}
        for ledger in sorted(ledgers):
            for path in _receipts(ledger):
                value = _read_receipt(path)
                binding = {**value["binding"], "endpoint": endpoint_identity(value["binding"]["endpoint"])}
                if binding == self.binding:
                    entries.append((path, value))
        return entries

    @staticmethod
    def _snapshot(entries: list[tuple[Path, dict[str, Any]]]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted({(str(path), json.dumps(value, sort_keys=True)) for path, value in entries}))

    def pending(self) -> dict[str, Any] | None:
        entries = self._entries()
        if self.lookup_receipts is None:
            self.lookup_receipts = self._snapshot(entries)
        for path, value in entries:
            if value["phase"] in ("verified", "refused") and (
                path != self.path or self.record is not None and value["owner"] == self._owner(self.record)
            ):
                continue
            number = value.get("provider_number")
            return failure("A prior foreign creation is still unverified.", (
                PullResult(number=number, url=value.get("provider_url", "")) if number else None
            ))
        return None

    def _owner(self, record: tracking.WorktreeRecord) -> dict[str, Any]:
        return {
            "subject": list(record_machine_identity.subject_fence(record, self.owner_path.resolve())),
            "nonce": record.creation_nonce, "allocation": serialized_receipt(record),
        }

    def require_owner(self, current: tracking.WorktreeRecord) -> None:
        if (
            self.record is None or self._owner(current) != self._owner(self.record)
            or current.status in ("finalizing", "finalized", "archived")
        ):
            raise ProviderError("Foreign creation owner changed; requalify the retained attempt.")

    def _write(self, value: dict[str, Any]) -> None:
        with pr_publish.metadata_lock(self.owner_path.stem, project=self.config.repo_name), tracking._RecordLock(
            self.owner_path, require_sidecar=True,
        ):
            if not self.owner_path.is_file():
                raise ProviderError("Foreign creation needs an existing managed owner.")
            self.require_owner(tracking.load_record(self.owner_path))
            self._store(value)

    def _store(self, value: dict[str, Any]) -> None:
        if self._read() != self.expected:
            raise ProviderError("Foreign creation receipt revision changed; reconcile before retrying.")
        raw = json.dumps(value, sort_keys=True)
        if len(raw.encode("utf-8")) > 65536:
            raise ProviderError("Foreign creation receipt history is full; reconcile before publication.")
        tracking._atomic_write(self.path, raw)
        self.expected = value

    def reserve(self, base: str, expected_head: str | None) -> dict[str, Any] | None:
        if self.record is None:
            raise ProviderError("Foreign creation needs an existing managed owner.")
        with pr_authority.guard():
            pending = self.pending()
            if pending is not None:
                return pending
            if self._snapshot(self._entries()) != self.lookup_receipts:
                return {"success": False, "publication_state": "not-started", "error": (
                    "Foreign creation changed during lookup; repeat exact-source lookup before publication."
                )}
            previous = self.expected
            history = [] if previous is None else [
                *previous["history"], {key: value for key, value in previous.items() if key != "history"},
            ]
            self._write({
                "version": 1, "binding": self.binding, "owner": self._owner(self.record),
                "revision": 1 if previous is None else previous["revision"] + 1,
                "phase": "publishing", "base": base, "expected_head": expected_head or "",
                "history": history,
            })
        return None

    def finish(self, pull: PullResult | None, *, verified: bool) -> None:
        if self.expected is None or self.expected["phase"] != "publishing":
            raise ProviderError("Foreign creation has no current reserved receipt.")
        if verified:
            from .providers.github_pull_source import validate_url
            if pull is None:
                raise ProviderError("Foreign creation did not return a pull-request identity.")
            validate_url(pull.url, pull.number, self.binding["target"], self.binding["endpoint"])
        self._write({
            **self.expected, "revision": self.expected["revision"] + 1,
            "phase": "verified" if verified else "unknown",
            "provider_number": pull.number if pull is not None else None,
            "provider_url": pull.url if pull is not None else "",
        })

    def refused(self) -> None:
        if self.expected is None or self.expected["phase"] != "publishing":
            raise ProviderError("Foreign creation has no current reserved receipt.")
        self._write({**self.expected, "revision": self.expected["revision"] + 1, "phase": "refused"})


@contextmanager
def claim_owner(
    attempt: Attempt | None, path: Path, pull: PullResult, *, reused: bool,
) -> Iterator[tracking.WorktreeRecord]:
    if attempt is None:
        with tracking._RecordLock(path, require_sidecar=True):
            yield tracking.load_record(path)
        return
    with pr_authority.guard(), pr_publish.metadata_lock(path.stem, project=attempt.config.repo_name), tracking._RecordLock(
        path, require_sidecar=True,
    ):
        record = tracking.load_record(path)
        attempt.require_owner(record)
        if reused:
            if attempt.pending() is not None:
                raise ProviderError("An intervening foreign creation is still unverified; do not claim its PR.")
        else:
            if attempt.expected is None or attempt.expected["phase"] != "unknown":
                raise ProviderError("Foreign creation has no retained provider receipt to claim.")
            if attempt._read() != attempt.expected:
                raise ProviderError("Foreign creation receipt changed before claiming.")
            from .providers.github_pull_source import validate_url
            validate_url(pull.url, pull.number, attempt.binding["target"], attempt.binding["endpoint"])
        yield record
        if not reused:
            from . import obligations
            durable = tracking.load_record(path)
            attempt.require_owner(durable)
            if not any(claim.kind == "pr" and claim.ref == pull.url and claim.state == obligations.ACTIVE
                       for claim in durable.resources):
                raise ProviderError("Foreign creation has no durable active PR claim; retain its pending receipt.")
            assert attempt.expected is not None
            attempt._store({
                **attempt.expected, "revision": attempt.expected["revision"] + 1, "phase": "verified",
            })
