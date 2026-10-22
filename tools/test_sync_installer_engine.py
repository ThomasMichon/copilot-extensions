from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "sync-installer-engine.py"
HUMAN_CAPABILITY_PATH = (
    REPO / "libs" / "update-telemetry" / "src" / "update_telemetry" / "capabilities.py"
)

_SPEC = importlib.util.spec_from_file_location("sync_installer_engine", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
sync_installer_engine = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_installer_engine)


def _human_admission():
    spec = importlib.util.spec_from_file_location("native_human_capability", HUMAN_CAPABILITY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.powershell_human_utf8_supported


def test_human_capability_probe_does_not_require_an_installed_runtime():
    program = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("bare_human_capability", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert "agent_worktrees" not in sys.modules
assert "update_telemetry" not in sys.modules
assert module.powershell_human_utf8_supported(pathlib.Path(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program, str(HUMAN_CAPABILITY_PATH),
         str(REPO / "plugins/agent-worktrees/scripts/install.ps1")],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_human_output_declarations_bind_real_packaged_entrypoints():
    contract = sync_installer_engine.ier
    source = REPO / "libs" / "installer-engine" / contract.HUMAN_HELPER
    supports = _human_admission()
    for name in contract.human_output_adopters(REPO, contract.ADOPTERS):
        script = contract.human_output_entrypoint(REPO / "plugins" / name)
        assert (script.parent / contract.HUMAN_HELPER).read_bytes() == source.read_bytes()
        assert supports(script), name
        declaration = json.loads(script.with_suffix(".human-output.json").read_text("utf-8"))
        assert declaration == contract.human_output_declaration(script)


@pytest.mark.parametrize("shell", ["bash", "pwsh", "powershell"])
def test_installer_action_identity_uses_existing_progress_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: str,
) -> None:
    if shell == "bash" and os.name == "nt":
        pytest.skip("exercise POSIX installers on a POSIX host")
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} unavailable")
    powershell = shell != "bash"
    suffix = ".ps1" if powershell else ".sh"
    markers = {"running": "...   ", "done": "[OK]  ", "skipped": "[SKIP]"}
    helpers = (
        {"running": "Write-Step", "done": "Write-Ok", "skipped": "Write-Skip"}
        if powershell else {"running": "_step", "done": "_ok", "skipped": "_skip"}
    )
    fragments = []
    expected = []
    states = []
    for plugin in (
        "agent-dispatch", "agent-vault", "agent-pull-requests", "agent-ssh", "budget-guidance",
        "agent-bridge", "agent-index", "agent-logger",
        "agent-containers", "agent-mcp", "agent-machines",
        "agent-worktrees", "agent-codespaces",
    ):
        initializer = plugin in {"agent-containers", "agent-mcp", "agent-machines"}
        entrypoint = ("init" if initializer else "install") + suffix
        source = (REPO / "plugins" / plugin / "scripts" / entrypoint).read_text(
            encoding="utf-8",
        )
        assert re.search(
            r"""\b(?:Write-Host|echo)\s+(['"])={3,}\s+\S+\s+(?:install|init|stamp|provision)\b""",
            source,
        ) is None, plugin
        source_lines = source.splitlines()
        actions = ["install", "update"] if plugin in {"agent-dispatch", "agent-vault"} \
            else ["install"] if plugin in {
                "agent-pull-requests", "agent-ssh", "budget-guidance",
            } else []
        cases = [(state, f"{plugin} {action}") for action in actions
                 for state in ("running", "done")]
        if powershell or plugin in {
            "agent-bridge", "agent-dispatch", "agent-index", "agent-vault",
            "agent-worktrees", "agent-codespaces",
        }:
            deferred = "explicit setup" if plugin == "agent-index" else "first use"
            cases.append(("running", f"{plugin} stamp; runtime deferred to {deferred}"))
        if initializer:
            cases.append(("running", f"{plugin} init"))
        if powershell and plugin == "agent-bridge":
            cases.append(("running", f"{plugin} provision"))
        if plugin in {"agent-worktrees", "agent-codespaces"}:
            cases.extend(("running", f"{plugin} {action}") for action in ("install", "update"))
            cases.append(("running", f"{plugin} dev; explicitly claimed runtime"))
            if plugin == "agent-worktrees":
                cases.extend(("running", f"{plugin} {detail}") for detail in (
                    "dev-release; restore before releasing claim",
                    "repair; distinct runtime generation",
                    "provision; tools only, no launcher/hooks",
                ))
            else:
                cases.append(("running", f"{plugin} provision"))
        if plugin == "agent-dispatch":
            cases.append(("skipped", "agent-dispatch install (superseded by a newer concurrent build)"))
        for state, message in cases:
            helper = helpers[state]
            declaration = f"function {helper} " if powershell else helper + "()"
            fragments.append(next(
                line for line in source_lines if line.startswith(declaration)
            ))
            invocation = re.search(
                rf"^[ \t]*(?:\w+(?:-\w+)*\)[ \t]*)?"
                rf"(?P<call>{re.escape(helper)}[ \t]+(['\"]){re.escape(message)}\2)"
                rf"[ \t]*(?:;;)?[ \t]*$",
                source, re.M,
            )
            assert invocation is not None, (plugin, state, message)
            index = source_lines.index(invocation[0])
            for neighbor in source_lines[max(0, index - 1):index] + source_lines[index + 1:index + 2]:
                assert neighbor.strip() not in {"Write-Host ''", 'Write-Host ""', "echo ''", 'echo ""'}
            fragments.append(invocation["call"])
            expected.append(f"  {markers[state]} {message}")
            states.append(f"{state} {message}")
    harness = tmp_path / ("identities" + suffix)
    harness.write_text(
        ("$ErrorActionPreference = 'Stop'\n" if powershell else "set -euo pipefail\n")
        + "\n".join(fragments) + "\n", encoding="utf-8",
    )
    argv = (
        [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)]
        if powershell else [executable, str(harness)]
    )
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    standalone = subprocess.run(
        argv, capture_output=True, text=True, timeout=10, check=False, **options,
    )
    assert standalone.returncode == 0, standalone.stderr
    assert standalone.stderr == ""
    assert standalone.stdout.splitlines() == expected

    monkeypatch.syspath_prepend(str(REPO / "libs" / "update-telemetry" / "src"))
    from update_telemetry.human import HumanOperation, native_output

    output = io.StringIO()
    with HumanOperation("installers", stream=output) as operation:
        with native_output() as sink:
            result = subprocess.run(
                argv, stdout=sink, stderr=sink, timeout=10, check=False, **options,
            )
        operation.complete("done" if result.returncode == 0 else "failed", f"exit {result.returncode}")
    assert result.returncode == 0
    lines = output.getvalue().splitlines()
    assert len(lines) == len(states) + 2, lines
    for line, message in zip(lines[1:-1], states, strict=True):
        assert re.fullmatch(
            rf"\[\d{{2}}:\d{{2}}:\d{{2}}\.\d{{3}}Z\] installers "
            rf"{re.escape(message)} \[elapsed \d+\.\d+s\]", line,
        )
    assert "installers done exit 0 [duration " in lines[-1]


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("plugin", ["agent-worktrees", "agent-codespaces"])
def test_construction_action_progress_precedes_native_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: str, plugin: str,
) -> None:
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} unavailable")
    scripts = REPO / "plugins" / plugin / "scripts"
    installer = scripts / "install.ps1"
    source = installer.read_text(encoding="utf-8")
    helper = next(line for line in source.splitlines() if line.startswith("function Write-Step "))
    if plugin == "agent-worktrees":
        utilities = (scripts / "service-utils.ps1").read_text(encoding="utf-8")
        directory_helper = re.search(
            r"^function Ensure-InstallDir \{.*?^\}", utilities, re.M | re.S,
        )
        assert directory_helper is not None
        helper += "\n" + directory_helper[0]
    fixture = tmp_path / "action.ps1"
    fixture.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$source = $args[0]; $Action = $args[1]\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { throw 'Installer parse failed' }\n"
        + helper + "\n"
        "function Resolve-Machine { [Console]::Error.WriteLine('prerequisite refused'); exit 23 }\n"
        "function Deploy-PythonRuntime { [Console]::Error.WriteLine('prerequisite refused'); exit 23 }\n"
        "function Deploy-Venv { [Console]::Error.WriteLine('prerequisite refused'); exit 23 }\n"
        "function git { $global:LASTEXITCODE = 0 }\n"
        "$InstallDir = $PSScriptRoot; $BinDir = $PSScriptRoot; $LocalBin = $PSScriptRoot\n"
        "$ContextualInstall = $true\n"
        "if ($source -like '*agent-codespaces*') {\n"
        "  foreach ($name in @('Invoke-Install', 'Invoke-Update')) {\n"
        "    $node = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $n.Name -eq $name}, $true)\n"
        "    if (-not $node) { throw 'Action function missing' }\n"
        "    . ([scriptblock]::Create($node.Extent.Text))\n"
        "  }\n"
        "  switch ($Action) {\n"
        "    install { Invoke-Install }\n"
        "    update { Invoke-Update }\n"
        "    provision { Invoke-Install -RuntimeOnly }\n"
        "  }\n"
        "} else {\n"
        "  $switches = @($ast.FindAll({param($n) "
        "$n -is [Management.Automation.Language.SwitchStatementAst] "
        "-and $n.Condition.Extent.Text -eq '$Action'}, $true))\n"
        "  $clause = @($switches[-1].Clauses | Where-Object { $_.Item1.Value -eq $Action })\n"
        "  if ($clause.Count -ne 1) { throw 'Action branch missing' }\n"
        "  $body = $clause[0].Item2.Extent.Text\n"
        "  & ([scriptblock]::Create($body.Substring(1, $body.Length - 2)))\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(REPO / "libs" / "update-telemetry" / "src"))
    from update_telemetry.human import HumanOperation, native_output

    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for action in ("install", "update", "provision"):
        argv = [
            executable, "-NoProfile", "-NonInteractive", "-File", str(fixture),
            str(installer), action,
        ]
        output = io.StringIO()
        with HumanOperation("installer", stream=output) as operation:
            with native_output() as sink:
                result = subprocess.run(
                    argv, stdout=sink, stderr=sink, timeout=10, check=False, **options,
                )
            operation.complete("failed", f"exit {result.returncode}")
        assert result.returncode == 23, output.getvalue()
        lines = output.getvalue().splitlines()
        assert len(lines) == 4, lines
        message = f"{plugin} {action}"
        if plugin == "agent-worktrees" and action == "provision":
            message += "; tools only, no launcher/hooks"
        assert re.fullmatch(
            rf"\[\d{{2}}:\d{{2}}:\d{{2}}\.\d{{3}}Z\] installer running "
            rf"{re.escape(message)} \[elapsed \d+\.\d+s\]", lines[1],
        )
        assert "installer prerequisite refused [elapsed " in lines[2]
        assert "installer failed exit 23 [duration " in lines[3]


def _copy_real_installer_engine(root: Path) -> None:
    contract = sync_installer_engine.ier
    canonical = root / "libs" / "installer-engine"
    canonical.mkdir(parents=True)
    origin = REPO / "libs" / "installer-engine"
    for asset in (*contract.FILES, *contract.support_files(origin)):
        (canonical / asset).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin / asset, canonical / asset)


def test_real_engine_fixture_retains_nested_support_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = sync_installer_engine.ier
    origin = tmp_path / "input"
    canonical = origin / "libs" / "installer-engine"
    assets = (*contract.FILES, "nested/support.py")
    for asset in assets:
        path = canonical / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture {asset}\n", encoding="utf-8")
    monkeypatch.setattr(contract, "support_files", lambda _: ("nested/support.py",))
    monkeypatch.setattr(f"{__name__}.REPO", origin)
    destination = tmp_path / "output"
    _copy_real_installer_engine(destination)
    for asset in assets:
        assert (destination / "libs" / "installer-engine" / asset).read_bytes() == (
            canonical / asset
        ).read_bytes()


def _real_human_consumer(root: Path, name: str, layout: str) -> Path:
    import materialize_installer_engine

    contract = sync_installer_engine.ier
    consumer = root / "plugins" / name
    scripts = _mk_plugin_scripts(root, name)
    entrypoint = contract.human_output_entrypoint(consumer)
    for asset in (entrypoint.name, entrypoint.with_suffix(".sh").name, contract.HUMAN_HELPER):
        shutil.copyfile(REPO / "plugins" / name / "scripts" / asset, scripts / asset)
    for ext in ("ps1", "sh"):
        script = entrypoint.with_suffix(f".{ext}")
        ref = contract.find_engine_ref(script, ext)
        if ref is None:
            assert name in contract.HUMAN_STANDALONE_ENTRYPOINTS
            continue
        old_line = (
            contract.local_line(ext) if contract.is_local_ref(ref, consumer)
            else contract.canonical_line(ext)
        )
        text = script.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        assert sum(line.lstrip().startswith(old_line) for line in lines) == 1
        script.write_text("".join(
            line.replace(old_line, contract.canonical_line(ext), 1)
            if line.lstrip().startswith(old_line) else line
            for line in lines
        ), encoding="utf-8")
        assert contract.is_canonical_ref(
            contract.find_engine_ref(script, ext), consumer, repo_root=root,
        )
    script = entrypoint
    script.with_suffix(".human-output.json").write_text(
        contract.human_output_json(script), encoding="utf-8",
    )
    assert layout in {"canonical", "materialized"}
    if layout == "materialized":
        log = materialize_installer_engine.materialize_ref_into(
            source_consumer_dir=consumer, dest_consumer_dir=consumer,
            canonical_root=root, dest_root=root,
        )
        assert not any(line.startswith("SKIP") for line in log), log
        if name in contract.ADOPTERS:
            assert any(line.startswith("OK") for line in log), log
    return consumer


@pytest.mark.parametrize("layout", ["canonical", "materialized"])
def test_materialized_human_declaration_binds_rewritten_entrypoint(
    tmp_path: Path, layout: str,
) -> None:
    import materialize_installer_engine

    contract = sync_installer_engine.ier
    source_root = tmp_path / "source"
    _copy_real_installer_engine(source_root)
    source = _real_human_consumer(source_root, "agent-bridge", layout)
    destination = tmp_path / "payload"
    shutil.copytree(source, destination)
    scripts = destination / "scripts"
    before = (scripts / "install.ps1").read_bytes()
    log = materialize_installer_engine.materialize_ref_into(
        source_consumer_dir=source, dest_consumer_dir=destination,
        canonical_root=source_root, dest_root=tmp_path,
    )
    if layout == "canonical":
        assert any(line.startswith("OK") for line in log), log
        assert (scripts / "install.ps1").read_bytes() != before
    else:
        assert log == []
        assert (scripts / "install.ps1").read_bytes() == before
    for ext in ("ps1", "sh"):
        assert contract.is_local_ref(
            contract.find_engine_ref(scripts / f"install.{ext}", ext), destination,
        )
    canonical = source_root / "libs" / "installer-engine"
    for asset in (*contract.FILES, *contract.support_files(canonical)):
        assert (scripts / asset).read_bytes() == (canonical / asset).read_bytes()
    assert _human_admission()(scripts / "install.ps1")
    snapshot = {path.name: path.read_bytes() for path in scripts.iterdir()}
    assert materialize_installer_engine.materialize_ref_into(
        source_consumer_dir=destination, dest_consumer_dir=destination,
        canonical_root=source_root, dest_root=tmp_path,
    ) == []
    assert {path.name: path.read_bytes() for path in scripts.iterdir()} == snapshot
    with (scripts / contract.HUMAN_HELPER).open("a", encoding="ascii") as stream:
        stream.write("# drift\n")
    assert not _human_admission()(scripts / "install.ps1")
    with pytest.raises(ValueError, match="conflicting native human-output assets"):
        materialize_installer_engine.materialize_ref_into(
            source_consumer_dir=source, dest_consumer_dir=destination,
            canonical_root=source_root, dest_root=tmp_path,
        )


@pytest.mark.parametrize("in_place", [False, True])
@pytest.mark.parametrize("layout", ["canonical", "materialized"])
def test_whole_payload_human_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, in_place: bool, layout: str,
) -> None:
    import materialize_main

    contract = sync_installer_engine.ier
    names = contract.human_output_adopters(REPO, contract.ADOPTERS)
    monkeypatch.chdir(tmp_path)
    source_root = Path("source")
    destination = Path("payload")
    _copy_real_installer_engine(source_root)
    for name in names:
        source = REPO / "plugins" / name
        consumer = _real_human_consumer(source_root, name, layout)
        (consumer / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.0.0"\n', encoding="utf-8",
        )
        for manifest in ("launch-wrapper-assets.json", "packaged-assets.json"):
            if (source / manifest).is_file():
                shutil.copyfile(source / manifest, consumer / manifest)
                data = json.loads((source / manifest).read_text(encoding="utf-8"))
                for asset in data["files"]:
                    origin = REPO / data["canonicalDir"] / asset
                    assert origin.resolve().is_relative_to(REPO.resolve())
                    target = source_root / data["canonicalDir"] / asset
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(origin, target)
    original = materialize_main.materialize_launch_wrapper_assets

    def later_owner(dest, *, canonical_root):
        result = original(dest, canonical_root=canonical_root)
        # Exercise ordering after the real final asset materializer, including
        # the engine's own installer (which has no installer-engine reference).
        script = dest / "plugins" / "agent-worktrees" / "scripts" / "install.ps1"
        with script.open("a", encoding="ascii") as stream:
            stream.write("\n# later owning materializer\n")
        return result

    monkeypatch.setattr(materialize_main, "materialize_launch_wrapper_assets", later_owner)
    if in_place:
        shutil.copytree(source_root, destination)
        report = materialize_main.materialize(destination, canonical_root=destination)
    else:
        report = materialize_main.build(destination, source_root=source_root)
    assert not [line for line in report if line.startswith("SKIP")], report
    supports = _human_admission()
    assert len(names) == 13
    for name in names:
        script = contract.human_output_entrypoint(destination / "plugins" / name)
        assert supports(script), name
        assert (script.parent / contract.HUMAN_HELPER).read_bytes() == (
            REPO / "libs" / "installer-engine" / contract.HUMAN_HELPER
        ).read_bytes()


@pytest.mark.parametrize(
    ("plugin", "entrypoint"),
    [("agent-bridge", "install.ps1"), ("agent-containers", "init.ps1"),
     ("agent-mcp", "init.ps1"), ("agent-machines", "init.ps1"),
     ("budget-guidance", "install.ps1")],
)
@pytest.mark.parametrize("asset", ["entrypoint", "declaration", "initializer"])
def test_same_root_invalid_human_source_fails_before_any_rewrite(
    tmp_path, monkeypatch, plugin, entrypoint, asset,
):
    import materialize_main

    root = tmp_path / "source"
    source = REPO / "plugins" / plugin / "scripts"
    scripts = root / "plugins" / plugin / "scripts"
    scripts.mkdir(parents=True)
    assets = {
        "entrypoint": entrypoint,
        "declaration": Path(entrypoint).with_suffix(".human-output.json").name,
        "initializer": "installer-human-output.ps1",
    }
    for name in assets.values():
        shutil.copyfile(source / name, scripts / name)
    canonical = root / "libs" / "installer-engine"
    canonical.mkdir(parents=True)
    shutil.copyfile(source / "installer-human-output.ps1", canonical / "installer-human-output.ps1")
    with (scripts / assets[asset]).open("a", encoding="ascii") as stream:
        stream.write("\n# invalid original source\n")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr(
        materialize_main, "materialize_file_pointers",
        lambda *args, **kwargs: pytest.fail("rewrites must not precede source validation"),
    )
    with pytest.raises(ValueError, match="conflicting native human-output assets"):
        materialize_main.materialize(root, canonical_root=root)
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    canonical_dir = tmp_path / "libs" / "installer-engine"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (canonical_dir / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    monkeypatch.setattr(sync_installer_engine, "REPO", tmp_path)
    monkeypatch.setattr(sync_installer_engine, "CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(sync_installer_engine, "ADOPTERS", ("agent-registered",))
    monkeypatch.setattr(sync_installer_engine, "PYTHON_ADOPTERS", ())
    return tmp_path


def _mk_plugin_scripts(repo: Path, name: str) -> Path:
    scripts = repo / "plugins" / name / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    return scripts


def test_python_engine_ships_and_checks_all_owned_process_files(fake_repo, monkeypatch):
    monkeypatch.setattr(sync_installer_engine, "ADOPTERS", ())
    monkeypatch.setattr(sync_installer_engine, "PYTHON_ADOPTERS", ("agent-python",))
    files = (*sync_installer_engine.ier.PYTHON_FILES, *sync_installer_engine.ier.SUPPORT_FILES)
    for name in files:
        (sync_installer_engine.CANONICAL_DIR / name).write_text(name, encoding="utf-8")
    assert len(sync_installer_engine.verify()) == len(files)
    assert len(sync_installer_engine.sync()) == len(files)
    assert sync_installer_engine.verify() == []
    scripts = fake_repo / "plugins" / "agent-python" / "scripts"
    (scripts / "installer_engine_process.py").write_text("drift", encoding="utf-8")
    assert any("installer_engine_process.py" in problem for problem in sync_installer_engine.verify())
    assert len(sync_installer_engine.sync()) == 1
    assert sync_installer_engine.verify() == []


def test_unregistered_python_engine_is_rejected(fake_repo, monkeypatch):
    monkeypatch.setattr(sync_installer_engine, "ADOPTERS", ())
    scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (scripts / "installer_engine.py").write_text("stray", encoding="utf-8")
    assert any("unregistered Python" in problem for problem in sync_installer_engine.verify())


def _write_canonical_refs(scripts: Path) -> None:
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )


def _write_local_refs(scripts: Path) -> None:
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/installer-engine.sh"\n',
        encoding="utf-8",
    )


def test_verify_passes_when_adopter_is_byte_identical(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    assert sync_installer_engine.verify() == []


def test_verify_accepts_a_canonical_reference_with_no_local_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)

    assert sync_installer_engine.verify() == []


def test_verify_flags_drifted_registered_adopter(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("drifted content\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("differs from" in p and "installer-engine.ps1" in p for p in problems)


def test_verify_flags_missing_registered_adopter(fake_repo):
    # agent-registered is declared in ADOPTERS but never vendored the files.
    assert any("is missing" in p for p in sync_installer_engine.verify())


def test_verify_flags_unregistered_adopter_with_a_stray_copy(fake_repo):
    # The registered adopter is in sync...
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    # ...but a second plugin has its own copy without being added to ADOPTERS.
    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (stray_scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_flags_unregistered_adopter_with_local_ref_and_no_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    _write_local_refs(stray_scripts)

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_flags_unregistered_adopter_with_trailing_comment_ref_and_no_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (stray_scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/installer-engine.sh" # load helpers\n',
        encoding="utf-8",
    )
    (stray_scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1') # load helpers\n",
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_ignores_plugin_with_no_installer_engine_file(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    _mk_plugin_scripts(fake_repo, "agent-unrelated")

    assert sync_installer_engine.verify() == []


def test_verify_flags_an_escaping_noncanonical_reference(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../outside/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    outside = fake_repo / "outside"
    outside.mkdir()
    (outside / "installer-engine.sh").write_text("nope\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("which is not libs/installer-engine/installer-engine.sh" in p for p in problems)


def test_verify_rejects_malformed_local_reference_even_when_copies_exist(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'missing/installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/missing/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("neither" in p and "installer-engine.sh" in p for p in problems)
    assert any("neither" in p and "installer-engine.ps1" in p for p in problems)


def test_verify_rejects_normalized_local_reference_with_missing_intermediate(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/missing/../installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'missing\\..\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("neither" in p and "missing/../installer-engine.sh" in p for p in problems)
    assert any("neither" in p and "missing\\..\\installer-engine.ps1" in p for p in problems)


def test_verify_rejects_mismatched_powershell_quotes(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1\")\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any("does not source installer-engine" in p for p in problems)


def test_verify_rejects_mixed_local_and_canonical_forms(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("mixes installer-engine forms" in p for p in problems)


def test_verify_rejects_symlinked_canonical_engine(fake_repo, native_file_symlink):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        native_file_symlink(fake_repo / "libs" / "installer-engine" / name, real / name)

    problems = sync_installer_engine.verify()

    assert any("is a symlink -- refusing" in p for p in problems)


def test_verify_rejects_symlinked_canonical_engine_in_local_mode(fake_repo, native_file_symlink):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        native_file_symlink(fake_repo / "libs" / "installer-engine" / name, real / name)

    problems = sync_installer_engine.verify()

    assert any("is a symlink -- refusing" in p for p in problems)


def test_verify_flags_duplicate_engine_source_lines(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any("contains 2 installer-engine source lines" in p for p in problems)


def test_sync_repairs_missing_local_copy_when_wrappers_are_local(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)

    written = sync_installer_engine.sync()

    assert "plugins/agent-registered/scripts/installer-engine.ps1" in written
    assert "plugins/agent-registered/scripts/installer-engine.sh" in written
    assert (scripts / "installer-engine.ps1").read_text(encoding="utf-8") == "canonical ps1\n"
    assert (scripts / "installer-engine.sh").read_text(encoding="utf-8") == "canonical sh\n"


def test_sync_removes_stale_local_copy_when_wrappers_are_canonical(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("stale\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("stale\n", encoding="utf-8")

    written = sync_installer_engine.sync()

    assert "removed plugins/agent-registered/scripts/installer-engine.ps1" in written
    assert "removed plugins/agent-registered/scripts/installer-engine.sh" in written
    assert not (scripts / "installer-engine.ps1").exists()
    assert not (scripts / "installer-engine.sh").exists()


@pytest.mark.parametrize("mode", ["canonical", "local"])
@pytest.mark.parametrize("initial", ["missing", "stale"])
def test_sync_repairs_python_support_in_both_wrapper_modes(fake_repo, mode, initial):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    writer = _write_canonical_refs if mode == "canonical" else _write_local_refs
    writer(scripts)
    canonical = fake_repo / "libs" / "installer-engine"
    with (canonical / "installer-engine.sh").open("a", encoding="utf-8") as stream:
        stream.write("# installer_dependency_policy.py\n")
    for name in sync_installer_engine.ier.SUPPORT_FILES:
        source = canonical / name
        source.write_text(f"# canonical {name}\n", encoding="utf-8")
        source.chmod(0o700)
        if initial == "stale":
            (scripts / name).write_text("# stale support\n", encoding="utf-8")
    if mode == "canonical" and initial == "missing":
        assert sync_installer_engine.verify() == []
    else:
        assert sync_installer_engine.verify()
    written = sync_installer_engine.sync()
    for name in sync_installer_engine.ier.SUPPORT_FILES:
        source = canonical / name
        destination = scripts / name
        assert f"plugins/agent-registered/scripts/{name}" in written
        assert destination.read_bytes() == source.read_bytes()
        if os.name != "nt":
            assert stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
    for name in sync_installer_engine.FILES:
        assert (scripts / name).exists() == (mode == "local")
    assert sync_installer_engine.verify() == []
    assert sync_installer_engine.sync() == []


def test_sync_refuses_symlinked_canonical_engine_in_local_mode(fake_repo, native_file_symlink):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        native_file_symlink(fake_repo / "libs" / "installer-engine" / name, real / name)

    with pytest.raises(RuntimeError, match="symlink -- refusing"):
        sync_installer_engine.sync()


@pytest.mark.parametrize("mode", ["canonical", "local"])
@pytest.mark.parametrize("name", sync_installer_engine.ier.SUPPORT_FILES)
@pytest.mark.parametrize("target_state", ["different", "matching", "dangling"])
def test_sync_refuses_linked_support_without_touching_target(
    fake_repo, native_file_symlink, mode, name, target_state,
):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    writer = _write_canonical_refs if mode == "canonical" else _write_local_refs
    writer(scripts)
    canonical = fake_repo / "libs" / "installer-engine"
    with (canonical / "installer-engine.sh").open("a", encoding="utf-8") as stream:
        stream.write("# installer_dependency_policy.py\n")
    for support in sync_installer_engine.ier.SUPPORT_FILES:
        source = canonical / support
        source.write_text(f"# canonical {support}\n", encoding="utf-8")
        source.chmod(0o700)
    outside = fake_repo / "unrelated"
    outside.mkdir()
    target = outside / name
    if target_state != "dangling":
        target.write_bytes(
            (canonical / name).read_bytes() if target_state == "matching" else b"unrelated bytes\n"
        )
        target.chmod(0o600)
    destination = scripts / name
    native_file_symlink(destination, target)
    original_link = os.readlink(destination)
    before = (
        target.read_bytes(), stat.S_IMODE(target.stat().st_mode), target.stat().st_mtime_ns,
    ) if target.exists() else None

    assert sync_installer_engine.verify()
    with pytest.raises(RuntimeError, match="symlink -- refusing"):
        sync_installer_engine.sync()

    assert destination.is_symlink()
    assert os.readlink(destination) == original_link
    if before is None:
        assert not target.exists()
    else:
        assert (
            target.read_bytes(), stat.S_IMODE(target.stat().st_mode), target.stat().st_mtime_ns,
        ) == before
