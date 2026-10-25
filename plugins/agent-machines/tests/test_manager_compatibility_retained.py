"""Fixed-generation dispatch crosses the real canonical retention bootstrap."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from agent_machines import manager_compatibility as compatibility
from agent_machines.fleet_update import CommandResult
from agent_machines.manager_selection import (
    BOOTSTRAP_FILES,
    POSIX_SELECTION_STUB,
    RETAINED_POSIX_SELECTION_STUB,
    RETAINED_WINDOWS_SELECTION_STUB,
    WINDOWS_SELECTION_STUB,
    read_manager_selection,
)

from ._manager_compatibility_helpers import (
    CURRENT_HELP,
    InstalledManager,
    ManagerProtocol,
    default_policy,
    installed_manager,
)


def _site_packages(installation: InstalledManager) -> Path:
    return installation.slot / (
        "Lib/site-packages" if os.name == "nt" else
        f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )


def _install_probe_cli(installation: InstalledManager) -> None:
    site = _site_packages(installation)
    # sitecustomize runs during site.main(), before the requested CLI imports.
    (site / "sitecustomize.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "runtime = sys.modules['_retained_versioned_runtime']\n"
        "slot = Path(sys.prefix)\n"
        "try:\n"
        "    with runtime._kernel_lease(slot.parent.parent, slot.name,\n"
        "                               kind='retention', shared=False, timeout=0):\n"
        "        held = False\n"
        "except TimeoutError:\n"
        "    held = True\n"
        "(slot.parent.parent / 'site-lease-evidence').write_text(str(held))\n",
        encoding="utf-8",
    )
    (site / "worktree_manager" / "__main__.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "from . import __version__\n"
        "slot = Path(sys.prefix)\n"
        "root = slot.parent.parent\n"
        "runtime = sys.modules['_retained_versioned_runtime']\n"
        "try:\n"
        "    with runtime._kernel_lease(root, slot.name, kind='retention',\n"
        "                               shared=False, timeout=0):\n"
        "        held = False\n"
        "except TimeoutError:\n"
        "    held = True\n"
        "assert held, 'CLI imported without canonical retention'\n"
        "assert (root / 'site-lease-evidence').read_text() == 'True'\n"
        "args = sys.argv[1:]\n"
        "with (root / 'dispatch-evidence.jsonl').open('a') as stream:\n"
        "    stream.write(json.dumps({'args': args, 'prefix': str(slot),\n"
        "                             'file': __file__, 'retained': held}) + '\\n')\n"
        "if args == ['--version']:\n"
        "    print('worktree-manager ' + __version__)\n"
        "elif args == ['--help']:\n"
        f"    print({CURRENT_HELP!r}, end='')\n"
        "elif args == ['automatic-updates', 'ownership', '--json']:\n"
        f"    print(json.dumps({{'root': str(root), 'policy': {default_policy()!r}}}))\n"
        "else:\n"
        "    assert args == ['update', '--automatic-trigger', 'legacy-agent-machines']\n"
        "    print('qualified retained generation executed')\n",
        encoding="utf-8",
    )


def _run(argv: list[str], **kwargs: Any) -> CommandResult:
    result = subprocess.run(
        argv, capture_output=True, text=True, **kwargs, **no_window_kwargs(),
    )
    return CommandResult(argv, result.returncode, result.stdout, result.stderr)


def test_recognized_native_stubs_match_current_manager_owned_renderers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    source = Path(__file__).resolve().parents[3] / "worktree-manager/src/worktree_manager"
    package = _site_packages(installation) / "worktree_manager"
    # A private installed fixture runs the owner's stdlib-only renderers in its
    # own retained interpreter; AM never imports Manager into its environment.
    shutil.copytree(
        source, package, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (package / "__main__.py").write_text(
        "import json\n"
        "from .runtime_launchers import _sh_binstub, _cmd_binstub\n"
        "print(json.dumps([_sh_binstub(), _cmd_binstub()]))\n",
        encoding="utf-8",
    )
    result = _run(installation.invocation, timeout=10)
    assert result.returncode == 0, result.output
    assert json.loads(result.stdout) == [
        RETAINED_POSIX_SELECTION_STUB, RETAINED_WINDOWS_SELECTION_STUB,
    ]


def test_current_manager_global_help_is_a_guarded_readonly_protocol() -> None:
    source = Path(__file__).resolve().parents[3] / "worktree-manager/src/worktree_manager/__main__.py"
    syntax = ast.parse(source.read_text(encoding="utf-8"))
    help_branches = []
    for node in ast.walk(syntax):
        if not isinstance(node, ast.If):
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
            and isinstance(first.value.func, ast.Name) and first.value.func.id == "print"
            and first.value.args and isinstance(first.value.args[0], ast.Constant)
            and first.value.args[0].value == "usage: worktree-manager [--version] [--help] <command>"
        ):
            help_branches.append(node)
    assert len(help_branches) == 1
    lines = []
    for statement in help_branches[0].body:
        if isinstance(statement, ast.Return):
            continue
        assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
        call = statement.value
        assert isinstance(call.func, ast.Name) and call.func.id == "print"
        lines.append(ast.literal_eval(call.args[0]) if call.args else "")
    assert compatibility._help_protocol("\n".join(lines) + "\n") == "guarded"


@pytest.mark.parametrize("help_text", [
    CURRENT_HELP + "  update                 duplicate\n",
    CURRENT_HELP.replace(
        "  update --request-status ID [--json]", "  update --unknown ID [--json]",
    ),
    CURRENT_HELP.replace(
        "  update [--wait|--detach|--supervised-debug]  submit and observe a durable suite update\n", "",
    ).replace(
        "  update --recovery-status|--recover-after-restart [--json]  inspect/settle interrupted work\n", "",
    ),
])
def test_update_help_rows_do_not_admit_duplicate_or_status_only_protocols(help_text: str) -> None:
    with pytest.raises(compatibility.ManagerCapabilityRefused, match="update"):
        compatibility._help_protocol(help_text)


@pytest.mark.parametrize("change", ["marker", "stub", "both"])
def test_guarded_dispatch_pins_qualified_generation_across_final_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    _install_probe_cli(installation)
    legacy = installation.root / "versions" / "0.1.0"
    legacy.mkdir()
    (legacy / ".install-complete").touch()
    (legacy / "legacy-sentinel").write_text("must not execute", encoding="utf-8")
    calls: list[list[str]] = []

    def rollback_at_dispatch(argv: list[str], **kwargs: Any) -> CommandResult:
        args = installation.arguments(argv)
        calls.append(args)
        if args[0] == "update":
            if change in {"marker", "both"}:
                (installation.root / "current-version").write_text("0.1.0\n", encoding="utf-8")
            if change in {"stub", "both"}:
                installation.command.write_text("foreign rolled-back stub", encoding="utf-8")
        return _run(argv, **kwargs)

    result = compatibility.run_compatible_manager_update(
        runner=rollback_at_dispatch, home=installation.home,
    )
    assert result.status == "ok", result.detail
    assert result.mode == "guarded"
    assert result.command == [
        *installation.invocation, "update", "--automatic-trigger", "legacy-agent-machines",
    ]
    assert calls == [
        ["--version"], ["--help"], ["automatic-updates", "ownership", "--json"],
        ["update", "--automatic-trigger", "legacy-agent-machines"],
    ]
    evidence = [
        json.loads(line)
        for line in (installation.root / "dispatch-evidence.jsonl").read_text().splitlines()
    ]
    assert len(evidence) == 4
    assert all(item["retained"] for item in evidence)
    assert all(Path(item["prefix"]) == installation.slot for item in evidence)
    assert all(Path(item["file"]).is_relative_to(installation.slot) for item in evidence)
    # The canonical lease must be free after the actual retained child exits.
    release = _run([
        installation.invocation[0], "-I", "-S", "-B", "-c",
        "import importlib.util,sys; from pathlib import Path; "
        "p=Path(sys.argv[1]); "
        "s=importlib.util.spec_from_file_location('fixture_runtime',p/'versioned_runtime.py'); "
        "m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); "
        "lease=m._kernel_lease(Path(sys.argv[2]),sys.argv[3],kind='retention',shared=False,timeout=0); "
        "lease.__enter__(); lease.__exit__(None,None,None); print('released')",
        str(installation.slot / ".runtime-bootstrap"), str(installation.root),
        installation.slot.name,
    ], timeout=10)
    assert release.returncode == 0, release.output
    assert release.stdout.strip() == "released"


@pytest.mark.parametrize("missing", [".install-complete.json", ".runtime-bootstrap/runtime_launcher.py"])
def test_generation_removed_after_qualification_refuses_without_legacy_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    _install_probe_cli(installation)
    calls: list[list[str]] = []

    def remove_at_dispatch(argv: list[str], **kwargs: Any) -> CommandResult:
        args = installation.arguments(argv)
        calls.append(args)
        if args[0] == "update":
            (installation.slot / missing).unlink()
            (installation.root / "current-version").write_text("0.1.0", encoding="utf-8")
        return _run(argv, **kwargs)

    result = compatibility.run_compatible_manager_update(
        runner=remove_at_dispatch, home=installation.home,
    )
    assert result.status == "error"
    assert result.mode == "guarded"
    assert len(calls) == 4
    assert calls[-1] == ["update", "--automatic-trigger", "legacy-agent-machines"]
    evidence = (installation.root / "dispatch-evidence.jsonl").read_text().splitlines()
    assert len(evidence) == 3


@pytest.mark.parametrize("during", ["--version", "--help", "ownership"])
def test_guarded_selector_change_during_readonly_probe_defers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)

    def change(args: list[str]) -> None:
        if during in args:
            (installation.root / "current-version").write_text("0.1.0", encoding="utf-8")

    runner = ManagerProtocol(installation, True, before=change)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "deferred"
    assert runner.updates == []
    assert all(call[0] != str(installation.command) for call in runner.calls)


def test_unretained_guarded_manager_is_not_a_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)
    runner = ManagerProtocol(installation, True)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert "immutable retained runtime" in result.detail
    assert runner.updates == []
    assert [installation.arguments(call) for call in runner.calls] == [["--version"], ["--help"]]


@pytest.mark.parametrize(
    "missing", [".install-complete.json", "payload/pyproject.toml", *(
        ".runtime-bootstrap/" + name for name in BOOTSTRAP_FILES
    ), "Scripts/python.exe" if os.name == "nt" else "bin/python"],
)
def test_damaged_retained_selection_is_refused_before_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    (installation.slot / missing).unlink()
    runner = ManagerProtocol(installation, True)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert runner.calls == []


@pytest.mark.parametrize(
    "corrupt",
    [
        "not json", "[]", "{}",
        '{"version":"1.2.3","version":"1.2.3"}',
        json.dumps({"version": "0.1.0", "completed_at": "", "pid": 1, "payload_hash": "a"}),
        json.dumps({"version": "1.2.3", "completed_at": "", "pid": True, "payload_hash": "a"}),
        json.dumps({"version": "1.2.3", "completed_at": "", "pid": 1, "payload_hash": ""}),
        json.dumps({"version": "1.2.3", "completed_at": "", "pid": 1, "payload_hash": "a", "x": 1}),
    ],
)
def test_retained_completion_must_be_attributable_and_unambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    (installation.slot / ".install-complete.json").write_text(corrupt, encoding="utf-8")
    runner = ManagerProtocol(installation, True)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert runner.calls == []


@pytest.mark.parametrize("change", ["legacy-stub", "foreign-stub", "payload-version", "project-version"])
def test_retained_selection_requires_owned_stub_and_consistent_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    if change == "legacy-stub":
        installation.command.write_bytes(
            (WINDOWS_SELECTION_STUB if os.name == "nt" else POSIX_SELECTION_STUB).encode("utf-8")
        )
    elif change == "foreign-stub":
        installation.command.write_text("foreign", encoding="utf-8")
    else:
        target = (
            installation.payload / "pyproject.toml" if change == "project-version" else
            installation.payload / "src/worktree_manager/__init__.py"
        )
        target.write_text(target.read_text().replace("1.2.3", "9.9.9"), encoding="utf-8")
    runner = ManagerProtocol(installation, True)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert runner.calls == []


@pytest.mark.parametrize("generation", [
    "1.2.3+repair." + "a" * 32,
    "1.2.3+source." + "a" * 40 + "." + "b" * 16,
    "1.2.3+source." + "a" * 40 + "." + "b" * 16 + ".repair." + "c" * 32,
])
def test_retained_source_and_repair_generations_keep_package_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generation: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True, generation=generation)
    selection = read_manager_selection(installation.home)
    assert selection.version == "1.2.3"
    assert list(selection.invocation) == installation.invocation


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv interpreter link")
def test_retained_python_may_link_to_base_but_payload_may_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    python = Path(installation.invocation[0])
    python.unlink()
    python.symlink_to(getattr(sys, "_base_executable", sys.executable))
    assert list(read_manager_selection(installation.home).invocation) == installation.invocation
    target = installation.payload / "src/worktree_manager/__init__.py"
    other = target.with_name("foreign.py")
    target.rename(other)
    target.symlink_to(other)
    runner = ManagerProtocol(installation, True)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert runner.calls == []
@pytest.mark.parametrize("repair_row", [
    "",
    "  update --recover-for-repair [--json]  authorize queue release without cleanup proof; no install\n",
])
def test_known_durable_update_help_versions_remain_guarded(repair_row):
    assert compatibility._help_protocol(CURRENT_HELP + repair_row) == "guarded"


def test_unknown_fourth_update_help_entry_is_not_a_legacy_fallback():
    help_text = CURRENT_HELP + "  update --recover-for-repair-now [--json]  unknown operation\n"
    with pytest.raises(compatibility.ManagerCapabilityRefused, match="ambiguous"):
        compatibility._help_protocol(help_text)


def test_repair_only_help_is_not_a_general_legacy_update():
    help_text = "\n".join(
        line for line in CURRENT_HELP.splitlines() if not line.startswith("  update ")
    ) + "\n  update --recover-for-repair [--json]  no install\n"
    with pytest.raises(compatibility.ManagerCapabilityRefused, match="ambiguous"):
        compatibility._help_protocol(help_text)
