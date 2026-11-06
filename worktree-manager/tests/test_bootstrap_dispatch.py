"""The Windows bootstrap remains present until its selected Manager has finished."""

from __future__ import annotations

from contextlib import nullcontext
import json
from itertools import product
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import self_install


def test_posix_bootstrap_retains_process_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = self_install._runtime_installer()
    seen = []

    class Replaced(BaseException):
        pass

    def replace(executable: str, argv: list[str]) -> None:
        seen.append((executable, argv))
        raise Replaced

    monkeypatch.setattr(installer, "os", SimpleNamespace(name="posix", execv=replace))
    command = ["/selected/python", "-I", "-S", "launcher.py", "-m", "worktree_manager", "update"]
    with pytest.raises(Replaced):
        installer._dispatch_manager(command)
    assert seen == [(command[0], command)]


def test_posix_update_participant_waits_until_its_borrowed_manager_exits(monkeypatch):
    installer = self_install._runtime_installer()
    seen = []

    def launch(command, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(wait=lambda: 23)

    monkeypatch.setattr(installer, "os", SimpleNamespace(name="posix", environ={"fixture": "value"}))
    monkeypatch.setattr(installer.subprocess, "Popen", launch)
    assert installer._dispatch_manager(
        ["/selected/python", "-m", "manager"], handoff={"custody": "witness"},
        cwd="/saved/submission",
    ) == 23
    assert seen[0]["env"] == {"fixture": "value", "custody": "witness"}
    assert seen[0]["cwd"] == "/saved/submission"
    assert "creationflags" not in seen[0]


def test_posix_owned_inspection_waits_without_forwarding_a_witness(monkeypatch):
    installer = self_install._runtime_installer()
    seen = []
    monkeypatch.setattr(installer, "os", SimpleNamespace(name="posix", environ={}))
    monkeypatch.setattr(
        installer.subprocess, "Popen",
        lambda command, **kwargs: seen.append(kwargs) or SimpleNamespace(wait=lambda: 0),
    )
    assert installer._dispatch_manager(
        ["/selected/python", "-m", "manager", "update", "--auth-report"], wait_for_exit=True,
    ) == 0
    assert "env" not in seen[0] and "creationflags" not in seen[0]


@pytest.mark.parametrize("prior,relative,remove_at_spawn", [
    (False, True, False), (False, True, True), (True, False, False),
])
def test_finish_survives_removed_source_anchor(
    tmp_path, monkeypatch, prior, relative, remove_at_spawn,
):
    from worktree_manager import source_config, update_driver_cli

    installer = self_install._runtime_installer()
    root = tmp_path / "manager"
    slot = root / "versions" / "fixture"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    anchor = tmp_path / "submission"
    anchor.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    repo = "../source" if relative else str(source)
    (root / "config.toml").write_text(
        "[source]\nrepo=" + json.dumps(repo) + "\n", encoding="utf-8",
    )
    module = (
        Path(__file__).parent / "fixtures" / "bootstrap-prior-cli" / "source_config.py"
        if prior else Path(source_config.__file__)
    )
    output = root / "observed.json"
    (bootstrap / "runtime_launcher.py").write_text(
        "import json,os,runpy\nfrom pathlib import Path\n"
        f"source=runpy.run_path({str(module)!r})\n"
        "repo=source['absolute_local_source'](source['resolved_repo']())\n"
        f"Path({str(output)!r}).write_text(json.dumps({{"
        "'cwd':str(Path.cwd()),'repo':repo,"
        f"'anchor':os.environ[{source_config.SOURCE_ANCHOR_ENV!r}]}}))\n"
        "raise SystemExit(23)\n", encoding="utf-8",
    )
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setenv(source_config.SOURCE_ANCHOR_ENV, str(anchor))
    monkeypatch.setattr(installer.runtime, "slot_python", lambda *args: Path(sys.executable))
    monkeypatch.setattr(installer.runtime, "retention_lease", lambda *args: nullcontext())
    monkeypatch.setattr(installer, "sys", SimpleNamespace(stdin=None, stdout=None, stderr=None))
    monkeypatch.setattr(
        update_driver_cli, "handoff_environment",
        lambda command: {source_config.SOURCE_ANCHOR_ENV: str(anchor)},
    )
    popen = installer.subprocess.Popen
    launches = []

    def launch(command, **options):
        launches.append(options["cwd"])
        if remove_at_spawn and len(launches) == 1:
            anchor.rmdir()
        return popen(command, **options)

    monkeypatch.setattr(installer.subprocess, "Popen", launch)
    if not remove_at_spawn:
        anchor.rmdir()
    args = SimpleNamespace(
        install_only=False, repair_update=False, update_participant=True, root=root,
    )
    assert installer._finish(args, slot, ["update"]) == 23
    assert json.loads(output.read_text()) == {
        "cwd": str(root), "repo": str(source), "anchor": str(anchor),
    }
    assert launches == [str(anchor), str(root)]


def test_dispatch_does_not_retry_a_missing_executable_with_a_live_anchor(tmp_path, monkeypatch):
    installer = self_install._runtime_installer()
    monkeypatch.setattr(installer, "sys", SimpleNamespace(stdin=None, stdout=None, stderr=None))
    launches = []

    def launch(command, **options):
        launches.append(options)
        raise FileNotFoundError("missing interpreter")

    monkeypatch.setattr(installer.subprocess, "Popen", launch)
    with pytest.raises(FileNotFoundError, match="missing interpreter"):
        installer._dispatch_manager(
            ["missing-interpreter"], cwd=str(tmp_path), fallback_cwd=str(tmp_path.parent),
        )
    assert len(launches) == 1


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("console_streams", list(product([False, True], repeat=3)))
def test_windows_bootstrap_waits_for_observer_and_preserves_exit(
    monkeypatch: pytest.MonkeyPatch, interrupted: bool, console_streams: tuple[bool, bool, bool],
) -> None:
    installer = self_install._runtime_installer()
    seen, waits = [], []

    def wait() -> int:
        waits.append(True)
        if interrupted and len(waits) == 1:
            raise KeyboardInterrupt
        return 43

    def launch(argv: list[str], **kwargs: object) -> SimpleNamespace:
        seen.append((argv, kwargs))
        return SimpleNamespace(wait=wait)

    monkeypatch.setattr(installer, "os", SimpleNamespace(name="nt"))
    streams = {name: SimpleNamespace(console=console)
               for name, console in zip(("stdin", "stdout", "stderr"), console_streams, strict=True)}
    monkeypatch.setattr(installer, "sys", SimpleNamespace(**streams))
    monkeypatch.setattr(installer, "_windows_console_stream", lambda stream: stream.console)
    monkeypatch.setattr(
        installer, "subprocess", SimpleNamespace(Popen=launch, CREATE_NO_WINDOW=0x08000000),
    )
    command = ["selected-python.exe", "-I", "-S", "launcher.py", "-m", "worktree_manager", "update"]
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            installer._dispatch_manager(command)
        assert len(waits) == 2
    else:
        assert installer._dispatch_manager(command) == 43
        assert len(waits) == 1
    assert seen == [(command, {
        **streams, "creationflags": 0 if any(console_streams) else 0x08000000,
    })]


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="opt-in native Windows process/window qualification",
)
@pytest.mark.parametrize(("code", "input_text"), [
    (0, "bootstrap input \u00e9"), (43, "bootstrap input \u00e9"), (43, None),
])
def test_native_windowless_bootstrap_waits_for_selected_manager(
    tmp_path: Path, code: int, input_text: str | None,
) -> None:
    from agent_procutil.owned_process import run_owned

    repo = Path(__file__).resolve().parents[2]
    observer = runpy.run_path(str(
        repo / "libs/agent-procutil/tests/test_spawn_daemon_windows_integration.py"
    ))
    parent = getattr(sys, "_base_executable", sys.executable)
    installer = repo / "worktree-manager/scripts/runtime_install.py"
    arguments = ["-m", "worktree_manager", "update", "--project", "example with spaces"]
    if input_text is None:
        with open(os.devnull, encoding="utf-8") as null:
            assert null.isatty()
            assert not self_install._runtime_installer()._windows_console_stream(null)
    with observer["_WindowObserver"](*observer["_configure_win32"]()) as windows:
        for cycle in range(2):
            marker = tmp_path / f"completed-{cycle}.json"
            child = tmp_path / f"selected-manager-{cycle}.py"
            child.write_text(
                "import json,pathlib,sys,time\n"
                "data=sys.stdin.read()\n"
                "print('selected Manager stdout \\u00e9',flush=True)\n"
                "print('selected Manager stderr \\u00e9',file=sys.stderr,flush=True)\n"
                "time.sleep(.2)\n"
                f"pathlib.Path({str(marker)!r}).write_text("
                "json.dumps({'argv':sys.argv[1:],'stdin':data}),encoding='utf-8')\n"
                f"raise SystemExit({code})\n",
                encoding="utf-8",
            )
            command = [parent, "-I", "-S", "-B", "-X", "utf8", str(child), *arguments]
            outer = (
                "import json,runpy,sys;"
                "dispatch=runpy.run_path(sys.argv[1])['_dispatch_manager'];"
                "raise SystemExit(dispatch(json.loads(sys.argv[2])))"
            )
            result = run_owned(
                [parent, "-I", "-S", "-B", "-X", "utf8", "-c", outer,
                 str(installer), json.dumps(command)],
                timeout=15, input_text=input_text,
            )
            assert result.returncode == code, result.stderr
            assert result.stdout == "selected Manager stdout \u00e9\n"
            assert result.stderr == "selected Manager stderr \u00e9\n"
            assert json.loads(marker.read_text(encoding="utf-8")) == {
                "argv": arguments, "stdin": input_text or "",
            }
    assert windows.surfaced == {}
    assert windows.focus_changes == {}
