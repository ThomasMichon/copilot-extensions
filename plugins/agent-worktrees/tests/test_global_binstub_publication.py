"""The required global tools publish exact files before runtime activation."""
from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess
import sys
import os
import tempfile
import venv

import pytest

from test_source_python_publication import (
    CALLBACK_TIMEOUT, admitted, native_hosts, publication_case as shared_publication_case,
)

CRITICAL = "agent-worktrees.cmd"
NAMES = ("agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1")
publication_case = shared_publication_case


@pytest.mark.parametrize(("shell", "executable"), native_hosts())
def test_global_frontdoor_keeps_import_cache_outside_the_selected_slot(
    tmp_path: Path, shell: str, executable: str,
) -> None:
    from agent_procutil import no_window_kwargs, python_cache_args
    from test_source_install_transactions import _ps, _sh

    plugin = Path(__file__).resolve().parents[1]
    home = tmp_path / "home with spaces"
    root = home / ".agent-worktrees"
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    library = slot / ("Lib/site-packages" if os.name == "nt" else
                      f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    package = library / "agent_worktrees"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,sys\nprint(json.dumps({'args':sys.argv[1:],"
        "'cache':sys.pycache_prefix,'prefix':sys.prefix}))\n", encoding="utf-8",
    )
    local_bin = home / ".local" / "bin"
    shutil.copytree(plugin / "bin", local_bin)
    resolver = root / "bin"
    resolver.mkdir()
    (resolver / "resolve-runtime.ps1").write_text(f"$AwPy = {_ps(python)}\n", encoding="utf-8")
    (resolver / "resolve-runtime.sh").write_text(f"AW_PY={_sh(python)}\n", encoding="utf-8")
    before = {path.relative_to(slot): path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    env = {key: value for key, value in os.environ.items() if key.upper() not in {
        "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTHONPATH", "PYTHONHOME",
    }}
    env.update(HOME=str(home), USERPROFILE=str(home))
    entry = local_bin / ("agent-worktrees.ps1" if shell == "ps1" else "agent-worktrees")
    command = (
        [executable, "-NoProfile", "-File", str(entry)]
        if shell == "ps1" else [executable, str(entry)]
    )
    result = subprocess.run(
        [*command, "cache-probe", "argument with spaces"], env=env, cwd=home,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["args"] == ["cache-probe", "argument with spaces"]
    assert Path(observed["prefix"]).resolve() == slot.resolve()
    assert observed["cache"] == python_cache_args(root)[1].split("=", 1)[1]
    assert {path.relative_to(slot): path.read_bytes() for path in slot.rglob("*") if path.is_file()} == before
    assert list((root / "cache" / "python").rglob("__main__*.pyc"))


@pytest.fixture
def publication_target():
    return CRITICAL


def publication_io_transform(fault, shell, monkeypatch, publication_target):
    return lambda _content: None


def inject_copy_fault(driver, fault, critical, monkeypatch):
    publication = driver.runtime._runtime_component("versioned_runtime_publication")
    original_stage, original_replace = publication._stage, publication.os.replace
    fired = False

    def stage(root, target, data, mode, receipt, **kwargs):
        if fault == "partial-write" and target.name == critical and kwargs.get("phase") == "forward":
            fd, name = tempfile.mkstemp(prefix=f".{critical}.partial-", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"partial")
            raise OSError(f"Injected partial file stage for {critical}: {name}")
        return original_stage(root, target, data, mode, receipt, **kwargs)

    def replace(source, target):
        nonlocal fired
        if fault == "directory-race" and Path(target).name == critical and not fired:
            fired = True
            target = Path(target)
            target.unlink()
            target.mkdir()
            (target / "sentinel").write_text("peer")
        return original_replace(source, target)

    monkeypatch.setattr(publication, "_stage", stage)
    monkeypatch.setattr(publication.os, "replace", replace)


@pytest.fixture
def publication_transform(fault, shell, monkeypatch, publication_target):
    return publication_io_transform(fault, shell, monkeypatch, publication_target)


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize(("shell", "executable"), native_hosts())
@pytest.mark.parametrize("fault", ["healthy", "same-content", "initial-directory", "directory-race", "partial-write"])
@pytest.mark.timeout(90)
def test_actual_callback_global_file_publication(
    publication_case, shell, executable, fault, monkeypatch,
):
    driver, root, slot, owner, content, _identity = publication_case
    installed = root.parent / ".copilot/installed-plugins/example/agent-worktrees"
    shutil.copytree(owner, installed)
    (root.parent / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }), encoding="utf-8")
    owner = installed
    with admitted(driver, root, owner, content, monkeypatch) as (governance, _lease):
        with governance.manifest_publication(
            b'{"schema_version":3,"service":"agent-worktrees"}',
        ) as bound:
            (root / "deploy-manifest.json").write_bytes(bound)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    local_bin = root.parent / ".local" / "bin"
    local_bin.mkdir(parents=True)
    for name in NAMES:
        value = (content / "bin" / name).read_bytes() if fault == "same-content" else b"previous\n"
        if fault == "same-content":
            value = value.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        (local_bin / name).write_bytes(value)
    resolvers = [root / "bin" / name for name in ("resolve-runtime.ps1", "resolve-runtime.sh")]
    for resolver in resolvers:
        resolver.parent.mkdir(parents=True, exist_ok=True)
        resolver.write_bytes(b"previous resolver\n")
    target = local_bin / CRITICAL
    inject_copy_fault(driver, fault, CRITICAL, monkeypatch)
    if fault == "initial-directory":
        target.unlink()
        target.mkdir()
        (target / "sentinel").write_text("peer", encoding="utf-8")
    with admitted(driver, root, owner, content, monkeypatch) as (governance, _lease):
        for name in ("current-version", "last-known-good"):
            (root / name).write_text("previous\n", encoding="utf-8")
        protected = {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in (root / "current-version", root / "last-known-good", root / "deploy-manifest.json")
        }
        before = {
            name: ((local_bin / name).read_bytes(), (local_bin / name).stat().st_ino,
                   (local_bin / name).stat().st_mtime_ns)
            for name in NAMES if (local_bin / name).is_file()
        }
        with driver.runtime.retention_custody(root, slot.name, payload_hash="candidate") as (_slot, descriptor):
            if fault in ("healthy", "same-content"):
                transaction = driver._publish_helpers(
                    governance, {"shell": shell, "executable": executable, "scope": "resolvers"},
                    content, slot, descriptor, timeout=CALLBACK_TIMEOUT, base_python=Path(sys._base_executable),
                )
                for name in NAMES:
                    if fault == "same-content" and shell == "ps1":
                        current = local_bin / name
                        assert (current.read_bytes(), current.stat().st_ino, current.stat().st_mtime_ns) == before[name]
                    else:
                        assert (local_bin / name).read_bytes() == (content / "bin" / name).read_bytes()
                assert not list(local_bin.glob(".*.publication.*"))
                transaction.keep()
            else:
                with pytest.raises((OSError, ValueError, subprocess.CalledProcessError)) as caught:
                    driver._publish_helpers(
                        governance, {"shell": shell, "executable": executable, "scope": "resolvers"},
                        content, slot, descriptor, timeout=CALLBACK_TIMEOUT, base_python=Path(sys._base_executable),
                    )
                diagnostic = (str(caught.value) + str(getattr(caught.value, "stderr", ""))).casefold()
                assert CRITICAL in diagnostic, diagnostic
                if fault == "partial-write":
                    assert target.read_bytes() == b"previous\n"
                    assert any(path.read_bytes() == b"partial" for path in local_bin.rglob("*") if path.is_file())
                else:
                    assert target.is_dir()
                    assert [path.name for path in target.iterdir()] == ["sentinel"]
                    assert (target / "sentinel").read_text() == "peer"
                for name in NAMES:
                    if name != CRITICAL:
                        assert (local_bin / name).read_bytes() == before[name][0], (
                            f"Earlier uncontested global binstub changed after {CRITICAL} failed: {name}"
                        )
                assert all(path.read_bytes() == b"previous resolver\n" for path in resolvers)
        assert {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in protected
        } == protected
