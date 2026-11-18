"""Portable enrollment-state controls; no user manager or live enrollment."""

import copy
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import sys
from typing import Any, cast

import pytest

from agent_machines import (
    _peer_launch, cli_health, health_enrollment as enrollment,
    health_enrollment_state as state, health_job_support, health_jobs, health_run, health_slots,
)

TOKEN = "a" * 64
IDENTITY = "1" * 32 + ":" + "2" * 64
OWNER = "urn:copilot-extensions:health-job:" + "c" * 64


def desired() -> state.LinuxDesired:
    return {
        "store": "/example/store", "policy_token": TOKEN,
        "account_uid": 1000, "enabled": True,
    }


def record() -> state.LinuxRecord:
    wanted = desired()
    return {
        "desired": wanted, "version": "1.0", "identity": IDENTITY,
        "definition": health_jobs.installed_linux_definition(
            slot="/example/runtime/versions/1.0",
            python="/example/runtime/versions/1.0/.venv/bin/python",
            identity=IDENTITY, store=wanted["store"], policy_token=TOKEN,
            owner=OWNER, account_uid=1000, enabled=True,
        ),
    }


def test_linux_consent_and_recovery_round_trip_without_reinterpreting_windows() -> None:
    value = state.empty_linux()
    assert state.validate_linux(value, account_uid=1000) == value
    before = record()
    value["desired"], value["confirmed"] = desired(), before
    value["pending"] = {
        "before": before, "candidate": before, "token": TOKEN,
        "references": ["1.0"], "intent": "replace", "native_unsettled": True,
    }
    assert state.validate_linux(copy.deepcopy(value), account_uid=1000) == value
    windows = {
        "schema": state.SCHEMA, "version": 1,
        "desired": None, "confirmed": None, "pending": None,
    }
    with pytest.raises(ValueError, match="platform"):
        state.validate_linux(windows, account_uid=1000)
    with pytest.raises(ValueError, match="platform"):
        state.validate_linux({**value, "platform": "win32"}, account_uid=1000)


def test_account_isolation_applies_to_confirmed_and_pending_records() -> None:
    value = state.empty_linux()
    value["desired"], value["confirmed"] = desired(), record()
    with pytest.raises(ValueError, match="account"):
        state.validate_linux(value, account_uid=1001)
    for location in ("before", "candidate"):
        value = state.empty_linux()
        foreign = record()
        foreign["desired"]["account_uid"] = 1001
        value["pending"] = {
            "before": foreign if location == "before" else None,
            "candidate": foreign if location == "candidate" else None,
            "token": TOKEN, "references": ["1.0"],
            "intent": "remove", "native_unsettled": False,
        }
        with pytest.raises(ValueError, match="account"):
            state.validate_linux(value, account_uid=1000)


@pytest.mark.parametrize("uid", [True, -1, 2**32 - 1, "1000", "S-1-5-21-1-2-3-1000"])
def test_linux_state_never_admits_sid_or_coerced_uid(uid: object) -> None:
    with pytest.raises(ValueError, match="account"):
        state.linux_desired({**desired(), "account_uid": uid}, account_uid=1000)


@pytest.mark.parametrize("mutation", [
    {"store": "C:\\store"}, {"store": "/example/../store"},
    {"store": "/example/store\n"}, {"user_id": "S-1-5-21-1-2-3-1000"},
])
def test_linux_desired_refuses_windows_and_uncontrolled_options(mutation: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="enrollment"):
        state.linux_desired({**desired(), **mutation}, account_uid=1000)


def test_state_versions_native_formats_and_pending_references_fail_closed() -> None:
    value = state.empty_linux()
    with pytest.raises(ValueError, match="state"):
        state.validate_linux({**value, "version": 3}, account_uid=1000)
    broken = {**record(), "definition": "<Task/>"}
    with pytest.raises(ValueError, match="definition"):
        state.linux_record(broken, account_uid=1000)
    for versions in (["../1.0"], ["1.0", "1.0"], ["dev"], ["1\\two"]):
        value["pending"] = {
            "before": None, "candidate": record(), "token": TOKEN,
            "references": versions, "intent": "replace", "native_unsettled": False,
        }
        with pytest.raises(ValueError, match="recovery"):
            state.validate_linux(value, account_uid=1000)


def test_pair_metadata_cannot_change_account_enabled_intent_or_native_names() -> None:
    for field, replacement in (("account_uid", 1001), ("enabled", False), ("owner", OWNER + "d")):
        broken = copy.deepcopy(record())
        definition: dict[str, object] = {**broken["definition"], field: replacement}
        with pytest.raises(ValueError, match="definition"):
            state.linux_record({**broken, "definition": definition}, account_uid=1000)
    broken = record()
    broken["definition"]["artifacts"]["foreign.service"] = "[Service]\nExecStart=/bin/true\n"
    with pytest.raises(ValueError, match="definition"):
        state.linux_record(broken, account_uid=1000)


class References:
    def __init__(self) -> None:
        self.owners: dict[str, list[str]] = {}
        self.pending: dict[str, str] | None = None

    def reference_transaction_custody(self, *_args: Any, **_kwargs: Any) -> Any:
        return nullcontext()

    def _read_runtime_reference_state(self, *_args: Any, **_kwargs: Any) -> Any:
        return copy.deepcopy(self.owners), self.pending

    def read_runtime_references(self, *_args: Any) -> dict[str, list[str]]:
        assert self.pending is None
        return copy.deepcopy(self.owners)

    def begin_runtime_reference_transaction(self, _root: Path, owner: str) -> dict[str, Any]:
        self.pending = self.pending or {"owner": owner, "token": TOKEN}
        assert self.pending["owner"] == owner
        return {"token": TOKEN, "owners": copy.deepcopy(self.owners)}

    def commit_runtime_reference_transaction(
        self, _root: Path, owner: str, token: str,
        updates: dict[str, list[str]], *, expected: dict[str, list[str]],
    ) -> None:
        assert self.pending == {"owner": owner, "token": token}
        assert {key: self.owners.get(key, []) for key in expected} == expected
        self.owners.update(updates)
        self.pending = None

    def replace_runtime_references(
        self, _root: Path, owner: str, versions: list[str], *, expected: list[str],
    ) -> None:
        assert self.pending is None and self.owners.get(owner, []) == expected
        self.owners[owner] = versions


class Pair:
    def __init__(self) -> None:
        self.definition: dict[str, Any] | None = None
        self.calls: list[str] = []
        self.instances: list[str] = []
        self.fail_after = ""
        self.unavailable = False
        self.loaded = True

    def call(
        self, operation: str, *, expected: str = "", definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.unavailable:
            raise RuntimeError("native manager unavailable")
        observed = self.snapshot()
        if operation == "read":
            return observed
        assert expected == observed["digest"]
        self.calls.append(operation)
        if operation in {"create", "replace"}:
            assert definition is not None and not definition["enabled"]
            self.definition = copy.deepcopy(definition)
            if self.fail_after == "partial":
                del self.definition["artifacts"][next(reversed(self.definition["artifacts"]))]
                self.fail_after = ""
                self.unavailable = True
                raise RuntimeError("interrupted publication")
        elif operation == "enable":
            assert self.definition is not None
            self.definition["enabled"] = True
        elif operation == "disable":
            assert self.definition is not None
            self.definition["enabled"] = False
            self.instances.clear()
        elif operation == "remove":
            self.definition = None
        if self.fail_after == operation:
            self.fail_after = ""
            raise RuntimeError("lost native reply")
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "configured": self.definition is not None, "definition": copy.deepcopy(self.definition),
            "enabled": bool(self.definition and self.definition["enabled"]), "account_uid": 1000,
            "instances": self.instances[:], "loaded": self.loaded and bool(
                self.definition and len(self.definition["artifacts"]) == 2
            ),
            "digest": hashlib.sha256(json.dumps(self.definition, sort_keys=True).encode()).hexdigest(),
        }


def linux_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[enrollment.Enrollment, Pair, References, list[Any]]:
    references, pair = References(), Pair()
    monkeypatch.setattr(enrollment, "local_uid", lambda: 1000)
    monkeypatch.setattr(health_run, "policy_preview", lambda _store: ({}, "", TOKEN))
    runtime = SimpleNamespace(
        _runtime_component=lambda _name: references, retention_lease=lambda *_args: nullcontext(),
    )

    def installation(version: str) -> Any:
        def definition(wanted: dict[str, Any], owner: str, *, enabled: bool) -> health_jobs.LinuxDefinition:
            slot = "/example/runtime/versions/" + version
            return health_jobs.installed_linux_definition(
                slot=slot, python=slot + "/.venv/bin/python", identity=IDENTITY,
                store=wanted["store"], policy_token=wanted["policy_token"], owner=owner,
                account_uid=wanted["account_uid"], enabled=enabled,
            )
        return SimpleNamespace(
            root=tmp_path, slot=tmp_path / "versions" / version, runtime=runtime,
            identity=IDENTITY, platform="linux", definition=definition,
        )

    selected = [installation("1.0")]
    owner = enrollment.Enrollment(
        selected[0], backend=pair,
        selector=lambda _root, version=None: installation(version) if version else selected[0],
        qualifier=lambda *_args: None,
    )
    selected.append(installation)
    return owner, pair, references, selected


def adopt(owner: enrollment.Enrollment, wanted: dict[str, Any] | None = None) -> dict[str, Any]:
    wanted = cast(dict[str, Any], wanted if wanted is not None else desired())
    preview = owner.configure(wanted)
    return owner.configure(wanted, apply=True, expected=preview["preview_token"])


def test_linux_uses_shared_opt_in_noop_replacement_and_withdrawal_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, pair, references, selected = linux_case(tmp_path, monkeypatch)
    assert owner.converge()["status"] == "not-adopted"
    assert not owner.path.exists() and not pair.calls and not references.owners
    result = adopt(owner)
    assert result["platform"] == "linux" and result["status"] == "converged"
    assert pair.calls == ["create", "enable"]
    pair.calls.clear()
    pair.instances = ["active invocation"]
    assert owner.converge()["status"] == "unchanged" and not pair.calls
    selected[0] = selected[1]("2.0")
    assert owner.converge()["status"] == "converged"
    assert pair.calls == ["disable", "replace", "enable"] and not pair.instances
    assert references.owners[enrollment.REFERENCE] == ["2.0"]
    assert owner.disable()["status"] == "disabled"
    assert owner.read()["desired"]["enabled"] is False
    assert owner.converge(remove=True)["status"] == "removed"
    assert not pair.definition and references.owners[enrollment.REFERENCE] == []


def test_linux_replacement_rollback_and_partial_publication_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, pair, references, selected = linux_case(tmp_path, monkeypatch)
    adopt(owner)
    before = copy.deepcopy(pair.definition)
    selected[0] = selected[1]("2.0")
    pair.fail_after = "enable"
    assert owner.converge()["rollback"] == "confirmed"
    assert pair.definition == before and references.owners[enrollment.REFERENCE] == ["1.0"]
    pair.fail_after = "partial"
    result = owner.converge()
    assert result["status"] == "unknown" and result["recovery_required"]
    assert owner.read()["pending"] is not None and references.pending is not None
    assert references.owners[enrollment.REFERENCE] == ["1.0"]
    pair.unavailable = False
    result = owner.converge()
    assert result["status"] == "rolled-back" and result["rollback"] == "confirmed"
    assert pair.definition == before and references.pending is None


def test_linux_unknown_fresh_publication_can_be_removed_without_adopting_another_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, pair, references, _ = linux_case(tmp_path, monkeypatch)
    pair.fail_after = "partial"
    assert adopt(owner)["status"] == "unknown"
    pair.unavailable = False
    assert owner.converge(remove=True)["status"] == "removed"
    assert not pair.definition and not owner.read()["pending"] and references.pending is None
    assert "enable" not in pair.calls


def test_disable_after_partial_replacement_restores_compatible_pair_without_reenabling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, pair, references, selected = linux_case(tmp_path, monkeypatch)
    adopt(owner)
    selected[0] = selected[1]("2.0")
    pair.fail_after = "partial"
    assert owner.converge()["status"] == "unknown"
    pair.unavailable = False
    pair.calls.clear()
    result = owner.disable()
    assert result["status"] == "disabled" and not result["enabled"] and not result["recovery_required"]
    assert owner.read()["confirmed"]["version"] == "1.0"
    assert references.owners[enrollment.REFERENCE] == ["1.0"] and "enable" not in pair.calls


@pytest.mark.parametrize("loss", ["policy", "support"])
@pytest.mark.parametrize("withdrawal", ["disable", "remove"])
def test_partial_publication_withdrawal_does_not_require_collection_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loss: str, withdrawal: str,
) -> None:
    owner, pair, references, selected = linux_case(tmp_path, monkeypatch)
    adopt(owner)
    selected[0] = selected[1]("2.0")
    pair.fail_after = "partial"
    assert owner.converge()["status"] == "unknown"
    pair.unavailable = False
    pair.calls.clear()
    if loss == "policy":
        monkeypatch.setattr(health_run, "policy_preview", lambda _store: ({}, "", "b" * 64))
    else:
        def unavailable(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("installed-job-collection-unsupported")

        owner.selector = unavailable
        owner.qualifier = unavailable
    result = owner.disable() if withdrawal == "disable" else owner.converge(remove=True)
    assert result["status"] == ("disabled" if withdrawal == "disable" else "removed")
    assert not result["enabled"] and not result["instances"] and not result["recovery_required"]
    assert references.pending is None and "enable" not in pair.calls
    assert references.owners[enrollment.REFERENCE] == (["1.0"] if withdrawal == "disable" else [])


def test_disabled_candidate_is_not_accepted_until_manager_load_is_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, pair, references, _ = linux_case(tmp_path, monkeypatch)
    pair.fail_after = "create"
    pair.loaded = False
    wanted = {**desired(), "enabled": False}
    # Keep the first failed publication pending rather than immediately rolling back.
    switch = owner._switch

    def interrupted(native: dict[str, Any], candidate: dict[str, Any] | None) -> dict[str, Any]:
        try:
            return switch(native, candidate)
        except RuntimeError:
            pair.unavailable = True
            raise

    monkeypatch.setattr(owner, "_switch", interrupted)
    assert adopt(owner, wanted)["status"] == "unknown"
    pending = owner.read()
    # Exercise candidate acceptance, not the independent withdrawal intent.
    pending["pending"]["intent"] = "replace"
    health_slots._write(owner.path, pending)
    pair.unavailable = False
    monkeypatch.setattr(owner, "_switch", switch)
    result = owner.converge()
    assert result["status"] == "rolled-back" and result["rollback"] == "confirmed"
    assert not owner.read()["confirmed"] and not owner.read()["pending"]
    assert references.owners[enrollment.REFERENCE] == []
    assert "enable" not in pair.calls


def test_cross_platform_state_and_account_changes_refuse_before_native_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, pair, _, _ = linux_case(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="account"):
        owner.configure({**desired(), "account_uid": 1001})
    owner.path.write_text(json.dumps(enrollment.empty()))
    with pytest.raises(ValueError, match="platform"):
        owner.status()
    assert not pair.calls


def test_normal_cli_derives_local_uid_requires_confirmation_and_refuses_windows_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    owner, pair, _, _ = linux_case(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(cli_health, "Path", PurePosixPath)
    monkeypatch.setattr(enrollment, "select", lambda **_kwargs: owner.installation)
    monkeypatch.setattr(enrollment, "Enrollment", lambda _installation: owner)
    parser = argparse.ArgumentParser()
    cli_health.add_parser(parser.add_subparsers())
    argv = ["health", "job", "configure", "--store-dir", "/example/store", "--enabled"]
    args = parser.parse_args(argv)
    assert cli_health.run_job(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview" and not owner.path.exists()
    args = parser.parse_args([*argv, "--apply", "--expect", preview["preview_token"]])
    assert cli_health.run_job(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "converged"
    assert owner.read()["desired"]["account_uid"] == 1000
    calls = pair.calls[:]
    args = parser.parse_args([*argv, "--user-id", "S-1-5-21-1-2-3-1000"])
    assert cli_health.run_job(args) == 2 and pair.calls == calls
    assert json.loads(capsys.readouterr().err)["error"] == "platform-job-options-mismatch"


def test_linux_selection_uses_retained_support_without_gui_host_or_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_procutil

    root = tmp_path / "runtime"
    slot = root / "versions" / "1.0"
    environment = slot / ".venv"
    python = environment / "bin" / "python"
    runtime = SimpleNamespace(current_version=lambda _root: "1.0", slot_python=lambda *_args: python)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setattr(_peer_launch, "_load_primitive", lambda path, _name: (
        runtime if path.name == "versioned_runtime.py" else SimpleNamespace()
    ))
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", lambda *_args: IDENTITY)
    monkeypatch.setattr(_peer_launch, "retained_runtime_prefix", lambda *_args, **_kwargs: [])
    checked: list[Path] = []
    monkeypatch.setattr(health_job_support, "require_collection_support", checked.append)

    def no_gui(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("Linux collection must not require a GUI interpreter")

    monkeypatch.setattr(agent_procutil, "windowless_python", no_gui)
    selected = enrollment.select()
    assert selected.platform == "linux" and selected.gui == "" and selected.python == python
    assert checked == [python]
