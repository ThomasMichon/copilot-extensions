"""Queue and receipt construction for authority protocol regressions."""

import hashlib
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import pytest

from update_driver import (
    Request, RequestSpec, Transaction, TrustedRunner, UpdateAuthority, change_authority, enqueue,
)
from update_driver._invocation import spec_document

LegacySubmission = tuple[str, str, str, str, float]


def runner(area: Path) -> TrustedRunner:
    return TrustedRunner((sys.executable, "-c", "pass"), str(area))


def submit(
    area: Path, *, authority: str = "", project: str = "",
    context: tuple[tuple[str, str], ...] = (), backend: str = "engine",
) -> Request:
    return enqueue(
        RequestSpec(backend, project=project, context=context, authority=authority),
        runner=runner(area), root=area / "state",
    )


def receipt(area: Path, marketplace: str, plugin: str) -> Path:
    path = area / "marketplaces" / marketplace / "plugins" / plugin / "install.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def record_requests(requests: list[str]) -> Callable[[Transaction], int]:
    def execute(transaction: Transaction) -> int:
        requests.append(transaction.request.id)
        return 0

    return execute


def prevalidated_legacy_submission(
    area: Path, identifier: str, backend: str, *, authority: str = "",
) -> LegacySubmission:
    from update_driver import core

    spec = spec_document(core._spec(RequestSpec(backend, project="example", authority=authority)))
    invocation = asdict(core._runner(runner(area)))
    spec_json, runner_json = core._json(spec), core._json(invocation)
    identity = hashlib.sha256((spec_json + "\n" + runner_json).encode()).hexdigest()
    return identifier, identity, spec_json, runner_json, time.time()


def commit_legacy_submission(root: Path, request: LegacySubmission) -> None:
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute(
            "INSERT INTO requests(id,identity,spec,runner,state,created_at) VALUES(?,?,?,?,'pending',?)",
            request,
        )


def interrupt_first_authority_switch(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from update_driver import authority

    write = authority.write

    def interrupt(db: sqlite3.Connection, context: str, selected: UpdateAuthority) -> None:
        write(db, context, selected)
        raise RuntimeError("interrupted before commit")

    with monkeypatch.context() as change:
        change.setattr(authority, "write", interrupt)
        with pytest.raises(RuntimeError, match="interrupted"):
            change_authority("project", project="example", root=root, timeout=1,
                             verify_capabilities=lambda _deadline: True)
    assert (root / "authority.guard").is_file()
    with sqlite3.connect(root / "queue.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
