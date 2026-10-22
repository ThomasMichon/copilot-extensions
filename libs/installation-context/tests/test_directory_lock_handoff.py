"""Live owner handoff is contention, not corrupt installation evidence."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def module(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "installation_context.py"
    spec = importlib.util.spec_from_file_location("handoff_installation_context", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("replacement", [False, True])
def test_changing_file_observation_is_distinct_from_invalid_evidence(
    module, tmp_path, monkeypatch, replacement,
):
    path = tmp_path / "owner.json"
    path.write_text('{"owner":1}', encoding="utf-8")
    original_read = module.os.read
    original_close = module.os.close
    changed = False
    observed_descriptor = None

    def read(descriptor, size):
        nonlocal changed, observed_descriptor
        content = original_read(descriptor, size)
        observed_descriptor = descriptor
        if content and not changed:
            if not replacement:
                changed = True
                path.write_text('{"owner":22}', encoding="utf-8")
        return content

    def close(descriptor):
        nonlocal changed
        original_close(descriptor)
        if replacement and descriptor == observed_descriptor and not changed:
            changed = True
            alternate = path.with_suffix(".replacement")
            alternate.write_text('{"owner":22}', encoding="utf-8")
            os.replace(alternate, path)

    monkeypatch.setattr(module.os, "read", read)
    monkeypatch.setattr(module.os, "close", close)
    with pytest.raises(module._FileIdentityChanged, match="changed while it was being read"):
        module.read_json(path)


def test_lock_contender_retries_a_witnessed_owner_handoff(module, tmp_path, monkeypatch):
    path = tmp_path / "lock"
    arguments = {"kind": "genesis", "marketplace_id": "example", "timeout_seconds": 2}
    first, second, third = [module._DirectoryLock(path, **arguments) for _ in range(3)]
    first.acquire()
    read_json = module.read_json
    step = 0

    def changing_owner(requested):
        nonlocal step
        if requested != first.owner_path or step < 0:
            return read_json(requested)
        if step == 0:
            step = -1
            first.release()
            third.acquire()
            step = 1
            raise module._FileIdentityChanged("JSON document changed while it was being read.")
        if step == 1:
            step = -1
            record = read_json(requested)
            third.release()
            step = 2
            return record
        return read_json(requested)

    monkeypatch.setattr(module, "read_json", changing_owner)
    try:
        second.acquire()
        assert step == 2 and second.acquired and not first.acquired and not third.acquired
    finally:
        for owner in (first, second, third):
            if owner.acquired:
                owner.release()


def test_changing_owner_retries_remain_bounded_without_releasing_the_owner(
    module, tmp_path, monkeypatch,
):
    arguments = {"kind": "genesis", "marketplace_id": "example"}
    owner = module._DirectoryLock(tmp_path / "lock", **arguments)
    contender = module._DirectoryLock(tmp_path / "lock", **arguments, timeout_seconds=.04)
    owner.acquire()
    before = owner.owner_path.read_bytes()
    moment = 0

    def now():
        nonlocal moment
        moment += .01
        return moment

    def changing(_path):
        raise module._FileIdentityChanged("owner changed")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module.time, "monotonic", now)
            patch.setattr(module.time, "sleep", lambda _delay: None)
            patch.setattr(module, "read_json", changing)
            with pytest.raises(module.InstallationContextError, match="remained busy"):
                contender.acquire()
        assert owner.acquired and not contender.acquired
        assert owner.owner_path.read_bytes() == before
    finally:
        owner.release()


@pytest.mark.parametrize("replacement", [False, True])
def test_stale_observation_after_clean_release_retries(module, tmp_path, monkeypatch, replacement):
    arguments = {"kind": "genesis", "marketplace_id": "example", "timeout_seconds": 2}
    first, second, third = [
        module._DirectoryLock(tmp_path / "lock", **arguments) for _ in range(3)
    ]
    first.acquire()
    owner_state = module._lock_owner_state
    changed = False

    def released_owner(owner):
        nonlocal changed
        if not changed:
            changed = True
            first.release()
            if replacement:
                third.acquire()
            return "stale"
        if third.acquired:
            third.release()
            return "stale"
        return owner_state(owner)

    monkeypatch.setattr(module, "_lock_owner_state", released_owner)
    try:
        second.acquire()
        assert changed and second.acquired and not first.acquired and not third.acquired
    finally:
        for owner in (first, second, third):
            if owner.acquired:
                owner.release()


@pytest.mark.parametrize("failure", ["malformed", "permission"])
def test_lock_contender_still_refuses_stable_invalid_or_unreadable_owner(
    module, tmp_path, monkeypatch, failure,
):
    arguments = {"kind": "genesis", "marketplace_id": "example", "timeout_seconds": 1}
    owner = module._DirectoryLock(tmp_path / "lock", **arguments)
    contender = module._DirectoryLock(tmp_path / "lock", **arguments)
    owner.acquire()
    before = owner.owner_path.read_bytes()
    try:
        with monkeypatch.context() as patch:
            if failure == "malformed":
                def read_json(_path):
                    raise module.InstallationContextError("malformed owner")

                patch.setattr(module, "read_json", read_json)
                message = "malformed owner"
            else:
                def read(*_args):
                    raise PermissionError("owner access denied")

                patch.setattr(module.os, "read", read)
                message = "owner access denied"
            with pytest.raises(module.InstallationContextError, match=message) as caught:
                contender.acquire()
            assert not isinstance(caught.value, module._FileIdentityChanged)
        assert owner.acquired and not contender.acquired
        assert owner.owner_path.read_bytes() == before
    finally:
        owner.release()
