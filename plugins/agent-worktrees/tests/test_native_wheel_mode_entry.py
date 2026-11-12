"""A retained native-wheel runtime remains a valid dev-mode control owner."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from test_python_runtime_modes import modes as modes


@pytest.mark.parametrize("corrupt", [None, "binding", "version", "extra"])
@pytest.mark.parametrize("loader", ["package", "standalone"])
def test_native_wheel_runtime_can_enter_modes_without_source_receipt_reinterpretation(
    modes, tmp_path, monkeypatch, corrupt, loader,
):
    from agent_worktrees import runtime_candidate, runtime_dev_cli

    if loader == "standalone":
        spec = importlib.util.spec_from_file_location(
            "_retained_origin_health", Path(runtime_candidate.__file__).resolve(),
        )
        runtime_candidate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime_candidate)
    runtime = modes.lifecycle.runtime
    generation = "1.0.0.profile." + "a" * 16
    slot = runtime.slot(tmp_path, generation)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture interpreter")
    runtime.mark_complete(tmp_path, generation, payload_hash="a" * 64)
    (tmp_path / "current-version").write_text(generation)
    binding = {"fixture": "admitted native closure"}
    canonical = json.dumps(binding, sort_keys=True, separators=(",", ":"))
    digest = "b" * 64
    origin = (tmp_path / "wheel.whl").as_uri() + "#sha256=" + digest
    wheels = {
        "construction": "closed-wheels",
        "lock_id": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
        "binding": binding,
        "distributions": {"agent-worktrees": {
            "version": "1.0.0", "filename": "agent_worktrees-1.0.0-py3-none-any.whl",
            "sha256": "sha256:" + digest,
        }},
    }
    if corrupt == "binding":
        wheels["binding"]["fixture"] = "changed"
    (slot / ".runtime-input-origins.json").write_text(json.dumps({
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "packages": {"agent-worktrees": origin}, "wheel_inputs": wheels,
    }), encoding="utf-8")

    class Distribution:
        metadata = {"Name": "agent-worktrees"}
        version = "9.0.0" if corrupt == "version" else "1.0.0"

        def locate_file(self, _name):
            return slot / "site-packages"

        def read_text(self, _name):
            return json.dumps({"url": origin, "archive_info": {"hashes": {"sha256": digest}}})

    installed = [Distribution()]
    if corrupt == "extra":
        installed.append(SimpleNamespace(
            metadata={"Name": "unrecorded"}, version="1", read_text=lambda _name: None,
        ))
    monkeypatch.setattr(runtime_candidate, "distributions", lambda: installed)
    monkeypatch.setattr(runtime_dev_cli, "_lifecycle", lambda: modes.lifecycle)
    monkeypatch.setattr(modes.lifecycle.engine, "_procutil", lambda _inputs: None)
    monkeypatch.setattr(runtime_candidate.sys, "prefix", str(slot))
    verified = []

    def probe(argv, **_options):
        runtime_candidate.verify_installed_origins()
        verified.append(True)
        return subprocess.CompletedProcess(argv, 0, '{"version":1,"compatible":true}', "")

    monkeypatch.setattr(modes.lifecycle.engine, "run_owned", probe)
    with ExitStack() as custody:
        if corrupt is None:
            selected = modes._retained_control(
                tmp_path, None, custody, mode="dev", wait_timeout=1, child_timeout=3,
            )
            assert selected is not None and selected[0] == python and verified == [True]
        else:
            with pytest.raises((RuntimeError, ValueError)):
                modes._retained_control(
                    tmp_path, None, custody, mode="dev", wait_timeout=1, child_timeout=3,
                )
