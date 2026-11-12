"""Selected-reader CLI controls; all inputs and child processes are fixture-owned."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "skills" / "monitor-agent-fleet"
    / "scripts" / "read_session_guidance.py"
)
SESSION = "00000000-0000-0000-0000-000000000001"
Selected = tuple[Path, Path, Path]


@pytest.fixture
def source() -> ModuleType:
    spec = importlib.util.spec_from_file_location("selected_reader", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def selected(tmp_path: Path) -> Selected:
    root = tmp_path / "session-state"
    session = root / SESSION
    provider = session / "instructions" / "example-plugin"
    provider.mkdir(parents=True)
    (provider / "session-guidance.instructions.md").write_text("small guidance", encoding="utf-8")
    return root, session, provider


def invoke(root: Path, *extra: str) -> tuple[subprocess.CompletedProcess[bytes], dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--session-state-root", str(root),
         "--session-id", SESSION, *extra],
        capture_output=True, timeout=10,
    )
    assert len(result.stdout) <= 32768
    assert result.stderr == b""
    return result, json.loads(result.stdout)


def test_native_cli_selected_scope_and_explicit_maintenance(selected: Selected) -> None:
    root, session, _ = selected
    maintenance = session / "maintenance.json"
    maintenance.write_text('{"notice":"example"}', encoding="utf-8")
    _, listing = invoke(root)
    assert not any("text" in r for r in listing["records"])
    result, report = invoke(
        root, "--guidance", "example-plugin", "--maintenance-file", str(maintenance)
    )
    assert result.returncode == 0
    assert report["coverage"] == "selected-scope"
    bodies = [r for r in report["records"] if r.get("status") == "read"]
    assert [r["text"] for r in bodies] == ["small guidance", '{"notice":"example"}']
    guidance = "instructions/example-plugin/session-guidance.instructions.md"
    assert next(i for i, r in enumerate(report["records"]) if r["path"] == guidance
                and r.get("kind") == "file") < report["records"].index(bodies[0])


def test_oversized_file_is_not_read(
    selected: Selected, source: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, provider = selected
    (provider / source.GUIDANCE).write_bytes(b"x" * (source.MAX_FILE_BYTES + 1))
    monkeypatch.setattr(source.os, "open", lambda *a, **k: pytest.fail("oversized read"))
    args = type("Args", (), dict(
        session_state_root=str(root), session_id=SESSION,
        guidance=["example-plugin"], maintenance_file=None,
    ))()
    report = source.Report()
    source.collect(args, report)
    assert report.bytes_read == 0 and "file-budget" in report.gaps
    result, body = invoke(root, "--guidance", "example-plugin")
    assert result.returncode == 2 and body["coverage"] == "partial"


def test_directory_iterator_is_not_materialized_or_overconsumed(
    selected: Selected, source: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _ = selected
    calls = 0
    real_scandir = source.os.scandir

    class Entries:
        def __enter__(self) -> "Entries":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def __iter__(self) -> "Entries":
            return self

        def __next__(self) -> Any:
            nonlocal calls
            calls += 1
            assert calls <= source.MAX_DIRECTORY_ENTRIES
            return type("Entry", (), {
                "name": f"entry-{calls}", "stat": lambda self, **k: root.stat(),
            })()

    monkeypatch.setattr(source.os, "scandir", lambda path: Entries())
    report = source.Report()
    report.listing(root, "")
    assert calls == source.MAX_DIRECTORY_ENTRIES
    assert "entry-budget" in report.gaps
    monkeypatch.setattr(source.os, "scandir", real_scandir)
    for i in range(80):
        (root / SESSION / f"entry-{i}").touch()
    result, body = invoke(root)
    assert result.returncode == 2 and "entry-budget" in body["gaps"]


def test_depth_and_unselected_content_are_not_walked(selected: Selected) -> None:
    root, _, provider = selected
    deep = provider / "deeper" / "never"
    deep.mkdir(parents=True)
    (deep / "private.txt").write_text("not selected", encoding="utf-8")
    result, body = invoke(root, "--guidance", "example-plugin")
    assert result.returncode == 2 and "depth-budget" in body["gaps"]
    assert b"private.txt" not in result.stdout and b"not selected" not in result.stdout


@pytest.mark.parametrize("target", ["provider", "file", "ancestor"])
def test_links_are_coverage_gaps(selected: Selected, tmp_path: Path, target: str) -> None:
    root, session, provider = selected
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "session-guidance.instructions.md").write_text("not selected", encoding="utf-8")
    try:
        if target == "provider":
            link = session / "instructions" / "linked-plugin"
            link.symlink_to(outside, target_is_directory=True)
            extra = ["--guidance", "linked-plugin"]
        elif target == "file":
            path = provider / "session-guidance.instructions.md"
            path.unlink()
            path.symlink_to(outside / path.name)
            extra = ["--guidance", "example-plugin"]
        else:
            link = tmp_path / "linked-root"
            link.symlink_to(root, target_is_directory=True)
            root = link
            extra = []
    except OSError:
        pytest.skip("native symbolic-link creation unavailable")
    result, body = invoke(root, *extra)
    assert result.returncode == 2 and body["coverage"] == "partial"
    assert b"not selected" not in result.stdout


def test_reparse_directory_is_refused_before_scandir(
    selected: Selected, source: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _ = selected
    real_lstat = Path.lstat

    def reparse(path: Path, *args: Any, **kwargs: Any) -> Any:
        info = real_lstat(path, *args, **kwargs)
        if path == root:
            return type("Info", (), dict(
                st_mode=info.st_mode, st_file_attributes=0x400,
            ))()
        return info

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(source.Refusal, match="link-or-reparse"):
        source.checked_path(str(root))


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
def test_native_windows_junction_is_not_followed(selected: Selected, tmp_path: Path) -> None:
    root, session, _ = selected
    outside = tmp_path / "outside-junction"
    outside.mkdir()
    (outside / "session-guidance.instructions.md").write_text("not selected", encoding="utf-8")
    link = session / "instructions" / "junction-plugin"
    created = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True, timeout=5,
    )
    assert created.returncode == 0
    try:
        result, body = invoke(root, "--guidance", "junction-plugin")
        assert result.returncode == 2 and body["coverage"] == "partial"
        assert b"not selected" not in result.stdout
    finally:
        link.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell consumer")
def test_native_windows_powershell_consumer(selected: Selected) -> None:
    powershell = shutil.which("powershell")
    assert powershell is not None
    quoted = ["'" + str(value).replace("'", "''") + "'" for value in (
        sys.executable, SCRIPT, selected[0], SESSION,
    )]
    command = (
        f"& {quoted[0]} -I {quoted[1]} --session-state-root {quoted[2]} "
        f"--session-id {quoted[3]} --guidance example-plugin; exit $LASTEXITCODE"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")[:200]
    assert len(result.stdout) <= 32768 and result.stderr == b""
    assert json.loads(result.stdout)["coverage"] == "selected-scope"


def test_total_read_and_output_budgets_are_enforced(selected: Selected, source: ModuleType) -> None:
    root, session, provider = selected
    for i in range(4):
        directory = session / "instructions" / f"plugin-{i}"
        directory.mkdir()
        (directory / source.GUIDANCE).write_bytes(b"x" * source.MAX_FILE_BYTES)
    result, body = invoke(root, *sum(
        (["--guidance", f"plugin-{i}"] for i in range(4)), []
    ))
    assert result.returncode == 2 and "total-budget" in body["gaps"]
    assert body["bytes_read"] == source.MAX_TOTAL_BYTES
    (provider / source.GUIDANCE).write_bytes(b"\x01" * source.MAX_FILE_BYTES)
    result, body = invoke(root, "--guidance", "example-plugin")
    assert result.returncode == 2 and "output-budget" in body["gaps"]
    assert not any(r.get("status") == "read" for r in body["records"])


@pytest.mark.parametrize("extra", [
    ["--guidance", "../escape"],
    ["--guidance", "x" * 10000],
    ["--session-id", "../escape"],
    ["--maintenance-file", ""],
    ["--timeout", "nan"],
    ["--timeout", "6"],
    ["--unknown", "private argument"],
])
def test_invalid_cli_is_bounded_and_sanitized(selected: Selected, extra: list[str]) -> None:
    result, body = invoke(selected[0], *extra)
    assert result.returncode == 2 and body["coverage"] == "partial"
    assert b"escape" not in result.stdout and b"private argument" not in result.stdout


@pytest.mark.parametrize("boundary,timeout", [
    ("filesystem", "0.1"), ("stdout", "0.1"), ("filesystem", None),
])
def test_native_deadline_exits_the_only_collector(
    selected: Selected, boundary: str, timeout: str | None,
) -> None:
    root = selected[0]
    code = (
        "import importlib.util,sys,time;"
        "s=importlib.util.spec_from_file_location('reader',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
    )
    if boundary == "filesystem":
        code += "m.os.scandir=lambda *a:time.sleep(30);"
    else:
        code += "sys.stdout=type('Blocked',(),{'write':lambda *a:time.sleep(30)})();"
    code += "raise SystemExit(m.main(sys.argv[2:]))"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(SCRIPT),
         "--session-state-root", str(root), "--session-id", SESSION,
         *(["--timeout", timeout] if timeout else [])],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 124
    assert result.stdout == b"" and result.stderr == b""


def test_global_entry_and_directory_budgets(selected: Selected, source: ModuleType) -> None:
    root = selected[0]
    report = source.Report()
    for _ in range(40):
        report.listing(root, "")
    assert report.entries <= source.MAX_ENTRIES
    assert "directory-budget" in report.gaps


def test_invalid_encoding_and_nonregular_inputs(selected: Selected) -> None:
    root, _, provider = selected
    path = provider / "session-guidance.instructions.md"
    path.write_bytes(b"\xff")
    result, report = invoke(root, "--guidance", "example-plugin")
    assert result.returncode == 2 and "invalid-utf8" in report["gaps"]
    path.unlink()
    path.mkdir()
    result, report = invoke(root, "--guidance", "example-plugin")
    assert result.returncode == 2 and "guidance-unavailable" in report["gaps"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX special file")
def test_fifo_is_never_opened(selected: Selected) -> None:
    root, _, provider = selected
    path = provider / "session-guidance.instructions.md"
    path.unlink()
    if sys.platform != "win32":
        os.mkfifo(path)
    result, report = invoke(root, "--guidance", "example-plugin")
    assert result.returncode == 2 and "guidance-unavailable" in report["gaps"]
