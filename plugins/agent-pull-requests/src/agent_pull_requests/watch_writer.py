"""Native writer ownership for the governed watch-owner adapter.

This boundary does not grant installation or transaction authority. Its caller
must supply a validator for each transition, including confirmed-successor-exit
authorization before a released predecessor may resume.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from single_instance_lease import SingleInstance

from .watch_daemon import Fetch, NativeFetch, Notify, WatchDaemon
from .watch_notification import default_notify, positive_seconds

if TYPE_CHECKING:
    from zdd.cutover_lock import CutoverLock

    from .watch_installation import WatchInstallation


class WatchWriter:
    """Bind one inactive core to its exact state root and native writer lease."""

    def __init__(
        self, *, root: Path, authorize: Callable[[str], None], fetch: Fetch,
        notify: Notify = default_notify, poll_interval: float = 30.0,
        native_fetch: NativeFetch | None = None,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("watch writer state root must be absolute")
        if not callable(authorize):
            raise TypeError("watch writer requires an authority validator")
        self.root = root
        self.authorize = authorize
        self.daemon = WatchDaemon(
            fetch=fetch, notify=notify, poll_interval=poll_interval,
            active=False, state_root=root,
            native_fetch=native_fetch,
            effect_guard=self._check_callback_effects,
            on_storage_refusal=self._reject_preparation,
        )
        self._lease = SingleInstance(root, service="agent-pull-requests-watch")
        self._lifecycle = threading.Lock()
        self._creating_pid = os.getpid()
        self._read_ready = False
        self._drain_started = False
        self._preparation_guard: Callable[[], None] | None = None
        self._strict_custody = False
        self._closed = False

    @property
    def writer_held(self) -> bool:
        self._assert_process()
        return self._lease.held

    @property
    def read_ready(self) -> bool:
        self._assert_process()
        return self._read_ready and self.writer_held and not self._closed

    def _assert_process(self) -> None:
        if os.getpid() != self._creating_pid:
            raise RuntimeError("watch writer belongs to another process")

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("watch writer lifecycle deadline expired")
        return remaining

    @contextmanager
    def _operation(self, timeout: float) -> Iterator[float]:
        self._assert_process()
        positive_seconds(timeout)
        deadline = time.monotonic() + timeout
        if not self._lifecycle.acquire(timeout=timeout):
            raise TimeoutError("watch writer lifecycle is busy")
        try:
            self._remaining(deadline)
            yield deadline
        finally:
            self._lifecycle.release()

    def _validate(self, operation: str, deadline: float) -> None:
        if self._closed:
            raise RuntimeError("watch writer is closed")
        if self.authorize(operation) is not None:
            raise PermissionError("watch writer authority validation did not complete")
        self._remaining(deadline)

    def _acquire_and_reload(self, deadline: float) -> None:
        self._read_ready = False
        guard = self._preparation_guard
        try:
            from .watch_storage import _require_legacy_state

            _require_legacy_state(self.root)
            if not self.writer_held:
                self._lease.acquire()
            self._lease.held_file_identity()
            self._remaining(deadline)
            if not self._strict_custody:
                self.daemon.reload_state(timeout=self._remaining(deadline))
            else:
                from .watch_storage import read_subscriptions_state

                if guard is not None:
                    guard()
                self.daemon.reload_state(
                    timeout=self._remaining(deadline),
                    read_state=lambda: read_subscriptions_state(self.root, require_plain=True),
                    retain_legacy=True,
                )
                self._lease.held_file_identity()
                if guard is not None:
                    guard()
            self._remaining(deadline)
        except Exception:
            if self._strict_custody:
                self._reject_preparation()
            raise
        self.daemon.set_persistence_guard(None, timeout=self._remaining(deadline))
        self._preparation_guard = None
        self._read_ready = True

    def _reject_preparation(self) -> None:
        self._assert_process()
        self._strict_custody = True
        self._read_ready = False
        self.daemon.stop_guarded_work()

    def _check_callback_effects(self) -> None:
        if not self._strict_custody:
            return
        try:
            self._assert_process()
            self._lease.held_file_identity()
            if not self.read_ready:
                raise PermissionError("watch writer requires readoption before callback effects")
        except Exception:
            self._reject_preparation()
            raise

    def _settle_preparation(self, deadline: float) -> None:
        if not self._strict_custody:
            return
        if not self.daemon.quiescent or self.daemon.has_legacy_pending():
            self.daemon.settle_workers(
                timeout=self._remaining(deadline), preserve_queued=True,
            )
        if self.daemon.has_legacy_pending():
            raise RuntimeError("guarded legacy work requires activation and drain before closure or release")

    def _resume_effects(self, deadline: float) -> None:
        if not self._strict_custody:
            self.daemon.resume_effects(timeout=self._remaining(deadline))
            self._remaining(deadline)
            return

        def complete() -> None:
            self.daemon.set_persistence_guard(None, timeout=self._remaining(deadline))
            self._preparation_guard = None

        self.daemon.resume_effects(
            timeout=self._remaining(deadline), before_admit=complete,
        )

    def adopt(self, *, timeout: float) -> None:
        """Acquire the real writer lock before reading the final snapshot."""
        with self._operation(timeout) as deadline:
            self._validate("adopt", deadline)
            if not self.daemon.quiescent:
                if not self._strict_custody or self.daemon.effects_enabled:
                    raise RuntimeError("watch writer adoption requires completed quiescence")
                self._reject_preparation()
                self.daemon.settle_workers(
                    timeout=self._remaining(deadline), preserve_queued=True,
                )
            self._acquire_and_reload(deadline)

    def activate(self, *, timeout: float) -> None:
        """Authorize effects separately from a read-ready writer acquisition."""
        with self._operation(timeout) as deadline:
            self._validate("activate", deadline)
            if not self.read_ready:
                raise RuntimeError("watch writer must be read-ready before activation")
            if self._preparation_guard is not None:
                raise RuntimeError("watch preparation requires guarded resume or readoption")
            try:
                self._check_callback_effects()
                self._resume_effects(deadline)
            except Exception:
                if self._strict_custody:
                    self._reject_preparation()
                raise
            self._drain_started = False

    def prepare_state_transition(
        self, *, timeout: float, installation: WatchInstallation | None = None,
    ) -> dict[str, Any]:
        """Prepare explicitly under existing custody; never seal, import or activate."""
        from .watch_registry import WatchRegistry
        from .watch_storage import _read_subscriptions_state, watch_state_paths
        from .watch_transition import _historical_identity, prepare, read_preparation

        with self._operation(timeout) as deadline:
            binding: dict[str, Any] = {"kind": "legacy", "stateRoot": str(self.root)}
            identity: tuple[int, int] | None = None
            prior: dict[str, Any] | None = None
            inspected = False

            def check_deadline() -> None:
                self._remaining(deadline)

            def guard_source() -> None:
                try:
                    self._assert_process()
                    if installation is not None:
                        installation.require_current()
                    held = self._lease.held_file_identity()
                    if held != _historical_identity(self.root) or (
                        identity is not None and held != identity
                    ):
                        raise PermissionError("watch preparation historical lease identity changed")
                    current = read_preparation(self.root, binding)
                    if inspected and current != prior:
                        raise PermissionError("watch preparation journal changed during drain")
                    WatchRegistry().restore_state(
                        _read_subscriptions_state(
                            watch_state_paths(self.root).legacy_subscriptions, require_plain=True,
                        ),
                        strict=True,
                    )
                except Exception:
                    self._reject_preparation()
                    raise

            def validate_legacy() -> None:
                self._validate("prepare-state-transition", deadline)
                if installation is not None:
                    installation.require_current()
                guard_source()
                check_deadline()

            def validate() -> None:
                self._validate("prepare-state-transition", deadline)
                if installation is not None:
                    installation.require_current()
                    if installation.state_root != self.root:
                        raise PermissionError("watch preparation installation state root differs")
                if not self.daemon.quiescent or self.daemon.effects_enabled:
                    raise PermissionError("watch preparation requires completed quiescence")
                if self.daemon.has_legacy_pending():
                    raise RuntimeError("queued legacy callbacks require activation and completed drain")
                check_deadline()

            self._validate("prepare-state-transition", deadline)
            if installation is not None:
                installation.require_current()
                if installation.state_root != self.root:
                    raise PermissionError("watch preparation installation state root differs")
                binding.update(kind="installation", installation=installation.evidence())
            self.daemon.set_persistence_guard(
                guard_source, timeout=self._remaining(deadline),
            )
            self._preparation_guard = guard_source
            self._strict_custody = True
            try:
                identity = _historical_identity(self.root)
                prior = read_preparation(self.root, binding)
                inspected = True
                if not self.writer_held:
                    self._lease.acquire(expected_file_identity=identity)
                guard_source()
                self._drain_started = True
                if not self.daemon.quiescent:
                    self.daemon.quiesce(
                        timeout=self._remaining(deadline), before_persist=validate_legacy,
                    )
                self._read_ready = False
                return prepare(
                    self.root, binding, self._lease, validate=validate, check_deadline=check_deadline,
                )
            except BaseException as error:
                self.daemon.stop_admission()
                self._drain_started = True
                if not isinstance(error, TimeoutError):
                    self._reject_preparation()
                try:
                    guard_source()
                except Exception:
                    self._reject_preparation()
                try:
                    self.daemon.quiesce(
                        timeout=self._remaining(deadline), persist=False, preserve_queued=True,
                    )
                except BaseException as drain_error:
                    raise error from drain_error
                raise

    def recover_prepared_state_transition(
        self, *, timeout: float, installation: WatchInstallation | None = None,
        guard: CutoverLock | None = None,
    ) -> dict[str, Any]:
        """Receive a prepared journal under fresh admission, without restoring effects."""
        return self._recover_state_transition(
            timeout=timeout, installation=installation, guard=guard, sealed=False,
        )

    def recover_sealed_state_transition(
        self, *, timeout: float, installation: WatchInstallation | None = None,
        guard: CutoverLock | None = None,
    ) -> dict[str, Any]:
        """Receive a qualified Windows sealed journal, without importing either registry."""
        return self._recover_state_transition(
            timeout=timeout, installation=installation, guard=guard, sealed=True,
        )

    def seal_recovered_state_transition(
        self, *, timeout: float, installation: WatchInstallation | None = None,
        guard: CutoverLock | None = None,
    ) -> dict[str, Any]:
        """Progress only an admitted v2 transition through the existing Windows sealer."""
        return self._recover_state_transition(
            timeout=timeout, installation=installation, guard=guard, sealed=True, sealing=True,
        )

    def _recover_state_transition(
        self, *, timeout: float, installation: WatchInstallation | None,
        guard: CutoverLock | None, sealed: bool, sealing: bool = False,
    ) -> dict[str, Any]:
        from zdd.cutover_lock import lock_path

        from .watch_transition_recovery import (
            _controller_environment, recover_prepared, recover_sealed, seal_recovered,
        )

        with self._operation(timeout) as deadline:
            def validate() -> None:
                self._validate(
                    "seal-recovered-state-transition" if sealing else
                    "recover-sealed-state-transition" if sealed else "recover-prepared-state-transition",
                    deadline,
                )
                if installation is not None:
                    if guard is None:
                        raise PermissionError("watch transition recovery requires an installation transaction")
                    guard.require_owned()
                    if guard.path != lock_path(installation.run_root):
                        raise PermissionError("watch transition recovery requires its own installation transaction")
                    installation.require_current(environment=_controller_environment())
                    if installation.state_root != self.root:
                        raise PermissionError("watch transition recovery installation state root differs")
                elif guard is not None:
                    raise PermissionError("watch legacy transition recovery has no installation transaction")
                if not self.daemon.quiescent or self.daemon.effects_enabled:
                    raise PermissionError("watch transition recovery requires completed quiescence")
                if self.daemon.has_legacy_pending():
                    raise RuntimeError("watch transition recovery cannot discard queued legacy callbacks")

            def refuse_persistence() -> None:
                raise PermissionError("watch transition recovery does not authorize legacy writes")

            def check_deadline() -> None:
                self._remaining(deadline)

            validate()
            self.daemon.set_persistence_guard(
                refuse_persistence, timeout=self._remaining(deadline),
            )
            self._preparation_guard = refuse_persistence
            self._strict_custody = True
            self._read_ready = False
            try:
                recover = seal_recovered if sealing else recover_sealed if sealed else recover_prepared
                return recover(
                    self.root, self._lease, installation=installation, guard=guard,
                    validate=validate, check_deadline=check_deadline,
                )
            except BaseException:
                self._reject_preparation()
                raise

    def seal_state_transition(
        self, *, timeout: float, installation: WatchInstallation | None = None,
    ) -> dict[str, Any]:
        """Seal/recover an already drained preparation; never adopt either registry."""
        from .watch_sealing import require_backend, seal

        with self._operation(timeout) as deadline:
            def validate() -> None:
                self._validate("seal-state-transition", deadline)
                if installation is not None:
                    installation.require_current()
                    if installation.state_root != self.root:
                        raise PermissionError("watch sealing installation state root differs")
                if not self.daemon.quiescent or self.daemon.effects_enabled:
                    raise PermissionError("watch sealing requires completed preparation quiescence")
                if self.daemon.has_legacy_pending():
                    raise RuntimeError("watch sealing cannot discard queued legacy callbacks")

            validate()
            require_backend()
            binding: dict[str, Any] = {"kind": "legacy", "stateRoot": str(self.root)}
            if installation is not None:
                binding.update(kind="installation", installation=installation.evidence())

            def refuse_persistence() -> None:
                raise PermissionError("watch sealing requires guarded legacy recovery before writes")

            self.daemon.set_persistence_guard(
                refuse_persistence, timeout=self._remaining(deadline),
            )
            self._preparation_guard = refuse_persistence
            self._strict_custody = True
            self._read_ready = False
            self._drain_started = True
            def check_deadline() -> None:
                self._remaining(deadline)

            try:
                return seal(
                    self.root, binding, self._lease, validate=validate,
                    check_deadline=check_deadline,
                )
            except BaseException:
                self._reject_preparation()
                raise

    def drain_and_release(self, *, timeout: float) -> None:
        """Never release on incomplete drain or failed post-drain authorization."""
        with self._operation(timeout) as deadline:
            self._validate("drain", deadline)
            if not self.writer_held:
                raise RuntimeError("watch writer does not hold predecessor authority")
            self._settle_preparation(deadline)
            self._drain_started = True
            self.daemon.quiesce(timeout=self._remaining(deadline))
            self._remaining(deadline)
            self._validate("release", deadline)
            self._lease.release()
            self._read_ready = False

    def resume(self, *, timeout: float) -> None:
        """Resume retained work, or reacquire/reload after authorized rollback.

        The caller's ``resume`` authorization must prove exact successor exit
        after any transfer. Lease availability alone is not retirement evidence.
        """
        with self._operation(timeout) as deadline:
            self._validate("resume", deadline)
            if not self._drain_started:
                raise RuntimeError("watch writer is not a drained predecessor")
            if not self.writer_held:
                self._acquire_and_reload(deadline)
            if not self.read_ready:
                raise RuntimeError("watch writer must be read-ready before resumption")
            try:
                self._lease.held_file_identity()
                if self._preparation_guard is not None:
                    self._preparation_guard()
            except Exception:
                self._reject_preparation()
                raise
            try:
                self._resume_effects(deadline)
            except Exception:
                if self._strict_custody:
                    self._reject_preparation()
                raise
            self._drain_started = False

    def compute(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._assert_process()
        if kind in {"register", "unregister"} and not self.read_ready:
            return {"error": "watch writer is not read-ready", "retryable": True}
        return self.daemon.compute(kind, payload)

    def require_passive(self) -> None:
        """Never treat a journal phase as proof that writer/effect adoption did not run."""
        if (
            self.writer_held or self.read_ready or self.daemon.effects_enabled
            or not self.daemon.quiescent or self.daemon.has_legacy_pending()
        ):
            raise PermissionError("watch cleanup requires an effect-free passive writer")

    def close_passive(self, *, timeout: float) -> None:
        with self._operation(timeout) as deadline:
            self._validate("cleanup-stop", deadline)
            self.require_passive()
            self.daemon.close(timeout=self._remaining(deadline))
            self._validate("cleanup-stop", deadline)
            self.require_passive()
            self._closed = True

    def close(self, *, timeout: float) -> None:
        """Retain the native lease when callbacks or accepted writes remain live."""
        with self._operation(timeout) as deadline:
            if self._closed:
                return
            self._settle_preparation(deadline)
            self.daemon.close(timeout=self._remaining(deadline))
            self._remaining(deadline)
            self._lease.release()
            self._read_ready = False
            self._closed = True

    def withdraw(self, *, timeout: float) -> None:
        """Revalidate stop custody after all accepted work has quiesced."""
        with self._operation(timeout) as deadline:
            self._validate("withdraw", deadline)
            self._settle_preparation(deadline)
            self.daemon.close(timeout=self._remaining(deadline))
            self._validate("withdraw", deadline)
            self._lease.release()
            self._read_ready = False
            self._closed = True
