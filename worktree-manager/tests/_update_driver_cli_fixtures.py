"""Synthetic queue capture and private daemon state for public CLI tests."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, NoReturn, TypeVar, TypedDict

import pytest

from update_driver import RequestSpec, TrustedRunner


@dataclass
class QueuedRequest:
    id: str
    log_path: Path
    spec: RequestSpec
    runner: TrustedRunner
    state: str = "pending"
    exit_code: int | None = None
    error: str | None = None
    recovery_authority: str | None = None
    released_at: float | None = None
    settled_at: float | None = None
    settlement_kind: str | None = None


class QueueCapture(TypedDict, total=False):
    spec: RequestSpec
    runner: TrustedRunner
    wait: str
    worker: tuple[object, ...]
    drained: bool
    custody: bool
    body: list[str]


Queued = tuple[QueueCapture, QueuedRequest]
T = TypeVar("T")


def record_result(records: list[T], value: T, result: int) -> int:
    records.append(value)
    return result


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Queued:
    import update_driver
    from worktree_manager import __main__ as manager, engine_client, update_driver_cli as driver
    from update_driver.authority import UpdateAuthority

    seen: QueueCapture = {}
    request = QueuedRequest(
        id="synthetic-request", log_path=tmp_path / "update.log",
        spec=RequestSpec("manager"), runner=TrustedRunner((), str(tmp_path)),
    )

    def enqueue(spec: RequestSpec, *, runner: TrustedRunner) -> QueuedRequest:
        seen["spec"], seen["runner"] = spec, runner
        request.spec = spec
        request.runner = runner
        return request

    def wait(request_id: str, **kwargs: object) -> QueuedRequest:
        seen["wait"] = request_id
        return replace(request, state="completed", exit_code=6)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("ordinary caller executed the installer")

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "current_authority", lambda **_kwargs: UpdateAuthority())
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (str(tmp_path / "python.exe"), "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: seen.update({"worker": args}))
    monkeypatch.setattr(update_driver, "wait", wait)
    monkeypatch.setattr(driver, "source_identity", lambda: {"repo": "synthetic-source", "ref": "fixed"})
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: "")
    monkeypatch.setattr(manager, "_cmd_update", forbidden)
    return seen, request


@pytest.fixture
def short_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    from agent_procutil.daemon_instances import (
        REGISTRY_ENV, InstanceCleanupResult, daemon_test_lifecycle,
    )

    # Exercise queue ownership, not the platform's AF_UNIX address-length limit.
    home = tmp_path_factory.mktemp("ud") / "home"
    home.mkdir()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(home / ".worktree-manager"))
    registry = home.parent / "daemon-instances"
    cleanup: list[InstanceCleanupResult] = []
    with daemon_test_lifecycle(
        owner="manager-update-cli-tests", registry_root=registry, cleanup_results=cleanup,
    ) as environment:
        monkeypatch.setenv(REGISTRY_ENV, str(registry))
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        yield home
    assert all(result.status != "held" for result in cleanup), cleanup
