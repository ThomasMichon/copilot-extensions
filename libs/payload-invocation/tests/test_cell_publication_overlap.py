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
from test_cell_management_process import _live_management_owner
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


@pytest.mark.parametrize("entry", ["settle", "repair", "dev", "dev-release"])
@pytest.mark.timeout(20)
def test_management_owner_exit_during_cutover_wait_refuses_publication(
    binding_fixture: dict, backend, monkeypatch: pytest.MonkeyPatch, entry: str,
) -> None:
    fixture = binding_fixture
    waiting, acquired = threading.Event(), threading.Event()
    publications: list[str] = []
    released: list[bool] = []

    class Guard:
        def release(self) -> None:
            released.append(True)

    def acquire_cutover(_root: Path, *, timeout_s: float = 180) -> Guard:
        waiting.set()
        assert acquired.wait(timeout=5), "fixture never released the cutover wait"
        return Guard()

    @contextmanager
    def retention(root: Path, _version: str, **_kwargs):
        yield root / "versions/candidate", 0

    monkeypatch.setattr(backend, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(backend, "_installed_identity", lambda _slot: {
        "build_info": {"version": "fixture"},
    })
    monkeypatch.setattr(backend.runtime, "retention_custody", retention)
    monkeypatch.setattr(backend.runtime, "activate", lambda *_args, **_kwargs: publications.append("selector"))
    monkeypatch.setitem(sys.modules, "agent_worktrees", SimpleNamespace(
        config=SimpleNamespace(install_dir=lambda: fixture["legacy"]),
        status_monitor_cutover=SimpleNamespace(_acquire_cutover_lock=acquire_cutover),
        runtime_deployment=SimpleNamespace(
            manifest_bytes=lambda *_args: b"manifest", read_manifest=lambda _root: None,
            publish=lambda *_args: publications.append("manifest"),
        ),
    ))
    with _live_management_owner(fixture) as (root, arguments, child):
        for name in ("COPILOT_EXTENSIONS_CONTEXT", fixture["primitive"].RUNTIME_PUBLICATION_ENV):
            monkeypatch.setenv(name, arguments["environment"][name])
        signature = backend.Governance(root, fixture["payload"]).signature()

        def install() -> None:
            if entry == "settle":
                backend.settle_candidate(
                    root, "candidate", payload=fixture["payload"], payload_hash="fixture",
                    expected_governance=signature, wait_timeout=5,
                )
            else:
                import runtime_install_modes as modes

                modes.run_owned(
                    root, fixture["payload"], payload=fixture["payload"],
                    uv=root / "unused", base_python=Path(sys.executable), mode=entry,
                    expected_governance=signature, wait_timeout=5,
                )

        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(install)
            try:
                assert waiting.wait(timeout=5), "installer never reached the cutover wait"
                assert child.stdin is not None
                child.stdin.write("exit\n")
                child.stdin.flush()
                assert child.wait(timeout=5) == 0
            finally:
                acquired.set()
            with pytest.raises(ValueError, match="management owner"):
                future.result(timeout=5)
    assert publications == []
    assert released == [True]
