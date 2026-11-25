from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_containers import __main__ as cli
from agent_containers import config, lease, resolver, runtime_drivers

_NAME = "example-1"
_INSTANCE = "instance-1"
_RUNTIME = "driver:example/example@0123456789abcdef"
_DATA = b"example\x00\xff\n"


@pytest.mark.parametrize("outcome", [
    "success", "wrong-lease", "wrong-id", "wrong-runtime", "changed-id", "changed-lease",
    "bad-copy",
])
def test_stage_ref_cli(
    outcome: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "input file"
    source.write_bytes(_DATA)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "ensure_state_dir", lambda: None)
    remote: dict[str, bytes] = {}

    def get_lease(name: str) -> SimpleNamespace:
        assert name == _NAME
        generation = 2.0 if outcome == "changed-lease" and remote else 1.0
        holder = "other" if outcome == "wrong-lease" else "task"
        return SimpleNamespace(effort=holder, acquired_at=generation)

    monkeypatch.setattr(lease, "get_lease", get_lease)
    admitted = False

    @contextmanager
    def admission(name: str, *, expected_assignment: dict[str, object]) -> Iterator[None]:
        nonlocal admitted
        assert name == _NAME
        assert expected_assignment == {"kind": "lease", "effort": "task", "acquired_at": 1.0}
        admitted = True
        try:
            yield
        finally:
            admitted = False

    monkeypatch.setattr(lease, "session_admission", admission)
    class Driver:
        name = "example"
        fleet_name = "example"
        spec = SimpleNamespace(name="example", command=["old-driver"], source="example")

        def exec(
            self, name: str, argv: list[str], *, user: str, instance_id: str,
        ) -> runtime_drivers.ExecResult:
            assert admitted and name == _NAME and user == "worker" and instance_id == _INSTANCE
            assert argv[:2] == ["sh", "-c"]
            if "mkdir" in argv[2]:
                assert argv[-1] == "/workspaces/example"
                output = f"/home/worker/{argv[-2]}"
            elif "sha256sum" in argv[2]:
                copied = remote[argv[-2]]
                output = f"{len(copied)} {hashlib.sha256(copied).hexdigest()}"
            else:
                del remote[f"{argv[-1]}/input"]
                output = ""
            return runtime_drivers.ExecResult(0, output + "\n", "")

        def copy_in(self, name: str, snapshot: Path, path: str, *, instance_id: str) -> None:
            assert admitted and name == _NAME and instance_id == _INSTANCE
            assert snapshot != source
            remote[path] = b"\x00" * len(_DATA) if outcome == "bad-copy" else snapshot.read_bytes()

    driver = Driver()
    monkeypatch.setattr(
        resolver, "resolve_live_exec_target",
        lambda name: SimpleNamespace(
            actual_profile=config.TRUSTED_PROFILE, fleet=SimpleNamespace(driver="example"),
            container_id=f"{_NAME}.{_INSTANCE}", user="worker", workspace_folder="/workspaces/example",
        ),
    )

    def owner_of(handle: str) -> runtime_drivers.Route:
        assert handle in {_NAME, f"{_NAME}.{_INSTANCE}"}
        replaced = outcome == "wrong-id" or (outcome == "changed-id" and bool(remote))
        return runtime_drivers.Route(
            driver, _NAME,  # type: ignore[arg-type]
            "replacement" if replaced else _INSTANCE,
        )

    monkeypatch.setattr(runtime_drivers, "owner_of", owner_of)
    runtime = runtime_drivers.runtime_identity(_NAME)
    if outcome == "wrong-runtime":
        driver.spec = SimpleNamespace(name="example", command=["new-driver"], source="example")
        assert runtime_drivers.runtime_identity(_NAME) != runtime
    code = cli.main([
        "stage-ref", _NAME, "--effort", "task", "--expected-instance", _INSTANCE,
        "--expected-runtime", runtime, "--file", str(source), "--json",
    ])
    captured = capsys.readouterr()

    assert not list(tmp_path.glob("stage-ref-*"))
    assert not admitted
    if outcome == "success":
        assert code == 0
        receipt = json.loads(captured.out)
        path, = remote
        assert receipt == {
            "schema_version": 1, "container": _NAME, "instance_id": _INSTANCE,
            "path": path, "size": len(_DATA), "sha256": hashlib.sha256(_DATA).hexdigest(),
        }
        assert remote[path] == _DATA
    else:
        assert code == 1
        assert not captured.out and not remote


@pytest.mark.parametrize("kind", ["directory", "oversize", "symlink"])
def test_stage_ref_rejects_input_before_admission(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from venue_copilot import refs

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "ensure_state_dir", lambda: None)
    monkeypatch.setattr(refs, "MAX_REF_BYTES", 2)

    def no_lease(name: str) -> None:
        pytest.fail("invalid inputs must not attempt admission")

    monkeypatch.setattr(lease, "get_lease", no_lease)
    source = tmp_path / "source"
    if kind == "directory":
        source.mkdir()
    elif kind == "oversize":
        source.write_bytes(b"abc")
    else:
        target = tmp_path / "regular"
        target.write_bytes(b"a")
        try:
            source.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation is not supported on this host")
    assert cli.main([
        "stage-ref", _NAME, "--effort", "task", "--expected-instance", _INSTANCE,
        "--expected-runtime", _RUNTIME, "--file", str(source), "--json",
    ]) == 1
    assert not capsys.readouterr().out
    assert not list(tmp_path.glob("stage-ref-*"))
