"""Explicit package/source build step; never called by a runtime launcher."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys


def build_native(output: Path, *, compiler: str | None = None) -> Path:
    if not sys.platform.startswith("linux") or platform.machine() not in ("x86_64", "aarch64"):
        raise RuntimeError("native daemon binding build requires supported Linux x86_64/aarch64")
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("daemon_instance_native.c")
    pending = output.with_name(output.name + f".{os.getpid()}.pending")
    command = [
        *shlex.split(compiler or os.environ.get("CC", "cc")), "-O2", "-std=c11",
        "-Wall", "-Wextra", str(source), "-o", str(pending),
    ]
    try:
        subprocess.run(
            command, check=True, timeout=60,
            env={**os.environ, "TMPDIR": str(output.parent)},
        )
        pending.chmod(0o755)
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler")
    args = parser.parse_args()
    print(build_native(args.output, compiler=args.compiler))
