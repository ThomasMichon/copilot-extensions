"""Exercise prepared selection and the original native lifecycle binding."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

import pytest

from daemon_lifecycle_assets import lifecycle_archive


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "libs/installer-engine"
PROCUTIL = ROOT / "libs/agent-procutil/src/agent_procutil"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(sys.platform != "linux" or BASH is None, reason="native Linux binding")


def prepare_adapter_assets(helpers: Path, area: Path) -> dict[str, str]:
    assert BASH is not None
    for name in (
        "installer-instance-reconcile.sh", "bootstrap-uv.sh", "installer-instance-python-version.txt",
    ):
        shutil.copyfile(ENGINE / name, helpers / name)
    tools = area / "native tools"
    tools.mkdir()
    managed = area / "managed interpreter"
    managed.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$MANAGED_TRACE"\n'
        f"exec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    managed.chmod(0o700)
    uv = tools / "uv"
    uv.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$UV_TRACE"\n'
        '[[ -z "${UV_PYTHON+x}${UV_PYTHON_PREFERENCE+x}${UV_MANAGED_PYTHON+x}'
        '${UV_NO_MANAGED_PYTHON+x}${VIRTUAL_ENV+x}${CONDA_PREFIX+x}${PYTHONHOME+x}${PYTHONPATH+x}'
        '${UV_WORKING_DIR+x}${UV_WORKING_DIRECTORY+x}${UV_ISOLATED+x}${UV_ENV_FILE+x}'
        '${UV_PROJECT+x}${UV_FROZEN+x}${UV_LOCKED+x}${UV_NO_SYNC+x}" ]] || exit 96\n'
        'case "$*" in\n'
        '  --version) printf "uv 0.8.0\\n" ;;\n'
        '  "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18")\n'
        '    [[ "${MANAGED_READY:-1}" == 1 ]] || exit 79\n'
        '    if [[ "${STALL_UV:-0}" != 0 ]]; then\n'
        '      if [[ "$STALL_UV" == term-resistant ]]; then\n'
        '        "$BASH" -c \'trap "" TERM; exec sleep 30\' &\n'
        '      else sleep 30 & fi\n'
        '      child=$!; printf "%s\\n" "$child" > "$UV_CHILD"; wait "$child"\n'
        '    fi\n'
        + (
            f"    cygpath -u {shlex.quote(str(managed))} ;;\n" if os.name == "nt" else
            f"    printf '%s\\n' {shlex.quote(str(managed))} ;;\n"
        )
        +
        '  *) exit 97 ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    uv.chmod(0o700)
    for name in ("python", "python3"):
        poison = tools / name
        poison.write_text(
            '#!/bin/bash\nprintf touched > "$POISON_TRACE"\nexit 95\n', encoding="utf-8",
        )
        poison.chmod(0o700)
    return {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
        "UV_TRACE": str(area / "uv-trace"), "MANAGED_TRACE": str(area / "managed-trace"),
        "POISON_TRACE": str(area / "poison-trace"), "UV_CHILD": str(area / "uv-child"),
        "COPILOT_EXTENSIONS_DAEMON_PYTHON": str(tools / "python"),
        "UV_PYTHON": "foreign", "UV_PYTHON_PREFERENCE": "only-system",
        "VIRTUAL_ENV": str(area / "foreign-venv"), "PYTHONHOME": str(area / "foreign-home"),
        "PYTHONPATH": str(area / "foreign-path"),
    }


def _run(helpers: Path, registry: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-c",
         '. "$1/installer-instance-reconcile.sh"; '
         'before="$COPILOT_EXTENSIONS_DAEMON_PYTHON|$UV_PYTHON|$VIRTUAL_ENV"; '
         'reconcile_installer_instances "$1" "$2" "$2"; '
         '[[ "$before" == "$COPILOT_EXTENSIONS_DAEMON_PYTHON|$UV_PYTHON|$VIRTUAL_ENV" ]] || exit 92; '
         'printf installer-success',
         "fixture", str(helpers), str(registry)],
        env=environment, capture_output=True, text=True, timeout=8,
    )


def _helpers(area: Path) -> tuple[Path, Path, dict[str, str]]:
    helpers = area / "helpers"
    helpers.mkdir()
    registry = area / "registry"
    registry.mkdir()
    environment = prepare_adapter_assets(helpers, area)
    (helpers / "daemon_instances.sh").write_bytes(
        (PROCUTIL / "daemon_instances.sh").read_bytes()
        + b'\nreconcile_daemon_instances() {\n'
        + b'  [[ "$1" == 128 && "$2" == 5 && "$4" == "$3" && "$5" == false ]] || return 93\n'
        + b'  _di_lifecycle reconcile-contract > "$3/result"\n}\n',
    )
    archive = helpers / "daemon_instance_runtime.pyz"
    archive.write_bytes(lifecycle_archive(ROOT))
    archive.chmod(0o600)
    return helpers, registry, environment


def _native_path(area: Path, environment: dict[str, str], *, with_uv: bool) -> str:
    directory = area / "native-only"
    directory.mkdir()
    for name in ("timeout", "dirname", "wc", "cat", "readlink", "stat", "id"):
        executable = shutil.which(name)
        assert executable is not None
        (directory / name).symlink_to(executable)
    if with_uv:
        (directory / "uv").symlink_to(Path(environment["PATH"].split(os.pathsep)[0]) / "uv")
    return str(directory)


@pytest.mark.parametrize("ambient", ["poisoned", "absent"])
def test_native_adapter_uses_prepared_binding_not_ambient_python(tmp_path: Path, ambient: str) -> None:
    helpers, registry, environment = _helpers(tmp_path)
    if ambient == "absent":
        environment["PATH"] = _native_path(tmp_path, environment, with_uv=True)
    result = _run(helpers, registry, environment)
    assert result.returncode == 0 and result.stdout == "installer-success", result.stderr
    assert (registry / "result").read_text().strip() == (
        "copilot-daemon-reconcile-v1 termination-policy runtime-scope"
    ), result.stderr
    assert Path(environment["MANAGED_TRACE"]).read_text().startswith("-I -S -B ")
    assert Path(environment["UV_TRACE"]).read_text().splitlines() == [
        "--version",
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
    ]
    assert not Path(environment["POISON_TRACE"]).exists()
    assert not result.stderr


@pytest.mark.parametrize("fault", ["unprepared", "missing-provider", "invalid-request", "missing-uv"])
def test_unavailable_binding_defers_without_ambient_fallback(tmp_path: Path, fault: str) -> None:
    helpers, registry, environment = _helpers(tmp_path)
    if fault == "unprepared":
        environment["MANAGED_READY"] = "0"
    elif fault == "missing-provider":
        (helpers / "bootstrap-uv.sh").unlink()
    elif fault == "missing-uv":
        environment["PATH"] = _native_path(tmp_path, environment, with_uv=False)
    else:
        (helpers / "installer-instance-python-version.txt").write_text("python3\n", encoding="utf-8")
    result = _run(helpers, registry, environment)
    assert result.returncode == 0 and result.stdout == "installer-success"
    assert "deferred: managed-" in result.stderr
    assert not (registry / "result").exists()
    assert not Path(environment["POISON_TRACE"]).exists()
    assert not Path(environment["MANAGED_TRACE"]).exists()
    if Path(environment["UV_TRACE"]).exists():
        assert all("install" not in line for line in Path(environment["UV_TRACE"]).read_text().splitlines())


@pytest.mark.parametrize("term_policy", ["default", "term-resistant"])
def test_selection_and_owned_descendants_keep_the_existing_deadline(
    tmp_path: Path, term_policy: str,
) -> None:
    helpers, registry, environment = _helpers(tmp_path)
    environment["STALL_UV"] = term_policy
    started = time.monotonic()
    result = _run(helpers, registry, environment)
    assert time.monotonic() - started < 7
    assert result.returncode == 0 and result.stdout == "installer-success"
    assert "deferred: native-reconcile-failed" in result.stderr
    assert not (registry / "result").exists()
    assert not Path(environment["POISON_TRACE"]).exists()
    child = int(Path(environment["UV_CHILD"]).read_text())
    status = Path(f"/proc/{child}/stat")
    assert not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] == "Z"


def test_managed_selection_preserves_the_native_interpreter_custody_refusal(tmp_path: Path) -> None:
    helpers, registry, environment = _helpers(tmp_path)
    (tmp_path / "managed interpreter").chmod(0o775)
    result = _run(helpers, registry, environment)
    assert result.returncode == 0 and result.stdout == "installer-success"
    assert "daemon lifecycle interpreter custody unavailable" in result.stderr
    assert not Path(environment["MANAGED_TRACE"]).exists()
    assert not Path(environment["POISON_TRACE"]).exists()
