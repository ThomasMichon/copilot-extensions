"""Late changes after native and authority reads, inside original publication."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import json
from pathlib import Path
import time
from typing import Any, Iterator

from fleet_manager import komodo_repair
from fleet_manager.komodo_profile import KomodoMaterialization
from fleet_manager.komodo_evidence import native_fingerprint
from fleet_manager.node_journal import NodeJournal


class AdvancingRepairClock:
    """One elapsed time source for signatures, admission and native deadlines."""

    def __init__(self, epoch: int) -> None:
        self.epoch = epoch
        self.started = time.monotonic()
        self.offset = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.offset

    def __call__(self) -> int:
        return self.epoch + int(self.monotonic() - self.started)

    @property
    def value(self) -> int:
        return self()

    @value.setter
    def value(self, value: int) -> None:
        self.offset += value - self()


def completion_phase_drift(
    monkeypatch: Any, change: Callable[[], None], *, status: bool,
) -> list[bool]:
    phase = komodo_repair._Attempt.phase
    entered = 0
    fired: list[bool] = []

    @contextmanager
    def after_work(instance: Any) -> Iterator[None]:
        nonlocal entered
        with phase(instance):
            entered += 1
            current = entered
            yield
            if current == (5 if status else 9) and not fired:
                assert not instance.authorization.completed
                fired.append(True)
                change()

    monkeypatch.setattr(komodo_repair._Attempt, "phase", after_work)
    return fired


def late_publication_drift(monkeypatch: Any, change: Callable[[], None]) -> list[bool]:
    armed: list[bool] = []
    fired: list[bool] = []
    append = NodeJournal._append
    retained = komodo_repair.validate_retained_outcomes

    def after_append(instance: Any, *args: Any, **kwargs: Any) -> Any:
        result = append(*args, **kwargs)
        if args[2].to_dict()["sequence"] == 3:
            armed.append(True)
        return result

    def after_evidence(root: Path, record: dict[str, Any]) -> dict[str, str]:
        result = retained(root, record)
        if armed and not fired:
            fired.append(True)
            change()
        return result

    monkeypatch.setattr(NodeJournal, "_append", after_append)
    monkeypatch.setattr(komodo_repair, "validate_retained_outcomes", after_evidence)
    return fired


def late_material_drift(
    monkeypatch: Any, material: KomodoMaterialization, mode: str,
) -> list[bool]:
    def change() -> None:
        if mode == "publication-late-credential-value":
            material.admin_password.write_text("changed-fixture-credential")
        elif mode == "publication-late-credential-inode":
            path = material.admin_password
            previous = path.with_name(path.name + ".fixture-retained")
            path.rename(previous)
            path.touch(mode=0o600)
            path.write_bytes(previous.read_bytes())
        elif mode == "publication-late-data-inode":
            path = material.mongo_data
            previous = path.with_name(path.name + ".fixture-retained")
            path.rename(previous)
            path.mkdir(mode=0o700)
            (path / "retained-fixture").write_bytes((previous / "retained-fixture").read_bytes())
        else:
            raise AssertionError("unexpected material drift control")

    return late_publication_drift(monkeypatch, change)


def replace_pending_container(directory: Path) -> bytes:
    path = directory / "live.json"
    original = path.read_bytes()
    containers = json.loads(original)
    before = native_fingerprint(containers["core"])
    containers["core"]["Id"] = "f" * 64
    containers["core"]["State"].update(Pid=9999, StartedAt="replacement-fixture-start")
    attachment = next(iter(containers["core"]["NetworkSettings"]["Networks"].values()))
    attachment["EndpointID"] = "replacement-fixture-endpoint"
    assert native_fingerprint(containers["core"]) != before
    path.write_text(json.dumps(containers))
    return original
