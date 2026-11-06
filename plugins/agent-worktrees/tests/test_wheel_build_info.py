import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "agent_worktrees" / "_build_info.py"
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def install_fixture(package_root, metadata_root, *, editable=False):
    package = package_root / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(SOURCE, package / "_build_info.py")
    metadata = metadata_root / "agent_worktrees-9.8.7.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: agent-worktrees\nVersion: 9.8.7\n\n",
        encoding="utf-8",
    )
    (metadata / "WHEEL").write_text("Wheel-Version: 1.0\n", encoding="utf-8")
    content = (package / "_build_info.py").read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
    (metadata / "RECORD").write_text(
        f"agent_worktrees/_build_info.py,sha256={digest},{len(content)}\n", encoding="utf-8",
    )
    if editable:
        (metadata / "direct_url.json").write_text(
            json.dumps({"url": "file:///source", "dir_info": {"editable": True}}),
            encoding="utf-8",
        )


def read_info(python, *paths):
    code = (
        "import json,sys; sys.path[:0]=sys.argv[1:]; "
        "from agent_worktrees._build_info import BUILD_INFO; print(json.dumps(BUILD_INFO))"
    )
    result = subprocess.run(
        [str(python), "-I", "-c", code, *(str(path) for path in paths)],
        check=True, capture_output=True, text=True, timeout=30, creationflags=HEADLESS,
    )
    return json.loads(result.stdout)


class WheelBuildInfoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_unprepared_installed_distribution_reports_its_own_version(self):
        site = self.root / "site"
        install_fixture(site, site)
        info = read_info(sys.executable, site)
        self.assertEqual(info["version"], "9.8.7")
        self.assertEqual(info["commit"], "unknown")
        self.assertEqual(info["source"], "distribution")

    def test_foreign_or_editable_metadata_does_not_identify_source_code(self):
        package, foreign = self.root / "package", self.root / "foreign"
        install_fixture(package, foreign)
        self.assertEqual(read_info(sys.executable, package, foreign)["version"], "1.0.0")
        editable = self.root / "editable"
        install_fixture(editable, editable, editable=True)
        self.assertEqual(read_info(sys.executable, editable)["version"], "1.0.0")
        stale = self.root / "stale"
        install_fixture(stale, stale)
        module = stale / "agent_worktrees" / "_build_info.py"
        module.write_text(module.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(read_info(sys.executable, stale)["version"], "1.0.0")

    def test_prepared_runtime_build_info_remains_authoritative(self):
        slot = self.root / "runtime"
        venv = slot / ".venv"
        subprocess.run(
            [sys.executable, "-I", "-m", "venv", "--without-pip", "--copies", str(venv)],
            check=True, capture_output=True, timeout=30, creationflags=HEADLESS,
        )
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = self.root / "site"
        install_fixture(site, site)
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        prepared = {"version": "2.3.4", "commit": "abc", "source": "prepared"}
        (bootstrap / "build-info.json").write_text(json.dumps(prepared), encoding="utf-8")
        self.assertEqual(read_info(python, site), prepared)
