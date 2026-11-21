"""Real canonical receipts and governance for the internal owner binding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from agent_procutil import no_window_kwargs
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from agent_pull_requests import _installation_context as _api
from agent_pull_requests.watch_installation import (
    PLUGIN_ID, WatchInstallation, _InstallationAPI,
)


class _FixtureAPI(_InstallationAPI, Protocol):
    POLICY_SCHEMA: str
    InstallationContextError: type[ValueError]

    def stamp_context(self, **kwargs: Any) -> dict[str, Any]: ...

    def compare_and_swap_activation(self, **kwargs: Any) -> dict[str, Any]: ...


api = cast(_FixtureAPI, _api)


def _policy(profile: Path, enabled: bool = True) -> None:
    path = profile / ".copilot-extensions" / "installation-mode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": api.POLICY_SCHEMA, "version": 1,
        "installationMode": {"enabled": enabled},
    }), encoding="utf-8")


def _installation(tmp_path: Path, *, source: str = "one") -> WatchInstallation:
    profile = tmp_path / "profile"
    profile.mkdir(exist_ok=True)
    payload = tmp_path / f"payload-{source}"
    payload.mkdir()
    durable = profile / ".copilot-extensions"
    stamped = api.stamp_context(
        payload_root=payload, plugin_id=PLUGIN_ID, durable_home=durable,
        payload_version="1.0.0", payload_origin="explicit",
        source_descriptor={"source": "github", "repo": f"example/{source}"},
        marketplace_key=source, expected_namespace_generation=0,
        expected_install_generation=0, environment={},
    )
    _policy(profile)
    activation = api.compare_and_swap_activation(
        context=stamped["installReceipt"], durable_home=durable,
        expected_marketplace_id=stamped["marketplaceId"],
        expected_plugin_id=PLUGIN_ID,
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"],
        expected_activation_generation=0, activation_mode="namespaced",
        activation_state="active", legacy_disposition="absent",
        legacy_probe={
            "declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z",
        },
        environment={}, os_profile=profile,
    )
    assert activation["status"] == "ready"
    return WatchInstallation.capture(
        receipt=Path(stamped["installReceipt"]), plugin_root=Path(stamped["pluginRoot"]),
        payload_root=payload, durable_home=durable, os_profile=profile, environment={},
    )


@pytest.fixture
def installation(tmp_path: Path) -> WatchInstallation:
    return _installation(tmp_path)


def _mutate(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_capture_and_governance_never_create_watch_state_or_route(
    installation: WatchInstallation,
) -> None:
    assert installation.require_current(environment={})["desiredMode"] == "namespaced"
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


def test_generated_script_and_packaged_primitive_are_canonical() -> None:
    root = Path(__file__).resolve().parents[3]
    canonical = root / "libs" / "installation-context"
    plugin = root / "plugins" / PLUGIN_ID
    for source in canonical.glob("*.py"):
        packaged = "_installation_context.py" if source.name == "installation_context.py" else source.name
        # The PowerShell worker is payload-only in sync-installation-context.py.
        if source.name != "_installation_context_worker.py":
            assert (plugin / "src" / "agent_pull_requests" / packaged).read_bytes() == source.read_bytes()
        assert (plugin / "scripts" / "installation-context" / source.name).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("field", [
    "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
])
def test_capture_refuses_relative_roots(installation: WatchInstallation, field: str) -> None:
    args = {
        key: getattr(installation, key)
        for key in ("receipt", "plugin_root", "payload_root", "durable_home", "os_profile")
    }
    args[field] = Path("relative")
    with pytest.raises(ValueError, match="must be absolute"):
        WatchInstallation.capture(**args, environment={})


def test_capture_refuses_foreign_same_plugin_cell(
    installation: WatchInstallation, tmp_path: Path,
) -> None:
    other = _installation(tmp_path, source="two")
    with pytest.raises(PermissionError, match="another installation"):
        WatchInstallation.capture(
            receipt=other.receipt, plugin_root=installation.plugin_root,
            payload_root=other.payload_root, durable_home=other.durable_home,
            os_profile=other.os_profile, environment={},
        )
    assert not installation.state_root.exists()
    assert not other.state_root.exists()


def test_capture_refuses_foreign_payload(
    installation: WatchInstallation, tmp_path: Path,
) -> None:
    with pytest.raises(api.InstallationContextError, match="Expected payload"):
        WatchInstallation.capture(
            receipt=installation.receipt, plugin_root=installation.plugin_root,
            payload_root=tmp_path / "another-payload", durable_home=installation.durable_home,
            os_profile=installation.os_profile, environment={},
        )


@pytest.mark.parametrize("kind", ["install", "namespace"])
@pytest.mark.parametrize("state", ["inactive", "orphaned", "removing"])
def test_inactive_receipts_refuse(
    installation: WatchInstallation, kind: str, state: str,
) -> None:
    path = (
        installation.receipt if kind == "install"
        else installation.plugin_root.parent.parent / "namespace.json"
    )
    _mutate(path, lambda value: value.update(state=state))
    with pytest.raises((PermissionError, api.InstallationContextError)):
        installation.require_current(environment={})


@pytest.mark.parametrize("kind", ["install", "namespace"])
def test_receipt_advance_refuses_pinned_binding(installation: WatchInstallation, kind: str) -> None:
    path = (
        installation.receipt if kind == "install"
        else installation.plugin_root.parent.parent / "namespace.json"
    )
    _mutate(path, lambda value: value.update(generation=value["generation"] + 1))
    with pytest.raises(PermissionError, match="receipts changed"):
        installation.require_current(environment={})


def test_root_rebinding_without_generation_advance_refuses(installation: WatchInstallation) -> None:
    _mutate(installation.receipt, lambda value: value["roots"].update(state="other-state"))
    with pytest.raises(PermissionError, match="receipts changed"):
        installation.require_current(environment={})
    assert not (installation.plugin_root / "other-state").exists()


def test_receipt_change_during_governance_resolution_refuses(
    installation: WatchInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve = api.resolve_installation_mode

    def advance_after_resolution(**kwargs: Any) -> dict[str, Any]:
        result = resolve(**kwargs)
        _mutate(installation.receipt, lambda value: value.update(generation=2))
        return result

    monkeypatch.setattr(api, "resolve_installation_mode", advance_after_resolution)
    with pytest.raises(PermissionError, match="receipts changed"):
        installation.require_current(environment={})


@pytest.mark.parametrize("policy", ["disabled", "missing", "malformed"])
def test_default_off_and_disabled_governance_refuse(
    installation: WatchInstallation, policy: str,
) -> None:
    path = installation.os_profile / ".copilot-extensions" / "installation-mode.json"
    if policy == "disabled":
        _policy(installation.os_profile, False)
    elif policy == "missing":
        path.unlink()
    else:
        path.write_text("broken", encoding="utf-8")
    with pytest.raises(PermissionError, match="governance blocks mutation"):
        installation.require_current(environment={})


def test_missing_activation_refuses_without_legacy_fallback(installation: WatchInstallation) -> None:
    (installation.plugin_root / "installation-activation.json").unlink()
    with pytest.raises(PermissionError, match="governance blocks mutation"):
        installation.require_current(environment={})


@pytest.mark.parametrize("policy", ["disabled", "missing"])
def test_cleanup_reads_pinned_identity_but_never_grants_normal_mutation(
    installation: WatchInstallation, policy: str,
) -> None:
    path = installation.os_profile / ".copilot-extensions" / "installation-mode.json"
    if policy == "missing":
        path.unlink()
    else:
        _policy(installation.os_profile, False)
    assert installation.read_identity(environment={})["generation"] == 1
    installation.require_cleanup(environment={})
    with pytest.raises(PermissionError, match="governance"):
        installation.require_current(environment={})
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


@pytest.mark.parametrize("blocked", ["malformed-policy", "missing-activation", "maintenance"])
def test_cleanup_does_not_bypass_other_governance_failures(
    installation: WatchInstallation, blocked: str,
) -> None:
    if blocked == "malformed-policy":
        path = installation.os_profile / ".copilot-extensions" / "installation-mode.json"
        path.write_text("broken", encoding="utf-8")
    elif blocked == "missing-activation":
        (installation.plugin_root / "installation-activation.json").unlink()
    else:
        (installation.plugin_root / "maintenance").write_text("maintenance", encoding="utf-8")
    with pytest.raises(PermissionError, match="passive cleanup"):
        with installation.cleanup_transaction(timeout=1, environment={}):
            pytest.fail("blocked installation acquired cleanup custody")
    assert not installation.run_root.exists()


@pytest.mark.parametrize("scope", ["user", "plugin"])
def test_maintenance_marker_blocks_mutation(installation: WatchInstallation, scope: str) -> None:
    root = (
        installation.os_profile / ".copilot-extensions"
        if scope == "user" else installation.plugin_root
    )
    (root / "maintenance").write_text("maintenance", encoding="utf-8")
    with pytest.raises(PermissionError, match="maintenance-blocked"):
        with installation.transaction(timeout=0.5, environment={}):
            pytest.fail("maintenance entered transaction")
    assert not installation.run_root.exists()


def test_linked_receipt_is_not_canonicalized_before_validation(
    installation: WatchInstallation, tmp_path: Path,
) -> None:
    linked = tmp_path / "linked-install.json"
    try:
        linked.symlink_to(installation.receipt)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows test host lacks symbolic-link privilege")
        raise
    with pytest.raises(api.InstallationContextError, match="symbolic link or reparse point"):
        WatchInstallation.capture(
            receipt=linked, plugin_root=installation.plugin_root,
            payload_root=installation.payload_root,
            durable_home=installation.durable_home,
            os_profile=installation.os_profile, environment={},
        )


def test_foreign_environment_activation_refuses(
    installation: WatchInstallation, tmp_path: Path,
) -> None:
    _mutate(
        installation.plugin_root / "installation-activation.json",
        lambda value: value["environment"].update(homeRealPath=str(tmp_path / "foreign")),
    )
    with pytest.raises(PermissionError, match="foreign-environment"):
        installation.require_current(environment={})


def test_current_inherited_payload_is_validated(installation: WatchInstallation) -> None:
    with pytest.raises(api.InstallationContextError, match="COPILOT_PLUGIN_ROOT conflicts"):
        installation.require_current(environment={"COPILOT_PLUGIN_ROOT": str(Path(__file__).parent)})


def test_transaction_holds_exact_shared_lease_through_body_and_releases(
    installation: WatchInstallation,
) -> None:
    with installation.transaction(timeout=0.5, environment={}) as guard:
        assert guard.path == installation.run_root / "zdd-cutover.lock"
        assert guard.held
        with guard.borrow():
            assert guard.held
        with pytest.raises(CutoverLockedError):
            CutoverLock(installation.run_root).acquire()
    assert not guard.held
    contender = CutoverLock(installation.run_root)
    contender.acquire()
    contender.release()
    assert not installation.state_root.exists()


def test_transaction_rechecks_governance_after_lock_acquisition(
    installation: WatchInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquire = CutoverLock.acquire

    def flip_after_acquire(self: CutoverLock, **kwargs: Any) -> None:
        acquire(self, **kwargs)
        _policy(installation.os_profile, False)

    monkeypatch.setattr(CutoverLock, "acquire", flip_after_acquire)
    with pytest.raises(PermissionError, match="governance blocks mutation"):
        with installation.transaction(timeout=0.5, environment={}):
            pytest.fail("disabled governance entered transaction body")
    monkeypatch.setattr(CutoverLock, "acquire", acquire)
    contender = CutoverLock(installation.run_root)
    contender.acquire()
    contender.release()


def test_transaction_releases_on_body_failure(installation: WatchInstallation) -> None:
    with pytest.raises(RuntimeError, match="body"):
        with installation.transaction(timeout=0.5, environment={}):
            raise RuntimeError("body failed")
    contender = CutoverLock(installation.run_root)
    contender.acquire()
    contender.release()


def test_independent_cells_do_not_contend_or_share_roots(
    installation: WatchInstallation, tmp_path: Path,
) -> None:
    other = _installation(tmp_path, source="two")
    assert other.state_root != installation.state_root
    assert other.run_root != installation.run_root
    with installation.transaction(timeout=0.5, environment={}):
        with other.transaction(timeout=0.5, environment={}):
            assert other.require_current(environment={})["status"] == "ready"


_CONTENDER = """
import sys
from pathlib import Path
from agent_pull_requests.watch_installation import WatchInstallation
from zdd.cutover_lock import CutoverLockedError
binding = WatchInstallation.capture(
    **dict(zip(("receipt", "plugin_root", "payload_root", "durable_home", "os_profile"),
               (Path(arg) for arg in sys.argv[1:]))), environment={},
)
try:
    with binding.transaction(timeout=0.1, environment={}):
        print("acquired", flush=True)
except CutoverLockedError:
    print("contended", flush=True)
"""


def test_real_subprocess_transaction_uses_same_native_lease(
    installation: WatchInstallation,
) -> None:
    command = [
        sys.executable, "-c", _CONTENDER,
        *(str(getattr(installation, key)) for key in (
            "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
        )),
    ]
    with installation.transaction(timeout=0.5, environment={}):
        blocked = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=10,
            **no_window_kwargs(),
        )
        assert blocked.stdout.strip() == "contended"
    acquired = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=10,
        **no_window_kwargs(),
    )
    assert acquired.stdout.strip() == "acquired"
    assert not installation.state_root.exists()


def test_inherited_binding_refuses_before_lock_or_validation(
    installation: WatchInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited = replace(installation, _creating_pid=os.getpid() + 1)
    monkeypatch.setattr(
        api, "validate_context_receipt",
        lambda *a, **k: pytest.fail("inherited binding accessed receipts"),
    )
    with pytest.raises(PermissionError, match="another process"):
        inherited.require_current(environment={})
    with pytest.raises(PermissionError, match="another process"):
        with inherited.transaction(timeout=0.5, environment={}):
            pytest.fail("inherited binding entered transaction")
    assert not inherited.run_root.exists()


def test_transition_origin_survives_replacement_without_reviving_old_binding(
    installation: WatchInstallation, tmp_path: Path,
) -> None:
    from test_watch_runtime import _advance_installation

    original = installation.evidence()
    before = json.dumps(original, sort_keys=True)
    advance = cast(Callable[[WatchInstallation, Path], WatchInstallation], _advance_installation)
    fresh = advance(installation, tmp_path)
    installation.payload_root.rmdir()
    with pytest.raises((PermissionError, api.InstallationContextError)):
        installation.require_current(environment={})
    with fresh.transaction(timeout=1, environment={}) as guard:
        fresh.require_transition_origin(original, guard=guard, environment={})
        assert guard.held
    assert json.dumps(original, sort_keys=True) == before
    assert original["generation"] < fresh.evidence()["generation"]
    assert original["payloadRoot"] != fresh.evidence()["payloadRoot"]
    assert not fresh.state_root.exists()


@pytest.mark.parametrize("field", [
    "marketplaceId", "pluginId", "sourceFingerprint", "cellRoot", "pluginRoot",
    "versionsRoot", "snapshotsRoot", "stateRoot", "runRoot", "logsRoot",
    "cacheRoot", "launchersRoot", "reposRoot", "namespaceReceipt", "installReceipt",
])
def test_transition_origin_keeps_every_immutable_scope_field(
    installation: WatchInstallation, field: str,
) -> None:
    original = installation.evidence()
    original[field] += "-foreign"
    with installation.transaction(timeout=1, environment={}) as guard:
        with pytest.raises(PermissionError, match=field):
            installation.require_transition_origin(original, guard=guard, environment={})
    assert not installation.state_root.exists()


@pytest.mark.parametrize("change", [
    "missing", "extra", "foreign", "root", "payload", "generation",
    "namespace-generation", "boolean-generation", "non-object",
])
def test_transition_origin_refuses_incomplete_or_conflicting_provenance(
    installation: WatchInstallation, tmp_path: Path, change: str,
) -> None:
    original = installation.evidence()
    if change == "missing":
        original.pop("payloadRoot")
    elif change == "extra":
        original["unknown"] = True
    elif change == "foreign":
        original = _installation(tmp_path, source="two").evidence()
    elif change == "root":
        original["stateRoot"] += "-foreign"
    elif change == "payload":
        original["payloadRoot"] = "relative"
    elif change == "generation":
        original["generation"] += 1
    elif change == "namespace-generation":
        original["namespaceGeneration"] = 0
    elif change == "boolean-generation":
        original["generation"] = True
    with installation.transaction(timeout=1, environment={}) as guard:
        with pytest.raises(PermissionError, match="transition origin"):
            installation.require_transition_origin(
                None if change == "non-object" else original, guard=guard, environment={},
            )
    assert not installation.state_root.exists()


@pytest.mark.parametrize("custody", ["unheld", "released", "foreign", "other-thread"])
def test_transition_origin_requires_current_transaction_custody(
    installation: WatchInstallation, tmp_path: Path, custody: str,
) -> None:
    from zdd.cutover_lock import CutoverBorrowError

    guard = CutoverLock(
        tmp_path / "foreign-run" if custody == "foreign" else installation.run_root,
    )
    if custody != "unheld":
        guard.acquire()
    if custody == "released":
        guard.release()
    try:
        def check() -> None:
            with pytest.raises((PermissionError, CutoverBorrowError)):
                installation.require_transition_origin(
                    installation.evidence(), guard=guard, environment={},
                )

        if custody == "other-thread":
            # Propagate assertions from the worker, not only thread warnings.
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(check).result(timeout=5)
        else:
            check()
    finally:
        guard.release()
    assert not installation.state_root.exists()


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_transition_origin_rechecks_complete_current_admission(
    installation: WatchInstallation, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    original = installation.evidence()
    require_current = installation.require_current
    calls = 0

    def changed(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == (1 if boundary == "before" else 2):
            _policy(installation.os_profile, False)
        return require_current(**kwargs)

    with installation.transaction(timeout=1, environment={}) as guard:
        monkeypatch.setattr(WatchInstallation, "require_current", lambda _self, **kw: changed(**kw))
        with pytest.raises(PermissionError, match="governance"):
            installation.require_transition_origin(original, guard=guard, environment={})
    assert not installation.state_root.exists()
