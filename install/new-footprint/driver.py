"""Cold Python entry for the experimental new-footprint bootstrap."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(*args: str) -> None:
    result = subprocess.run(args, timeout=1800, capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    result.check_returncode()


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--srcroot", type=Path, required=True)
    parser.add_argument("--repo")
    parser.add_argument("--knowledge-repo")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--clear-trial", action="store_true")
    args = parser.parse_args()
    root = args.root.absolute()
    if root != root.resolve() or root in (Path.home(), Path(root.anchor)):
        parser.error("refusing indirect or unscoped installation root")
    # Clear only an exclusively created trial under TEMP, never an adopted root.
    trial = root.parent == Path(tempfile.gettempdir()).resolve() and root.name.startswith("ce-bootstrap-trial-")
    marker = root / ".bootstrap-trial.json"
    if args.clear_trial:
        if not trial or not marker.is_file() or marker.is_symlink() or json.loads(marker.read_bytes()) != str(root):
            parser.error("not a bootstrap-owned temporary trial")
        if (root / "apps").exists():
            parser.error("installed applications require owner-mediated retirement")
        if set(p.name for p in root.iterdir()) - {marker.name, "registry"}:
            parser.error("unexpected trial contents")
        shutil.rmtree(root)
        return
    if trial and not root.exists():
        root.mkdir(mode=0o700)
        marker.write_text(json.dumps(str(root)), encoding="utf-8")
    kit = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv")
    if not uv:
        parser.error("uv is required")
    with tempfile.TemporaryDirectory(prefix="ce-onboard-") as raw:
        work = Path(raw)
        env = work / "tooling"
        run(uv, "venv", "--python", sys.executable, str(env))
        py = str(env / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        run(sys.executable, "-I", "-B", str(kit / "tools/seasoned_uv.py"), "--registry-only",
            "--uv", uv, "--", "--python", py, "--only-binary", ":all:", "packaging>=25", "pyyaml>=6.0.3")
        run(py, "-I", "-B", str(kit / "install/new-footprint/onboard.py"), *sys.argv[1:],
            "--workspace", raw)


if __name__ == "__main__":
    main()
