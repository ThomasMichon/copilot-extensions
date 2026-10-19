"""Real canonical receipts and governance for the internal owner binding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from agent_pull_requests import _installation_context as api
from agent_pull_requests.watch_installation import PLUGIN_ID, WatchInstallation


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
def installation(tmp_path):
    return _installation(tmp_path)


def _mutate(path: Path, change) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_capture_and_governance_never_create_watch_state_or_route(installation):
    assert installation.require_current(environment={})["desiredMode"] == "namespaced"
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


def test_generated_script_and_packaged_primitive_are_canonical():
    root = Path(__file__).resolve().parents[3]
    canonical = root / "libs" / "installation-context"
    plugin = root / "plugins" / PLUGIN_ID
    for source in canonical.glob("*.py"):
        packaged = "_installation_context.py" if source.name == "installation_context.py" else source.name
        assert (plugin / "src" / "agent_pull_requests" / packaged).read_bytes() == source.read_bytes()
        assert (plugin / "scripts" / "installation-context" / source.name).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("field", [
    "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
])
def test_capture_refuses_relative_roots(installation, field):
    args = {
        key: getattr(installation, key)
        for key in ("receipt", "plugin_root", "payload_root", "durable_home", "os_profile")
    }
    args[field] = Path("relative")
    with pytest.raises(ValueError, match="must be absolute"):
        WatchInstallation.capture(**args, environment={})


def test_capture_refuses_foreign_same_plugin_cell(installation, tmp_path):
    other = _installation(tmp_path, source="two")
    with pytest.raises(PermissionError, match="another installation"):
        WatchInstallation.capture(
            receipt=other.receipt, plugin_root=installation.plugin_root,
            payload_root=other.payload_root, durable_home=other.durable_home,
            os_profile=other.os_profile, environment={},
        )
    assert not installation.state_root.exists()
    assert not other.state_root.exists()


def test_capture_refuses_foreign_payload(installation, tmp_path):
    with pytest.raises(api.InstallationContextError, match="Expected payload"):
        WatchInstallation.capture(
            receipt=installation.receipt, plugin_root=installation.plugin_root,
            payload_root=tmp_path / "another-payload", durable_home=installation.durable_home,
            os_profile=installation.os_profile, environment={},
        )


@pytest.mark.parametrize("kind", ["install", "namespace"])
@pytest.mark.parametrize("state", ["inactive", "orphaned", "removing"])
def test_inactive_receipts_refuse(installation, kind, state):
    path = (
        installation.receipt if kind == "install"
        else installation.plugin_root.parent.parent / "namespace.json"
    )
    _mutate(path, lambda value: value.update(state=state))
    with pytest.raises((PermissionError, api.InstallationContextError)):
        installation.require_current(environment={})


@pytest.mark.parametrize("kind", ["install", "namespace"])
def test_receipt_advance_refuses_pinned_binding(installation, kind):
    path = (
        installation.receipt if kind == "install"
        else installation.plugin_root.parent.parent / "namespace.json"
    )
    _mutate(path, lambda value: value.update(generation=value["generation"] + 1))
    with pytest.raises(PermissionError, match="receipts changed"):
        installation.require_current(environment={})


def test_root_rebinding_without_generation_advance_refuses(installation):
    _mutate(installation.receipt, lambda value: value["roots"].update(state="other-state"))
    with pytest.raises(PermissionError, match="receipts changed"):
        installation.require_current(environment={})
    assert not (installation.plugin_root / "other-state").exists()


def test_receipt_change_during_governance_resolution_refuses(installation, monkeypatch):
    resolve = api.resolve_installation_mode

    def advance_after_resolution(**kwargs):
        result = resolve(**kwargs)
        _mutate(installation.receipt, lambda value: value.update(generation=2))
        return result

    monkeypatch.setattr(api, "resolve_installation_mode", advance_after_resolution)
    with pytest.raises(PermissionError, match="receipts changed"):
        installation.require_current(environment={})


@pytest.mark.parametrize("policy", ["disabled", "missing", "malformed"])
def test_default_off_and_disabled_governance_refuse(installation, policy):
    path = installation.os_profile / ".copilot-extensions" / "installation-mode.json"
    if policy == "disabled":
        _policy(installation.os_profile, False)
    elif policy == "missing":
        path.unlink()
    else:
        path.write_text("broken", encoding="utf-8")
    with pytest.raises(PermissionError, match="governance blocks mutation"):
        installation.require_current(environment={})


def test_missing_activation_refuses_without_legacy_fallback(installation):
    (installation.plugin_root / "installation-activation.json").unlink()
    with pytest.raises(PermissionError, match="governance blocks mutation"):
        installation.require_current(environment={})


@pytest.mark.parametrize("policy", ["disabled", "missing"])
def test_cleanup_reads_pinned_identity_but_never_grants_normal_mutation(installation, policy):
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
def test_cleanup_does_not_bypass_other_governance_failures(installation, blocked):
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
def test_maintenance_marker_blocks_mutation(installation, scope):
    root = (
        installation.os_profile / ".copilot-extensions"
        if scope == "user" else installation.plugin_root
    )
    (root / "maintenance").write_text("maintenance", encoding="utf-8")
    with pytest.raises(PermissionError, match="maintenance-blocked"):
        with installation.transaction(timeout=0.5, environment={}):
            pytest.fail("maintenance entered transaction")
    assert not installation.run_root.exists()


def test_linked_receipt_is_not_canonicalized_before_validation(installation, tmp_path):
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


def test_foreign_environment_activation_refuses(installation, tmp_path):
    _mutate(
        installation.plugin_root / "installation-activation.json",
        lambda value: value["environment"].update(homeRealPath=str(tmp_path / "foreign")),
    )
    with pytest.raises(PermissionError, match="foreign-environment"):
        installation.require_current(environment={})


def test_current_inherited_payload_is_validated(installation):
    with pytest.raises(api.InstallationContextError, match="COPILOT_PLUGIN_ROOT conflicts"):
        installation.require_current(environment={"COPILOT_PLUGIN_ROOT": str(Path(__file__).parent)})


def test_transaction_holds_exact_shared_lease_through_body_and_releases(installation):
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


def test_transaction_rechecks_governance_after_lock_acquisition(installation, monkeypatch):
    acquire = CutoverLock.acquire

    def flip_after_acquire(self, **kwargs):
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


def test_transaction_releases_on_body_failure(installation):
    with pytest.raises(RuntimeError, match="body"):
        with installation.transaction(timeout=0.5, environment={}):
            raise RuntimeError("body failed")
    contender = CutoverLock(installation.run_root)
    contender.acquire()
    contender.release()


def test_independent_cells_do_not_contend_or_share_roots(installation, tmp_path):
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


def test_real_subprocess_transaction_uses_same_native_lease(installation):
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


def test_inherited_binding_refuses_before_lock_or_validation(installation, monkeypatch):
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
