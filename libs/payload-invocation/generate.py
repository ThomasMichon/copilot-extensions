#!/usr/bin/env python3
"""Generate checked-in payload-local command shims from plugin manifests."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).resolve().parent / "templates"
SCHEMA = "copilot-extensions.payload-invocation"
VERSION = 1

_COMMAND = re.compile(r"^agent-[a-z0-9-]+$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RUNTIME_ROOT = re.compile(r"^\.[a-z0-9-]+$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]+$")
_PURPOSE = re.compile(r"^[A-Za-z0-9 ._/-]+$")
_OUTPUT_DIR = re.compile(r"^[a-z0-9][a-z0-9_./-]*$")


def load_manifest(path: Path) -> dict[str, str | int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or data.get("version") != VERSION:
        raise ValueError(f"{path}: expected {SCHEMA} version {VERSION}")
    checks = {
        "command": _COMMAND,
        "module": _MODULE,
        "runtimeRoot": _RUNTIME_ROOT,
        "noSelfProvisionEnv": _ENV,
        "purpose": _PURPOSE,
    }
    for field, pattern in checks.items():
        value = data.get(field)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError(f"{path}: invalid {field}: {value!r}")
    output_dir = data.get("outputDir", "bin")
    if (
        not isinstance(output_dir, str)
        or not _OUTPUT_DIR.fullmatch(output_dir)
        or ".." in Path(output_dir).parts
    ):
        raise ValueError(f"{path}: invalid outputDir: {output_dir!r}")
    data["outputDir"] = output_dir
    return data


def render(template: str, data: dict[str, str | int]) -> str:
    output_parts = Path(str(data["outputDir"])).parts
    payload_up = "/".join(".." for _part in output_parts)
    values = {
        "COMMAND": str(data["command"]),
        "MODULE": str(data["module"]),
        "RUNTIME_ROOT": str(data["runtimeRoot"]),
        "NO_SELFPROVISION_ENV": str(data["noSelfProvisionEnv"]),
        "PURPOSE": str(data["purpose"]),
        "OUTPUT_DIR": str(data["outputDir"]),
        "OUTPUT_DIR_PS": str(data["outputDir"]).replace("/", "\\"),
        "PAYLOAD_UP": payload_up,
        "PAYLOAD_UP_PS": payload_up.replace("/", "\\"),
        "PAYLOAD_UP_WIN": payload_up.replace("/", "\\"),
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"@@{key}@@", value)
    remaining = sorted(set(re.findall(r"@@[A-Z_]+@@", rendered)))
    if remaining:
        raise ValueError(f"unresolved template fields: {', '.join(remaining)}")
    return rendered


def expected_files(manifest: Path) -> dict[Path, str]:
    data = load_manifest(manifest)
    command = str(data["command"])
    output = manifest.parent / str(data["outputDir"])
    template_names = {
        output / command: "posix-shim.tmpl",
        output / f"{command}.ps1": "powershell-shim.tmpl",
        output / f"{command}.cmd": "cmd-shim.tmpl",
        manifest.parent / "scripts" / "emit-command-catalog.sh": "catalog-posix.tmpl",
        manifest.parent / "scripts" / "emit-command-catalog.ps1": "catalog-powershell.tmpl",
    }
    return {
        path: render((TEMPLATES / template).read_text(encoding="utf-8"), data)
        for path, template in template_names.items()
    }


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def process_manifest(manifest: Path, *, check: bool) -> list[str]:
    errors: list[str] = []
    for path, expected in expected_files(manifest).items():
        executable = path.suffix == "" or path.name == "emit-command-catalog.sh"
        if check:
            try:
                actual = path.read_text(encoding="utf-8")
            except OSError:
                actual = ""
            if actual != expected:
                errors.append(f"{display_path(path)}: generated content is stale")
            if (
                executable
                and os.name != "nt"
                and path.exists()
                and not path.stat().st_mode & stat.S_IXUSR
            ):
                errors.append(f"{display_path(path)}: POSIX shim is not executable")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8", newline="\n")
        if executable:
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"generated {display_path(path)}")
    return errors


def discover_manifests() -> list[Path]:
    return sorted((REPO / "plugins").glob("*/payload-invocation.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="process every plugin manifest")
    parser.add_argument("--check", action="store_true", help="fail when generated files drift")
    args = parser.parse_args(argv)

    manifests = discover_manifests() if args.all else [path.resolve() for path in args.manifests]
    if not manifests:
        parser.error("provide a manifest or use --all")

    errors: list[str] = []
    for manifest in manifests:
        errors.extend(process_manifest(manifest, check=args.check))
    if errors:
        print("\n".join(errors))
        return 1
    if args.check:
        print(f"payload-invocation: {len(manifests)} manifest(s) in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
