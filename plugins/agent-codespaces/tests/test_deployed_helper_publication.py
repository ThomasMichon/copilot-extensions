"""Real installer callers must replace exact helper files, never directory children."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[3]
ENGINE = REPO / "libs" / "installer-engine" / "installer-engine.sh"
HELPERS = (
    "versioned_runtime.py", "versioned_runtime_leases.py",
    "versioned_runtime_construction.py", "versioned_runtime_source.py",
    "versioned_runtime_publication.py",
)
RESOLVERS = ("resolve-runtime.sh", "resolve-runtime.ps1")
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def installer_function(plugin: str, extension: str, name: str) -> str:
    path = REPO / "plugins" / plugin / "scripts" / f"install.{extension}"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    declaration = rf"^function {re.escape(name)}(?:\s|\()" if extension == "ps1" else rf"^{name}\(\) "
    following = r"^function [A-Za-z_]" if extension == "ps1" else r"^[A-Za-z_][A-Za-z0-9_]*\(\) [({]"
    start = next(index for index, line in enumerate(lines) if re.match(declaration, line))
    delimiter = None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if delimiter is not None:
            if (line.startswith(delimiter) if extension == "ps1" else line.strip() == delimiter):
                delimiter = None
            continue
        if line.rstrip() in ("}", ")"):
            end = index + 1
            break
        if re.match(following, line):
            end = index
            break
        pattern = r"@(['\"])\s*$" if extension == "ps1" else r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1"
        match = re.search(pattern, line)
        if match:
            delimiter = match[1] + "@" if extension == "ps1" else match[2]
    return "".join(lines[start:end])


def bash() -> str:
    if os.name == "nt":
        git = shutil.which("git")
        executable = Path(git).parent.parent / "bin" / "bash.exe" if git else None
    else:
        executable = shutil.which("bash")
    if not executable or not Path(executable).is_file():
        pytest.skip("native Bash unavailable")
    return str(executable)


def payload(tmp_path: Path) -> Path:
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    for name in (*HELPERS, *RESOLVERS):
        source = REPO / "plugins" / "agent-codespaces" / "scripts" / name
        shutil.copyfile(source, scripts / name)
        (scripts / name).chmod(0o755 if name.endswith(".sh") else 0o644)
    return scripts


def test_codespaces_helper_directory_is_not_a_successful_copy(tmp_path: Path) -> None:
    scripts = payload(tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "versioned_runtime_publication.py"
    target.mkdir()
    (target / "sentinel").write_text("peer", encoding="utf-8")
    script = tmp_path / "helper-probe.sh"
    script.write_text(f"""
set -euo pipefail
umask 027
. '{ENGINE.as_posix()}'
SCRIPT_DIR='{scripts.as_posix()}'; INSTALL_DIR='{root.as_posix()}'
_fail() {{ printf '%s\\n' "$*" >&2; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
{installer_function("agent-codespaces", "sh", "_deploy_versioned_runtime_helper")}
if _deploy_versioned_runtime_helper; then exit 0; else exit $?; fi
""", encoding="utf-8", newline="\n")
    result = subprocess.run(
        [bash(), script.as_posix()],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert result.returncode != 0, f"False success; directory children: {list(target.iterdir())}"
    assert (target / "sentinel").read_text() == "peer"
    assert [path.name for path in target.iterdir()] == ["sentinel"]


INDEX_SETUP = r"""
fake_python() {
    case "$*" in
        *construction-shell*)
            cat <<'CONSTRUCTION'
_runtime_construction_begin() { _RUNTIME_CONSTRUCTION_REUSE=0; }
_runtime_construction_complete() { printf 'complete\n' >> "$PHASE_LOG"; }
_runtime_construction_finish() { :; }
CONSTRUCTION
            ;;
        *activate*) printf 'activate\n' >> "$PHASE_LOG" ;;
        *) return 96 ;;
    esac
}
_bootstrap_python() { printf fake_python; }
_payload_hash() { printf hash; }
_ensure_uv() { return 0; }; _ensure_uv_index() { :; }
_resolve_zdd() { return 1; }; _resolve_vendored_lib() { return 1; }
_uv_pip_install() { return 0; }
_with_index_build_lock() { shift; "$@"; }
_test_index_venv() { printf 'health\n' >> "$PHASE_LOG"; }
_new_index_venv() {
    mkdir -p "${2%/*}"
    printf '#!/usr/bin/env bash\nprintf "imports\\n" >> "$PHASE_LOG"\n' > "$2"
    chmod +x "$2"
}
"""


@pytest.mark.parametrize(("plugin", "critical_name"), [
    ("agent-codespaces", "versioned_runtime_publication.py"),
    ("agent-index", "versioned_runtime_publication.py"),
    ("agent-bridge", "versioned_runtime_publication.py"),
    ("agent-logger", "versioned_runtime_publication.py"),
    ("agent-bridge", "resolve-runtime.sh"),
    ("agent-logger", "resolve-runtime.sh"),
])
@pytest.mark.parametrize("fault", ["first-install", "healthy", "directory", "race", "copy-error"])
def test_actual_bash_helper_callers(
    tmp_path: Path, plugin: str, critical_name: str, fault: str,
) -> None:
    scripts = payload(tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    functions = {
        "agent-codespaces": "_deploy_versioned_runtime_helper",
        "agent-index": "_install_engine",
        "agent-bridge": "deploy_binstub",
        "agent-logger": "deploy_runtime_helpers",
    }
    destinations = {
        name: root / ("scripts" if name in HELPERS else "bin") / name
        if plugin == "agent-bridge" else root / "bin" / name
        if plugin == "agent-logger" else root / name
        for name in ((*HELPERS, *RESOLVERS) if plugin in ("agent-bridge", "agent-logger") else HELPERS)
    }
    critical = destinations[critical_name]
    for destination in destinations.values():
        destination.parent.mkdir(exist_ok=True)
        if fault != "first-install":
            destination.write_bytes(b"previous\n")
            destination.chmod(0o600)
    if fault == "directory":
        critical.unlink()
        critical.mkdir()
        (critical / "sentinel").write_text("peer", encoding="utf-8")
    script = tmp_path / "helper-probe.sh"
    script.write_text(f"""
set -euo pipefail
umask 027
. '{ENGINE.as_posix()}'
SCRIPT_DIR='{scripts.as_posix()}'; INSTALL_DIR='{root.as_posix()}'
PLUGIN_DIR="${{SCRIPT_DIR%/*}}"; ENGINE_HOME="$INSTALL_DIR"; SRC_VERSION=1.2.3
LOCAL_BIN="$INSTALL_DIR/local-bin"; BINSTUB="$LOCAL_BIN/agent-bridge"
PUBLISH_GLOBAL_BINSTUBS=false
export PHASE_LOG="$INSTALL_DIR/phases"
critical='{critical.as_posix()}'
_fail() {{ printf '%s\\n' "$*" >&2; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}
python() {{ printf forbidden >> "$INSTALL_DIR/python-call"; return 97; }}
python3() {{ python "$@"; }}; _bootstrap_python() {{ python; }}
{INDEX_SETUP if plugin == "agent-index" else ""}
{installer_function(plugin, "sh", functions[plugin])}
mv() {{
    if [[ '{fault}' == race && "${{@: -2:1}}" == */'{critical_name}' ]]; then
        command rm -f "$critical"; command mkdir "$critical"
        printf peer > "$critical/sentinel"
    fi
    command mv "$@"
}}
cp() {{
    if [[ '{fault}' == copy-error && "${{@: -2:1}}" == "$SCRIPT_DIR/{critical_name}" ]]; then
        printf partial > "${{@: -1}}"
        return 75
    fi
    command cp "$@"
}}
if {functions[plugin]}; then exit 0; else exit $?; fi
""", encoding="utf-8", newline="\n")
    result = subprocess.run(
        [bash(), script.as_posix()],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert not (root / "python-call").exists()
    assert not (root / "local-bin").exists()
    if fault in ("first-install", "healthy"):
        assert result.returncode == 0, result.stdout + result.stderr
        for name, destination in destinations.items():
            assert destination.read_bytes() == (scripts / name).read_bytes()
            if os.name != "nt":
                expected = 0o600 if fault == "healthy" else (
                    (scripts / name).stat().st_mode & 0o777 & ~0o027
                )
                assert destination.stat().st_mode & 0o777 == expected
        assert not list(root.rglob("*.publication-stage.*"))
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        if fault in ("directory", "race"):
            assert (critical / "sentinel").read_text() == "peer"
            assert [path.name for path in critical.iterdir()] == ["sentinel"]
        else:
            assert critical.read_bytes() == b"previous\n"
        if fault != "directory" or plugin != "agent-logger":
            assert list(critical.parent.glob(f".{critical.name}.publication-stage.*"))
    if plugin == "agent-index":
        phases = (root / "phases").read_text().splitlines()
        assert phases[:3] == ["health", "imports", "complete"]
        assert ("activate" in phases) == (fault in ("first-install", "healthy"))


@pytest.mark.parametrize("publish_global", [False, True])
def test_bridge_bash_optional_resolvers_and_global_branch(tmp_path: Path, publish_global: bool) -> None:
    scripts = payload(tmp_path)
    for name in RESOLVERS:
        (scripts / name).unlink()
    root = tmp_path / "runtime"
    script = tmp_path / "optional-probe.sh"
    script.write_text(f"""
set -euo pipefail
. '{ENGINE.as_posix()}'
SCRIPT_DIR='{scripts.as_posix()}'; INSTALL_DIR='{root.as_posix()}'
LOCAL_BIN="$INSTALL_DIR/local-bin"; BINSTUB="$LOCAL_BIN/agent-bridge"
PUBLISH_GLOBAL_BINSTUBS={'true' if publish_global else 'false'}
_fail() {{ printf '%s\\n' "$*" >&2; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}; _ok() {{ :; }}
python() {{ return 97; }}; python3() {{ return 97; }}; _bootstrap_python() {{ return 97; }}
{installer_function("agent-bridge", "sh", "deploy_binstub")}
if deploy_binstub; then exit 0; else exit $?; fi
""", encoding="utf-8", newline="\n")
    result = subprocess.run(
        [bash(), script.as_posix()], cwd=tmp_path, capture_output=True,
        text=True, timeout=20, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "local-bin" / "agent-bridge").exists() == publish_global
    for name in HELPERS:
        assert (root / "scripts" / name).read_bytes() == (scripts / name).read_bytes()
    assert not any((root / "bin" / name).exists() for name in RESOLVERS)
    assert not list(root.rglob("*.publication-stage.*"))


def powershell(host: str) -> str:
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    return executable


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
@pytest.mark.parametrize("fault", [
    "healthy", "directory-helper", "directory-resolver", "copy-error", "optional-missing",
])
def test_actual_bridge_powershell_helper_caller(
    tmp_path: Path, host: str, fault: str,
) -> None:
    executable = powershell(host)
    scripts = payload(tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    destinations = {
        name: root / ("scripts" if name in HELPERS else "bin") / name
        for name in (*HELPERS, *RESOLVERS)
    }
    for destination in destinations.values():
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(b"previous\n")
    critical = destinations["resolve-runtime.sh" if fault == "directory-resolver"
                            else "versioned_runtime_publication.py"]
    if fault.startswith("directory"):
        critical.unlink()
        critical.mkdir()
        (critical / "sentinel").write_text("peer", encoding="utf-8")
    if fault == "optional-missing":
        for name in RESOLVERS:
            (scripts / name).unlink()
    script = scripts / "probe.ps1"
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$InstallDir = '{root.as_posix()}'
$publishGlobalBinstubs = $false
function Write-Ok {{ param($Message) }}
{installer_function("agent-bridge", "ps1", "Write-Binstubs")}
$held = $null
try {{
    if ('{fault}' -eq 'copy-error') {{
        $held = [IO.File]::Open((Join-Path $PSScriptRoot 'versioned_runtime_publication.py'),
            [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
    }}
    Write-Binstubs
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 71
}} finally {{
    if ($held) {{ $held.Dispose() }}
}}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    if fault in ("healthy", "optional-missing"):
        assert result.returncode == 0, result.stdout + result.stderr
        for name, destination in destinations.items():
            expected = b"previous\n" if fault == "optional-missing" and name in RESOLVERS else (
                scripts / name
            ).read_bytes()
            assert destination.read_bytes() == expected
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        if fault.startswith("directory"):
            assert [path.name for path in critical.iterdir()] == ["sentinel"]
            assert (critical / "sentinel").read_text() == "peer"
        else:
            assert critical.read_bytes() == b"previous\n"
