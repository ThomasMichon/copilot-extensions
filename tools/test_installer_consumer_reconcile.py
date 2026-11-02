"""Opt-in real normal installer-engine consumer, with no network or host installs."""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("PROCUTIL_INSTALLER_CONSUMER_ACCEPTANCE") != "1"
    or os.getuid() == 0,
    reason="requires explicit non-root private Linux installer-consumer lane",
)

BACKEND = r'''
import base64,csv,hashlib,io,pathlib,tomllib,zipfile
def get_requires_for_build_wheel(config_settings=None): return []
def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):
 root=pathlib.Path(__file__).parent;p=tomllib.loads((root/"pyproject.toml").read_text())["project"]
 name=p["name"].replace("-","_");version=p["version"];info=f"{name}-{version}.dist-info"
 native=p["name"]=="agent-procutil";tag="py3-none-linux_x86_64" if native else "py3-none-any"
 files={f.relative_to(root/"src").as_posix():f.read_bytes() for f in (root/"src").rglob("*") if f.is_file() and "__pycache__" not in f.parts}
 metadata=f"Metadata-Version: 2.1\nName: {p['name']}\nVersion: {version}\n"+"".join(f"Requires-Dist: {d}\n" for d in p.get("dependencies",[]))
 files[info+"/METADATA"]=(metadata+"\n").encode()
 files[info+"/WHEEL"]=f"Wheel-Version: 1.0\nGenerator: isolated-product-fixture\nRoot-Is-Purelib: {str(not native).lower()}\nTag: {tag}\n".encode()
 rows=io.StringIO();writer=csv.writer(rows)
 for path,data in sorted(files.items()):writer.writerow([path,"sha256="+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode(),str(len(data))])
 writer.writerow([info+"/RECORD","",""]);files[info+"/RECORD"]=rows.getvalue().encode()
 wheel=f"{name}-{version}-{tag}.whl"
 with zipfile.ZipFile(pathlib.Path(wheel_directory)/wheel,"w") as archive:
  for path,data in files.items():
   entry=zipfile.ZipInfo(path);entry.external_attr=(0o100755 if path.endswith("/daemon-instance") else 0o100644)<<16;archive.writestr(entry,data)
 return wheel
'''

WORKER = r'''
import sys,time
from pathlib import Path
from agent_procutil.daemon_instances import admit_daemon_instance,DaemonLeasePolicy
root=Path(sys.argv[1]);registry=Path(sys.argv[2]);purpose=sys.argv[3]
policy=DaemonLeasePolicy(.5,1.5) if purpose=="unattended-test" else None
with admit_daemon_instance("consumer-"+purpose,runtime_root=root,source=Path(__file__),purpose=purpose,registry_root=registry,lease=policy) as guard:
 (root/"first.pid").write_text("owned")
 (root/"blocked").mkdir(exist_ok=True)
 (root/"blocked"/"second.pid").write_text("owned")
 guard.add_artifact(root/"first.pid");guard.add_artifact(root/"blocked"/"second.pid")
 (root/"proof.txt").write_text("retain")
 (root/"ready").write_text("ready")
 time.sleep(60)
'''


def _project(path, name, version, dependencies=""):
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\nversion="{version}"\n{dependencies}'
        '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
    )
    (path / "backend.py").write_text(BACKEND)
    return path


def _snapshot(slot):
    return {
        path.relative_to(slot).as_posix(): (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in slot.rglob("*") if path.is_file() and "__pycache__" not in path.parts
    }


def test_normal_consumer_fresh_repeat_healthy_update_and_real_cleanup_retry(tmp_path, monkeypatch):
    import agent_procutil

    engine_dir = ROOT / "libs" / "installer-engine"
    monkeypatch.syspath_prepend(str(ROOT / "libs" / "versioned-runtime"))
    monkeypatch.syspath_prepend(str(engine_dir))
    import installer_engine as engine
    from installer_instance_reconcile import reconcile_installer_instances

    owned = tmp_path / "work"
    owned.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(owned))
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(owned))
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_NO_CACHE", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    uv = shutil.which("uv")
    assert uv
    package_root = Path(agent_procutil.__file__).parent
    assert (package_root / "native" / "linux-x86_64" / "daemon-instance").is_file()
    procutil = _project(
        tmp_path / "inputs" / "procutil", "agent-procutil",
        importlib.metadata.version("agent-procutil"),
    )
    shutil.copytree(package_root, procutil / "src" / "agent_procutil", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    product = _project(
        tmp_path / "inputs" / "product", "normal-consumer-fixture", "1.0.0",
        'dependencies=["agent-procutil"]\n[tool.uv.sources]\nagent-procutil={path="../procutil",editable=true}\n',
    )
    (product / "src").mkdir()
    (product / "src" / "consumer_health.py").write_text("VALUE=1\n")
    registry = tmp_path / "registry"
    production = tmp_path / "protected-production"
    consumer = tmp_path / "normal-consumer"
    events = []

    def normal_install(root, version):
        # The fixture supplies only the product's outer operation. Admission,
        # construction, installed-origin health and reuse are the real engine.
        assert reconcile_installer_instances(package_root, registry_root=registry)
        with engine.numbered_candidate(
            root, version, plugin=product, uv=Path(uv), base_python=Path(sys.executable),
            health_module="consumer_health", wait_timeout=30, child_timeout=30,
        ) as candidate:
            current = engine.runtime.current_version(root)
            if current != version:
                engine.runtime.activate(root, version, link_name="venv", link_free=True)
            events.append((version, candidate.reused))
            return candidate.slot

    production_slot = normal_install(production, "1.0.0")
    production_markers = {
        name: (production / name).read_bytes() for name in ("current-version", "last-known-good")
    }
    production_files = _snapshot(production_slot)
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    live = []

    def start(data, purpose):
        data.mkdir()
        process = subprocess.Popen(
            [str(engine.runtime.slot_python(production, "1.0.0")), "-I", "-B", str(worker),
             str(data), str(registry), purpose],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        live.append(process)
        deadline = time.monotonic() + 5
        while not (data / "ready").exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(process.communicate()[1].decode())
            time.sleep(.01)
        assert (data / "ready").exists()
        return process

    def assert_production():
        assert prod.poll() is None
        assert engine.runtime.current_version(production) == "1.0.0"
        assert production_markers == {
            name: (production / name).read_bytes() for name in production_markers
        }
        assert _snapshot(production_slot) == production_files
        assert not registry.is_relative_to(production)
        for name in (".reconcile.cursor", ".reconcile.lock"):
            assert (registry / name).is_file()
            assert not (production / name).exists()
            assert not (production_slot / name).exists()

    def expired(label, partial=False):
        data = tmp_path / label
        process = start(data, "unattended-test")
        process.communicate(timeout=7)
        assert (data / "first.pid").exists()
        assert (data / "blocked" / "second.pid").exists()
        if partial:
            (data / "blocked").chmod(0o500)
        return data

    def assert_reclaimed(data):
        assert not (data / "first.pid").exists()
        assert not (data / "blocked" / "second.pid").exists()
        assert (data / "proof.txt").read_text() == "retain"
        assert_production()

    try:
        prod = start(tmp_path / "production-data", "production")
        fresh = expired("fresh-data")
        slot = normal_install(consumer, "1.0.0")
        assert events[-1] == ("1.0.0", False)
        assert_reclaimed(fresh)
        published = _snapshot(slot)
        for label in ("repeat-data", "healthy-data"):
            data = expired(label)
            assert normal_install(consumer, "1.0.0") == slot
            assert events[-1] == ("1.0.0", True)
            assert _snapshot(slot) == published
            assert_reclaimed(data)
        project_file = product / "pyproject.toml"
        project_file.write_text(project_file.read_text().replace('version="1.0.0"', 'version="2.0.0"'))
        update = expired("update-data")
        assert normal_install(consumer, "2.0.0").name == "2.0.0"
        assert events[-1] == ("2.0.0", False)
        assert_reclaimed(update)
        failed = expired("retry-data", partial=True)
        normal_install(consumer, "2.0.0")
        assert (failed / "blocked" / "second.pid").exists()
        assert (failed / "proof.txt").read_text() == "retain"
        first_removed = not (failed / "first.pid").exists()
        (failed / "blocked").chmod(0o700)
        normal_install(consumer, "2.0.0")
        assert_reclaimed(failed)
        print(f"real-normal-consumer fresh/repeat/healthy/update/cleanup-retry passed; first-attempt-partial={first_removed}")
    finally:
        for process in live:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=7)
