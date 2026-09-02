"""Tests for the bounded aggregate-mode agent-worktrees producer."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "emit_session_context.py"
SPEC = importlib.util.spec_from_file_location("emit_session_context", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_cold_start_budgets_fit_declared_engine_timeout() -> None:
    declaration = json.loads(
        (PLUGIN / "session-context.json").read_text(encoding="utf-8")
    )
    contributor = next(
        item
        for item in declaration["contributors"]
        if item["id"] == "aggregate-context"
    )
    engine_timeout = contributor["timeoutSeconds"]

    assert MODULE.DEADLINE_SECONDS <= engine_timeout
    assert MODULE.SNAPSHOT_WAIT_SECONDS < MODULE.DEADLINE_SECONDS
    assert MODULE.MACHINE_TIMEOUT_SECONDS >= 8
    assert MODULE.CONDUCT_TIMEOUT_SECONDS >= 8
    assert MODULE.SNAPSHOT_TIMESTAMP_SKEW_MS <= 5_000


def test_snapshot_reader_loads_windows_and_posix_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {
            "sessionId": "session-1",
            "cwd": str(tmp_path),
            "source": "startup",
            "timestamp": 123456789,
        }
    ).encode()
    launch_key = MODULE._launch_keys(payload, "1.2.3")[0]
    root = tmp_path / "snapshots"
    root.mkdir()
    monkeypatch.setattr(MODULE, "_snapshot_root", lambda: root)
    (root / f"marketplace-overrides-{launch_key}.json").write_text(
        json.dumps(
            {
                "launchKey": launch_key,
                "output": json.dumps({"additionalContext": "marketplace"}),
            }
        ),
        encoding="utf-8-sig",
    )
    (root / f"register-session-{launch_key}").write_text(
        launch_key + "\n" + json.dumps({"additionalContext": "binding"}),
        encoding="utf-8",
    )
    (root / f"register-nudge-{launch_key}.json").write_text(
        json.dumps({"launchKey": launch_key, "output": "{}"}),
        encoding="utf-8",
    )

    snapshots = MODULE._await_snapshots(
        payload,
        "1.2.3",
        deadline=time.monotonic() + 1,
    )

    assert snapshots == {
        "marketplace-overrides": "marketplace",
        "register-session": "binding",
        "register-nudge": "",
    }


def test_snapshot_reader_accepts_nearby_sibling_hook_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {
            "sessionId": "session-sibling-hook",
            "cwd": str(tmp_path),
            "source": "new",
            "timestamp": 10_000,
        }
    ).encode()
    sibling_payload = json.dumps(
        {
            "sessionId": "session-sibling-hook",
            "cwd": str(tmp_path),
            "source": "new",
            "timestamp": 12_500,
        }
    ).encode()
    sibling_key = MODULE._launch_keys(sibling_payload, "1.2.3")[0]
    root = tmp_path / "snapshots"
    root.mkdir()
    monkeypatch.setattr(MODULE, "_snapshot_root", lambda: root)
    (root / f"register-session-{sibling_key}.json").write_text(
        json.dumps(
            {
                "launchKey": sibling_key,
                "output": json.dumps({"additionalContext": "binding"}),
            }
        ),
        encoding="utf-8",
    )

    snapshots = MODULE._await_snapshots(
        payload,
        "1.2.3",
        deadline=time.monotonic() + 1,
    )

    assert snapshots["register-session"] == "binding"
    assert (root / f"register-session-{sibling_key}.json").exists()
    assert MODULE._read_snapshot(
        "register-session",
        (sibling_key,),
    ) == "binding"


def test_snapshot_reader_accepts_early_coarsely_timed_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_seconds = time.time()
    payload = json.dumps(
        {
            "sessionId": "session-early-sibling",
            "cwd": str(tmp_path),
            "source": "new",
            "timestamp": int(launch_seconds * 1000),
        }
    ).encode()
    launch_key = MODULE._launch_keys(payload, "1.2.3")[0]
    root = tmp_path / "snapshots"
    root.mkdir()
    monkeypatch.setattr(MODULE, "_snapshot_root", lambda: root)
    snapshot = root / f"register-session-{launch_key}.json"
    snapshot.write_text(
        json.dumps(
            {
                "launchKey": launch_key,
                "output": json.dumps({"additionalContext": "binding"}),
            }
        ),
        encoding="utf-8",
    )
    coarse_mtime = int(
        launch_seconds
        - (MODULE.SNAPSHOT_TIMESTAMP_SKEW_MS / 1000)
        - 1
    )
    os.utime(snapshot, (coarse_mtime, coarse_mtime))

    snapshots = MODULE._await_snapshots(
        payload,
        "1.2.3",
        deadline=time.monotonic() + 1,
    )

    assert snapshots["register-session"] == "binding"


def test_binding_snapshot_fallback_matches_fresh_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_seconds = time.time()
    payload = json.dumps(
        {
            "sessionId": "session-binding-fallback",
            "cwd": str(tmp_path),
            "source": "new",
            "timestamp": int(launch_seconds * 1000),
        }
    ).encode()
    root = tmp_path / "snapshots"
    root.mkdir()
    monkeypatch.setattr(MODULE, "_snapshot_root", lambda: root)
    binding = (
        "## agent-worktrees session command catalog\n\n"
        '{"schema":"catalog"}\n\n'
        "[agent-worktrees] This Copilot session is bound.\n"
        f"Checkout: repo=example; id=wt; role=harness; kind=worktree; "
        f"status=active; writable=true; path={tmp_path}.\n"
        "State: status=ready."
    )
    (root / "register-session-unrelated-key.json").write_text(
        json.dumps(
            {
                "launchKey": "unrelated-key",
                "output": json.dumps({"additionalContext": binding}),
            }
        ),
        encoding="utf-8",
    )

    snapshots = MODULE._await_snapshots(
        payload,
        "1.2.3",
        deadline=time.monotonic() + 1,
    )

    assert snapshots["register-session"] == binding


def test_binding_snapshot_fallback_rejects_future_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_seconds = time.time()
    payload = json.dumps(
        {
            "sessionId": "session-future-binding",
            "cwd": str(tmp_path),
            "source": "new",
            "timestamp": int(launch_seconds * 1000),
        }
    ).encode()
    root = tmp_path / "snapshots"
    root.mkdir()
    monkeypatch.setattr(MODULE, "_snapshot_root", lambda: root)
    binding = (
        "## agent-worktrees session command catalog\n\n"
        "[agent-worktrees] This Copilot session is bound.\n"
        f"Checkout: repo=example; id=wt; role=harness; kind=worktree; "
        f"status=active; writable=true; path={tmp_path}."
    )
    snapshot = root / "register-session-future.json"
    snapshot.write_text(
        json.dumps(
            {
                "launchKey": "future",
                "output": json.dumps({"additionalContext": binding}),
            }
        ),
        encoding="utf-8",
    )
    future = launch_seconds + MODULE.SNAPSHOT_COMPLETION_WINDOW_SECONDS + 10
    os.utime(snapshot, (future, future))

    snapshots = MODULE._await_snapshots(
        payload,
        "1.2.3",
        deadline=time.monotonic() + 0.1,
    )

    assert snapshots["register-session"] == ""


def test_snapshot_reader_ignores_stale_or_missing_snapshot_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setattr(MODULE, "_snapshot_root", lambda: missing)
    assert MODULE._read_snapshot("register-session", ("key",)) is None

    root = tmp_path / "snapshots"
    root.mkdir()
    monkeypatch.setattr(MODULE, "_snapshot_root", lambda: root)
    snapshot = root / "register-session-key.json"
    snapshot.write_text(
        json.dumps(
            {
                "launchKey": "key",
                "output": json.dumps({"additionalContext": "stale"}),
            }
        ),
        encoding="utf-8",
    )
    os.utime(snapshot, (1, 1))

    assert (
        MODULE._read_snapshot(
            "register-session",
            ("key",),
            min_mtime=time.time() - 1,
        )
        is None
    )
    assert not snapshot.exists()


def test_binding_snapshot_can_use_the_full_collector_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = [0.0]
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: current[0])
    monkeypatch.setattr(
        MODULE.time,
        "sleep",
        lambda seconds: current.__setitem__(0, current[0] + seconds),
    )
    monkeypatch.setattr(
        MODULE,
        "_launch_keys",
        lambda *_args, **_kwargs: ("key",),
    )

    def fake_read(
        name: str,
        _keys: tuple[str, ...],
        **_kwargs: object,
    ) -> str | None:
        if name == "register-session" and current[0] >= 4:
            return "binding"
        return None

    monkeypatch.setattr(MODULE, "_read_snapshot", fake_read)

    snapshots = MODULE._await_snapshots(
        b"{}",
        "1.2.3",
        deadline=8.5,
    )

    assert snapshots["register-session"] == "binding"
    assert snapshots["marketplace-overrides"] == ""
    assert snapshots["register-nudge"] == ""
    assert current[0] >= 4


@pytest.mark.skipif(os.name != "nt", reason="Windows snapshot-key parity")
@pytest.mark.parametrize("timestamp", [123456789, 1e-7, 1.234567890123456])
def test_launch_key_matches_windows_snapshot_consumer(
    tmp_path: Path,
    timestamp: int | float,
) -> None:
    version = json.loads(
        (PLUGIN / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    payload = json.dumps(
        {
            "sessionId": "session-key-parity",
            "cwd": str(tmp_path),
            "source": "startup",
            "timestamp": timestamp,
        }
    )
    launch_key = MODULE._launch_keys(payload.encode(), version)[0]
    root = tmp_path / ".agent-worktrees" / ".session-context"
    root.mkdir(parents=True)
    expected = json.dumps({"additionalContext": "binding"})
    (root / f"register-session-{launch_key}.json").write_text(
        json.dumps({"launchKey": launch_key, "output": expected}),
        encoding="utf-8-sig",
    )
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert powershell is not None

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "register-session.ps1"),
            "--context-only",
        ],
        input=payload,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "COPILOT_PLUGIN_ROOT": str(PLUGIN),
        },
        check=True,
    )

    assert json.loads(result.stdout)["additionalContext"] == "binding"


def test_float_launch_keys_are_injective() -> None:
    first = json.dumps(
        {
            "sessionId": "session-float-key",
            "cwd": str(PLUGIN),
            "source": "startup",
            "timestamp": 1.234567890123456,
        }
    ).encode()
    second = json.dumps(
        {
            "sessionId": "session-float-key",
            "cwd": str(PLUGIN),
            "source": "startup",
            "timestamp": 1.234567890123457,
        }
    ).encode()

    assert set(MODULE._launch_keys(first, "1.2.3")).isdisjoint(
        MODULE._launch_keys(second, "1.2.3")
    )


def test_side_effect_launch_key_contract_is_synchronized() -> None:
    for name in ("register-session", "register-nudge", "marketplace-overrides"):
        bash = (PLUGIN / "scripts" / f"{name}.sh").read_text(encoding="utf-8")
        powershell = (PLUGIN / "scripts" / f"{name}.ps1").read_text(
            encoding="utf-8"
        )
        assert '"f64:" + struct.pack(">d", timestamp).hex()' in bash
        assert "[BitConverter]::DoubleToInt64Bits($Value)" in powershell
        assert "'f64:' + $Bits.ToString(" in powershell


def test_side_effect_hooks_prefer_payload_scripts() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    commands = hooks["hooks"]["sessionStart"]

    for name in ("register-session", "register-nudge", "marketplace-overrides"):
        hook = next(
            item
            for item in commands
            if f"{name}.ps1" in item.get("powershell", "")
        )
        assert f"scripts\\{name}.ps1" in hook["powershell"]
        assert f"scripts/{name}.sh" in hook["bash"]
        assert f".agent-worktrees\\bin\\{name}.ps1" in hook["powershell"]
        assert f".agent-worktrees/bin/{name}.sh" in hook["bash"]


@pytest.mark.skipif(os.name != "nt", reason="Windows path-case parity")
def test_launch_key_candidates_cover_windows_input_casing(tmp_path: Path) -> None:
    version = json.loads(
        (PLUGIN / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    mixed_case_cwd = str(tmp_path).swapcase()
    payload = json.dumps(
        {
            "sessionId": "session-case-parity",
            "cwd": mixed_case_cwd,
            "source": "startup",
            "timestamp": 123456789,
        }
    )
    launch_keys = MODULE._launch_keys(payload.encode(), version)
    root = tmp_path / ".agent-worktrees" / ".session-context"
    root.mkdir(parents=True)
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert powershell is not None

    matched = False
    for launch_key in launch_keys:
        snapshot = root / f"register-session-{launch_key}.json"
        snapshot.write_text(
            json.dumps(
                {
                    "launchKey": launch_key,
                    "output": json.dumps({"additionalContext": "binding"}),
                }
            ),
            encoding="utf-8-sig",
        )
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-File",
                str(PLUGIN / "scripts" / "register-session.ps1"),
                "--context-only",
            ],
            input=payload,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "USERPROFILE": str(tmp_path),
                "COPILOT_PLUGIN_ROOT": str(PLUGIN),
            },
            check=True,
        )
        snapshot.unlink()
        if json.loads(result.stdout).get("additionalContext") == "binding":
            matched = True
            break

    assert matched


def test_collect_fragments_spawns_only_substantive_runtime_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...], float]] = []
    monkeypatch.setattr(
        MODULE,
        "_await_snapshots",
        lambda *_args, **_kwargs: {
            "marketplace-overrides": "marketplace",
            "register-session": "binding",
            "register-nudge": "nudge",
        },
    )

    def fake_run(
        script: Path,
        _payload: bytes,
        *args: str,
        deadline: float,
        timeout: float,
    ) -> str:
        assert deadline > time.monotonic()
        calls.append((script.name, args, timeout))
        return script.name

    monkeypatch.setattr(MODULE, "_run", fake_run)

    fragments = MODULE._collect_fragments(tmp_path, b"{}", "1.2.3")

    assert sorted(calls) == [
        ("session-conduct", ("--aggregate",), MODULE.CONDUCT_TIMEOUT_SECONDS),
        ("session-machine", (), MODULE.MACHINE_TIMEOUT_SECONDS),
    ]
    assert fragments == {
        "marketplace": "marketplace",
        "binding": "binding",
        "machine": "session-machine",
        "conduct": "session-conduct",
        "nudge": "nudge",
    }


def test_compose_prioritizes_binding_and_conduct_within_budget() -> None:
    fragments = {
        "marketplace": "marketplace-" + ("x" * 500),
        "binding": "[agent-worktrees] This Copilot session is bound.",
        "machine": "Machine: Example\nProject: example",
        "conduct": (
            "Agent-worktrees owns this session's worktree binding. "
            "`agent-worktrees status` is authoritative. "
            "Load `agent-worktrees:worktree` for details."
        ),
        "nudge": "nudge-" + ("x" * 500),
    }

    rendered = MODULE._compose("1.2.3", fragments)
    payload = json.loads(rendered)
    context = payload["additionalContext"]

    assert context.startswith("[owner: agent-worktrees@1.2.3]")
    assert fragments["binding"] in context
    assert fragments["conduct"] in context
    assert len(rendered.encode("utf-8")) <= MODULE.MAX_CONTEXT_BYTES


@pytest.mark.parametrize("segments", [80, 400])
def test_compose_bounds_final_json_with_deep_windows_paths(
    segments: int,
) -> None:
    windows_path = "C:\\" + "\\".join(
        f"segment-{index:03d}" for index in range(segments)
    )
    fragments = {
        "binding": (
            "[agent-worktrees] This Copilot session is bound to "
            f"{windows_path}."
        ),
        "conduct": "Use exact paths and preserve \"quoted\" context.",
    }

    rendered = MODULE._compose("1.2.3", fragments)
    payload = json.loads(rendered)

    assert len(rendered.encode("utf-8")) <= MODULE.MAX_CONTEXT_BYTES
    assert payload["additionalContext"].startswith(
        "[owner: agent-worktrees@1.2.3]"
    )


def test_compactors_preserve_binding_and_machine_identity() -> None:
    binding = (
        "## command catalog\n\n"
        "[agent-worktrees] This Copilot session reports mux pane %7 and is "
        "bound to worktree wt-example; run task commands from /repo."
    )
    assert MODULE._compact_binding(binding).startswith(
        "[agent-worktrees] This Copilot session"
    )

    machine = MODULE._compact_machine(
        "Machine: Example\nHostname: host\nDescription: long\n"
        "Capabilities: many\nProject: repo\nBinstub: repo"
    )
    assert machine.splitlines() == [
        "Machine: Example",
        "Hostname: host",
        "Project: repo",
        "Binstub: repo",
    ]


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None or shutil.which("pwsh") is None,
    reason="Bash/PowerShell parity requires both shells",
)
def test_payload_wrappers_emit_identical_bounded_context(tmp_path: Path) -> None:
    plugin = tmp_path / "agent-worktrees"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    shutil.copy2(
        PLUGIN / "scripts" / "emit-session-context.sh",
        scripts / "emit-session-context.sh",
    )
    shutil.copy2(
        PLUGIN / "scripts" / "emit-session-context.ps1",
        scripts / "emit-session-context.ps1",
    )
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.3"}),
        encoding="utf-8",
    )
    for name in (
        "marketplace-overrides",
        "register-session",
        "session-machine",
        "session-conduct",
        "register-nudge",
    ):
        (scripts / f"{name}.sh").write_text(
            "#!/usr/bin/env bash\nprintf '{}'\n",
            encoding="utf-8",
        )
        (scripts / f"{name}.ps1").write_text(
            "[Console]::Out.Write('{}')\n",
            encoding="utf-8",
        )
    environment = {**os.environ, "COPILOT_PLUGIN_ROOT": str(plugin)}
    payload = '{"sessionId":"session-1","cwd":"/repo"}'
    bash = subprocess.run(
        ["bash", str(scripts / "emit-session-context.sh")],
        input=payload,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    powershell = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(scripts / "emit-session-context.ps1"),
        ],
        input=payload,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert powershell.stdout == bash.stdout
    context = json.loads(bash.stdout)["additionalContext"]
    assert context == "[owner: agent-worktrees@1.2.3]"
    assert len(bash.stdout.encode("utf-8")) <= MODULE.MAX_CONTEXT_BYTES

    py_only = tmp_path / "py-only"
    py_only.mkdir()
    (py_only / "py").symlink_to(sys.executable)
    powershell_command = shutil.which("pwsh")
    assert powershell_command is not None
    py_result = subprocess.run(
        [
            powershell_command,
            "-NoProfile",
            "-File",
            str(scripts / "emit-session-context.ps1"),
        ],
        input=payload,
        env={
            **environment,
            "PATH": str(py_only),
            "HOME": str(tmp_path / "home"),
            "USERPROFILE": str(tmp_path / "home"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(py_result.stdout)["additionalContext"] == (
        "[owner: agent-worktrees@1.2.3]"
    )
