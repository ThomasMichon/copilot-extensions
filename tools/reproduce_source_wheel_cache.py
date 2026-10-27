#!/usr/bin/env python3
"""Offline UV local-source cache controls in fresh disposable environments."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


BACKEND = r'''
import base64
import hashlib
from pathlib import Path
import zipfile


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    root = Path(__file__).parent
    counter = root / "build-count.txt"
    counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")
    prefix = "cache_demo-1.0.dist-info/"
    files = {
        "cache_demo.py": (root / "cache_demo.py").read_bytes(),
        prefix + "METADATA": b"Metadata-Version: 2.4\nName: cache-demo\nVersion: 1.0\n\n",
        prefix + "WHEEL": b"Wheel-Version: 1.0\nGenerator: cache-control\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    records = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        records.append(f"{name},sha256={digest},{len(data)}")
    records.append(prefix + "RECORD,,")
    files[prefix + "RECORD"] = ("\n".join(records) + "\n").encode()
    filename = "cache_demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return filename
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.uv, args.python):
        if not path.is_absolute() or not path.is_file():
            parser.error("uv and python must be existing absolute executable files")
    if not args.scratch_parent.is_absolute() or not args.scratch_parent.is_dir():
        parser.error("scratch-parent must be an existing private absolute directory")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    task = Path(tempfile.mkdtemp(prefix=f"source-cache-control-{stamp}-", dir=args.scratch_parent))
    project = task / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="build_backend"\nbackend-path=["."]\n'
        '[project]\nname="cache-demo"\nversion="1.0"\ndependencies=[]\n',
        encoding="utf-8",
    )
    (project / "build_backend.py").write_text(BACKEND, encoding="utf-8")
    module = project / "cache_demo.py"
    module.write_text('VALUE = "original"\n', encoding="utf-8")
    requirement = task / "source.in"
    requirement.write_text(f"cache-demo @ {project.as_uri()}\n", encoding="utf-8")
    report: dict = {"scope": "Synthetic offline cache characterization, not installer acceptance", "steps": []}
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("UV_", "PIP_", "PYTHON"))
    }
    environment["UV_NO_PROGRESS"] = "1"
    environment["NO_COLOR"] = "1"
    process_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def run(name: str, arguments: list[str]) -> None:
        command = [str(args.uv), "--no-config", "--offline", "--cache-dir", str(task / "cache"), *arguments]
        start = time.monotonic()
        result = subprocess.run(
            command, cwd=task, env=environment, capture_output=True, **process_options,
        )
        text = result.stderr.decode("utf-8", errors="replace")
        (task / f"{name}.stdout").write_bytes(result.stdout)
        (task / f"{name}.stderr").write_bytes(result.stderr)
        report["steps"].append({
            "name": name, "seconds": time.monotonic() - start, "exit": result.returncode,
            "building_lines": sum(bool(re.match(r"^\s*Building ", line)) for line in text.splitlines()),
        })
        (task / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if result.returncode:
            raise SystemExit(f"{name} exited {result.returncode}; logs retained in {task}")

    for label in ("explicit-first", "explicit-repeat", "requirements", "source-change"):
        if label == "source-change":
            module.write_text('VALUE = "changed"\n', encoding="utf-8")
        venv = task / label
        run(f"{label}-venv", [
            "venv", "--python", str(args.python), "--no-python-downloads", "--no-project", str(venv),
        ])
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        source = [str(project)] if label.startswith("explicit-") else ["-r", str(requirement)]
        run(f"{label}-install", [
            "pip", "install", "--python", str(python), "--no-index",
            "--no-deps", "--no-sources", "--no-editable", *source,
        ])
        sites = list(venv.glob("Lib/site-packages")) if os.name == "nt" else list(venv.glob("lib/python*/site-packages"))
        if len(sites) != 1:
            raise RuntimeError("The isolated target does not have one site-packages directory")
        installed = (sites[0] / "cache_demo.py").read_text(encoding="utf-8")
        report.setdefault("cases", {})[label] = {
            "build_count": int((project / "build-count.txt").read_text()),
            "installed_changed_source": 'VALUE = "changed"' in installed,
        }
    report["status"] = "completed"
    (task / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"task_directory": str(task), **report}, indent=2))


if __name__ == "__main__":
    main()
