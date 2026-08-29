"""Windows supervisor service-generation retirement."""

from __future__ import annotations

from agent_dispatch.supervisor_processes import (
    WindowsProcess,
    parse_windows_process_json,
    retire_windows_supervisor_generations,
    select_supervisor_generation_pids,
)


INSTALL_DIR = r"C:\Users\user\.agent-dispatch"


def _proc(pid, parent, executable, command_line):
    return WindowsProcess(
        pid=pid,
        parent_pid=parent,
        executable=executable,
        command_line=command_line,
    )


def test_selects_old_wrapper_master_children_and_descendants_across_slots():
    old_python = INSTALL_DIR + r"\versions\0.1.0-dev1\Scripts\python.exe"
    older_python = INSTALL_DIR + r"\versions\0.1.0-dev0\Scripts\python.exe"
    current_python = INSTALL_DIR + r"\versions\0.1.0-dev2\Scripts\python.exe"
    launcher = INSTALL_DIR + r"\supervise-service.ps1"
    processes = [
        _proc(
            100,
            1,
            r"C:\Windows\System32\conhost.exe",
            rf'conhost.exe --headless powershell.exe -File "{launcher}"',
        ),
        _proc(
            101,
            100,
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            rf'powershell.exe -File "{launcher}"',
        ),
        _proc(
            102,
            101,
            old_python,
            rf'"{old_python}" -m agent_dispatch supervise serve --legacy-env',
        ),
        _proc(
            103,
            102,
            old_python,
            rf'"{old_python}" -m agent_dispatch emitter serve "emitter.json"',
        ),
        _proc(
            104,
            102,
            old_python,
            rf'"{old_python}" -m agent_dispatch supervise --all-repos --label queue',
        ),
        # The old master is already gone, but its autonomous producer still runs
        # from a superseded slot and must be retired.
        _proc(
            200,
            1,
            older_python,
            rf'"{older_python}" -m agent_dispatch schedule serve "schedule.json"',
        ),
        # A standalone producer on the current slot is a supported public surface
        # and must not be killed merely because it uses the installed runtime.
        _proc(
            201,
            1,
            current_python,
            rf'"{current_python}" -m agent_dispatch emitter serve "current.json"',
        ),
        # A registered emitter's in-flight external command is a descendant and
        # must not survive the generation that owned it.
        _proc(105, 103, r"C:\Tools\producer.exe", "producer.exe tick"),
        # Unrelated agent-dispatch processes are not supervisor-owned.
        _proc(
            300,
            1,
            old_python,
            rf'"{old_python}" -m agent_dispatch serve',
        ),
        _proc(
            301,
            1,
            old_python,
            rf'"{old_python}" -m agent_dispatch emitter tick "emitter.json"',
        ),
    ]

    selected = select_supervisor_generation_pids(
        processes, INSTALL_DIR, current_version="0.1.0-dev2"
    )

    assert set(selected) == {100, 101, 102, 103, 104, 105, 200}
    assert selected.index(105) < selected.index(103) < selected.index(102)
    assert 300 not in selected
    assert 301 not in selected
    assert 201 not in selected


def test_parse_windows_process_inventory_accepts_single_or_array_payload():
    single = parse_windows_process_json(
        '{"ProcessId":10,"ParentProcessId":1,'
        '"ExecutablePath":"C:\\\\Python\\\\python.exe","CommandLine":"python -V"}'
    )
    assert single == [
        WindowsProcess(
            pid=10,
            parent_pid=1,
            executable=r"C:\Python\python.exe",
            command_line="python -V",
        )
    ]
    array = parse_windows_process_json(
        '[{"ProcessId":20,"ParentProcessId":2,'
        '"ExecutablePath":"C:\\\\Python\\\\python.exe","CommandLine":"python -V"},'
        '{"ProcessId":21,"ParentProcessId":20,'
        '"ExecutablePath":null,"CommandLine":null}]'
    )
    assert array == [
        WindowsProcess(
            pid=20,
            parent_pid=2,
            executable=r"C:\Python\python.exe",
            command_line="python -V",
        ),
        WindowsProcess(pid=21, parent_pid=20),
    ]


def test_retirement_terminates_every_selected_generation():
    python = INSTALL_DIR + r"\versions\0.1.0-dev1\Scripts\python.exe"
    launcher = INSTALL_DIR + r"\supervise-service.ps1"
    processes = [
        _proc(10, 1, r"C:\Windows\System32\conhost.exe", rf'conhost -File "{launcher}"'),
        _proc(11, 10, python, rf'"{python}" -m agent_dispatch supervise serve'),
        _proc(12, 11, python, rf'"{python}" -m agent_dispatch webhook --config hook.json'),
    ]
    terminated: list[int] = []

    def terminate(pid: int) -> bool:
        terminated.append(pid)
        return True

    result = retire_windows_supervisor_generations(
        INSTALL_DIR,
        list_processes=lambda: processes,
        terminate=terminate,
        platform_name="nt",
        current_version="0.1.0-dev2",
    )

    assert result.ok
    assert result.selected == [12, 11, 10]
    assert result.retired == [12, 11, 10]
    assert terminated == [12, 11, 10]
