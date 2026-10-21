"""Managed and standalone publication share namespace-before-cutover ordering."""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_command_binding import binding_fixture as binding_fixture
from test_installer_publication import _cell, _policy, backend as backend

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


@pytest.mark.parametrize("state", ["missing", "standalone"])
@pytest.mark.parametrize("entry", ["settle", "repair", "dev", "dev-release"])
def test_standalone_waiter_does_not_hold_cutover_behind_management(
    binding_fixture: dict, backend, monkeypatch: pytest.MonkeyPatch,
    state: str, entry: str,
) -> None:
    fixture, primitive = binding_fixture, binding_fixture["primitive"]
    environment: dict[str, str] = {}
    root, arguments = _cell(fixture, environment)
    _policy(fixture, root, arguments, state)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", environment["COPILOT_EXTENSIONS_CONTEXT"])
    monkeypatch.delenv(primitive.RUNTIME_PUBLICATION_ENV, raising=False)
    namespace_attempted = threading.Event()
    cutover_lock = threading.Lock()
    released: list[bool] = []

    class PublicationReached(Exception):
        pass

    class Governance(backend.Governance):
        @contextmanager
        def held(self, *, timeout: float = 180):
            namespace_attempted.set()
            with super().held(timeout=timeout):
                yield

    class Guard:
        def release(self) -> None:
            cutover_lock.release()
            released.append(True)

    def acquire_cutover(_root: Path, *, timeout_s: float = 180) -> Guard:
        assert cutover_lock.acquire(timeout=timeout_s)
        return Guard()

    def publication_reached(*_args, **_kwargs) -> None:
        raise PublicationReached

    @contextmanager
    def retention(*_args, **_kwargs):
        yield root / "versions" / "candidate", 0

    monkeypatch.setattr(backend, "Governance", Governance)
    monkeypatch.setattr(backend, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(backend, "_installed_identity", lambda _slot: {})
    monkeypatch.setattr(backend, "_selected", publication_reached)
    monkeypatch.setattr(backend.runtime, "retention_custody", retention)
    monkeypatch.setattr(backend.runtime, "current_version", publication_reached)
    monkeypatch.setattr(backend.runtime, "read_dev_claim", publication_reached)
    monkeypatch.setitem(sys.modules, "agent_worktrees", SimpleNamespace(
        config=SimpleNamespace(install_dir=lambda: fixture["legacy"]),
        status_monitor_cutover=SimpleNamespace(_acquire_cutover_lock=acquire_cutover),
        runtime_deployment=SimpleNamespace(manifest_bytes=lambda *_args: b"manifest"),
    ))

    def standalone() -> None:
        if entry == "settle":
            backend.settle_candidate(
                root, "candidate", payload=fixture["payload"], payload_hash="fixture",
                expected_governance=Governance(root, fixture["payload"]).signature(),
                wait_timeout=5,
            )
        else:
            import runtime_install_modes as modes

            modes.run_owned(
                root, fixture["payload"], payload=fixture["payload"],
                uv=root / "unused", base_python=Path(sys.executable), mode=entry,
                expected_governance=Governance(root, fixture["payload"]).signature(),
                wait_timeout=5,
            )

    with ThreadPoolExecutor(max_workers=1) as workers:
        with primitive.runtime_publication_lease(**arguments) as patch:
            future = workers.submit(standalone)
            assert namespace_attempted.wait(timeout=5), "standalone caller never attempted admission"
            # A managed child already owns the namespace and must remain able
            # to acquire cutover while the standalone caller waits for it.
            acquired = cutover_lock.acquire(timeout=1)
            try:
                assert acquired, "standalone caller inverted namespace/cutover ownership"
                primitive.borrow_runtime_publication(**{
                    **arguments, "environment": {**arguments["environment"], **patch},
                })
            finally:
                if acquired:
                    cutover_lock.release()
        with pytest.raises(PublicationReached):
            future.result(timeout=10)
    assert released == [True]
