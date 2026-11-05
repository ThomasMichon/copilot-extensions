"""The native source adapter borrows, rather than invents, installation authority."""
from __future__ import annotations

import importlib.util
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("outcome", [
    "accepted", "revoked", "foreign-owner", "artifact",
    "foreign-lock", "foreign-profile", "local-packages", "foreign-native",
    "no-lifecycle", "lost-lifecycle",
])
def test_source_adapter_retains_live_owner_through_native_install(tmp_path, monkeypatch, outcome):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    payload = tmp_path / "installed"
    payload.mkdir()
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    root = tmp_path / "runtime"
    identity = SimpleNamespace(
        plugin="agent-worktrees", document=lambda: {"plugin": "agent-worktrees"},
    )
    policy = {
        "status": "ready",
        "owner": {
            "pluginId": "foreign" if outcome == "foreign-owner" else "agent-worktrees",
            "runtimeRoot": str(root),
        },
        "selection": {"allowSourceFallback": True},
    }
    plan = SimpleNamespace(
        policy=lambda: policy, source_trigger="release-index-unavailable",
        release_selection=object() if outcome == "artifact" else None,
    )
    profile = {"target": {"environment": {"python_version": "3.11"}}}
    binding = {
        "profile": {} if outcome == "foreign-profile" else profile,
        "source_lock": {
            "policy": {} if outcome == "foreign-lock" else policy,
            "component": "agent-worktrees", "source": identity.document(), "extras": [],
            "trigger": plan.source_trigger, "target": profile["target"],
        },
        "build_toolchain": {}, "build_profile": {},
    }
    wheels = SimpleNamespace(receipt=lambda: {
        "binding": binding, "lock_id": "sha256:" + "a" * 64,
        "distributions": {"agent-worktrees": {}},
    })
    held, events = [], []

    def verify():
        assert held == [True]
        events.append("verify")
        if outcome == "revoked" and events.count("verify") == 3:
            raise ValueError("source policy revoked")

    source = SimpleNamespace(
        payload=prepared, identity=identity, profile=SimpleNamespace(record=lambda: profile),
        wheels=wheels, runtime_version="selected.profile." + "a" * 16,
        local_packages=("foreign",) if outcome == "local-packages" else ("agent-worktrees",),
        verify=verify,
    )

    @contextmanager
    def retain(actual, **options):
        assert actual is plan and options["installation"]["payload_root"] == payload
        held.append(True)
        try:
            yield source
        finally:
            held.pop()
            events.append("released")

    engine = SimpleNamespace(
        _package_roots=lambda path: (path,),
        _package_name=lambda _path: "agent-worktrees",
        _source_files=lambda _paths: (),
        _construction_hash=lambda _files, _extras, _local: "frozen-source",
        _wheel_construction_hash=lambda _source, _wheels: "e" * 64,
    )
    native = SimpleNamespace(
        __file__=str(tmp_path / "foreign.py" if outcome == "foreign-native"
                     else scripts / "runtime_install.py"),
        engine=engine,
        runtime_source=SimpleNamespace(manifest_fields=lambda _identity: {"identity": "selected"}),
    )

    lifecycle_checks = []

    def require_lifecycle(**_kwargs):
        lifecycle_checks.append(True)
        if outcome == "no-lifecycle":
            raise ValueError("whole-operation lifecycle is missing")
        if outcome == "lost-lifecycle" and len(lifecycle_checks) == 4:
            raise ValueError("whole-operation lifecycle was lost")

    native.Governance = lambda *_args, **_kwargs: SimpleNamespace(
        require_owned_lifecycle=require_lifecycle,
    )

    def install(actual_root, actual_source, **options):
        assert held == [True] and actual_root == root and actual_source == prepared
        assert options["payload"] == payload and options["wheel_inputs"] is wheels
        assert options["expected_source_identity"] == {"identity": "selected"}
        assert options["expected_wheel_selection"] == {
            "identity": {"identity": "selected"}, "source_descriptor": None,
            "runtime_version": source.runtime_version, "payload_hash": "e" * 64,
            "extras": [], "local_packages": ["agent-worktrees"],
        }
        options["_before_publish"]()
        events.append("helpers")
        options["_before_publish"]()
        events.append("selector")
        return {"runtime_version": source.runtime_version}

    native.install = install
    monkeypatch.setitem(sys.modules, "runtime_install", native)
    monkeypatch.setitem(
        sys.modules, "runtime_source_build", SimpleNamespace(retain_source_runtime_inputs=retain),
    )
    specification = importlib.util.spec_from_file_location(
        "_test_worktrees_source_owner", scripts / "runtime_install_source.py",
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    installation = {"payload_root": payload, "source_descriptor": None}
    (tmp_path / "uv").touch()
    arguments = {
        "installation": installation, "component": "agent-worktrees",
        "python": Path(sys.executable), "uv": str(tmp_path / "uv"),
        "deadline": time.monotonic() + 60,
    }
    if outcome in (
        "foreign-owner", "artifact", "foreign-lock", "foreign-profile",
        "local-packages", "foreign-native", "no-lifecycle",
    ):
        with pytest.raises(ValueError):
            module.install_source_runtime(plan, **arguments)
        assert "helpers" not in events and "selector" not in events
    elif outcome == "revoked":
        with pytest.raises(ValueError, match="source policy revoked"):
            module.install_source_runtime(plan, **arguments)
        assert events == ["verify", "verify", "helpers", "verify", "released"]
    elif outcome == "lost-lifecycle":
        with pytest.raises(ValueError, match="lifecycle was lost"):
            module.install_source_runtime(plan, **arguments)
        assert events == ["verify", "verify", "helpers", "released"]
    else:
        result = module.install_source_runtime(plan, **arguments)
        assert result["runtime_version"] == source.runtime_version
        assert events == [
            "verify", "verify", "helpers", "verify", "selector", "verify", "released",
        ]
    assert held == []
