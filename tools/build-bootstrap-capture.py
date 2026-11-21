#!/usr/bin/env python3
"""Build the finite Windows bootstrap host with an already installed toolchain."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "libs" / "installer-engine" / "bootstrap-capture.cs"
OUTPUT = SOURCE.with_suffix(".exe")


def run(command: list[str], env: dict[str, str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=60, check=check,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env,
    )


def compile_source(source: Path, output: Path, *, sdk: str | None = None) -> str:
    if os.name != "nt":
        raise RuntimeError("Building the Windows host requires existing Windows framework references")
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise RuntimeError("An existing .NET SDK is required; this build never installs one")
    env = dict(os.environ, DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
               DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
    inventory = run([dotnet, "--list-sdks"], env, check=True)
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for line in inventory.stdout.splitlines():
        match = re.fullmatch(r"(\d+\.\d+\.\d+) \[(.+)]", line.strip())
        if match and (sdk is None or match[1] == sdk):
            version = tuple(int(part) for part in match[1].split("."))
            compiler = Path(match[2]) / match[1] / "Roslyn" / "bincore" / "csc.dll"
            if version[0] >= 8 and compiler.is_file():
                candidates.append((version, compiler))
    if not candidates:
        raise RuntimeError("No matching existing .NET 8+ SDK compiler is available")
    _, compiler = max(candidates)
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319"
    references = [
        framework / name for name in (
            "mscorlib.dll", "System.dll", "System.Core.dll",
            "System.Runtime.Serialization.dll", "System.Xml.dll",
        )
    ]
    if not all(path.is_file() for path in references):
        raise RuntimeError("Existing Windows .NET Framework references are unavailable")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        dotnet, str(compiler), "/nologo", "/noconfig", "/nostdlib+",
        "/target:exe", "/platform:anycpu", "/langversion:5", "/debug-",
        "/optimize+", "/deterministic+", "/warnaserror+", f"/pathmap:{ROOT}=/_/",
        f"/out:{output}", *(f"/reference:{path}" for path in references), str(source),
    ]
    result = run(command, env)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def build(output: Path, *, sdk: str | None = None) -> str:
    return compile_source(SOURCE, output, sdk=sdk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--sdk")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory(prefix="bootstrap-build-", dir=ROOT) as directory:
            candidate = Path(directory) / OUTPUT.name
            digest = build(candidate, sdk=args.sdk)
            if candidate.read_bytes() != args.output.read_bytes():
                raise RuntimeError("Checked-in bootstrap host differs from its deterministic build")
    else:
        digest = build(args.output, sdk=args.sdk)
    print(f"bootstrap-capture.exe sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
