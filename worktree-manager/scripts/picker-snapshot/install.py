#!/usr/bin/env python3
"""Install a locked closure only after configured-registry age admission."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

MIN_AGE_SECONDS = 604800
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
STRONG_ALGORITHMS = {"sha256", "sha384", "sha512"}


class AdmissionError(RuntimeError):
    """The locked dependency closure could not prove release age and integrity."""


def integrity_digests(value: object) -> dict[str, bytes]:
    if value is None or value == "":
        return {}
    if not isinstance(value, str):
        raise AdmissionError("unsupported registry integrity")
    sizes = {"sha1": 20, "sha256": 32, "sha384": 48, "sha512": 64}
    digests = {}
    for token in value.split():
        algorithm, separator, encoded = token.split("?", 1)[0].partition("-")
        if not separator or algorithm not in sizes:
            raise AdmissionError("unsupported registry integrity")
        try:
            digest = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise AdmissionError("malformed registry integrity") from exc
        if len(digest) != sizes[algorithm] or (
            algorithm in digests and digests[algorithm] != digest
        ):
            raise AdmissionError("malformed or ambiguous registry integrity")
        digests[algorithm] = digest
    return digests


def seasoned(timestamp: object, now: datetime) -> bool:
    if not isinstance(timestamp, str) or now.tzinfo is None:
        return False
    try:
        uploaded = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return uploaded.tzinfo is not None and (now - uploaded).total_seconds() >= MIN_AGE_SECONDS
    except (ValueError, OverflowError):
        return False


def npm_command() -> list[str]:
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise AdmissionError("node and npm are required")
    executable = Path(npm).resolve()
    if executable.suffix == ".js":
        return [node, str(executable)]
    candidates = [
        executable.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
        executable.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
    ]
    cli = next((path for path in candidates if path.is_file()), None)
    if cli is None:
        raise AdmissionError("cannot locate npm's attributable CLI")
    return [node, str(cli)]


def read_metadata(command: list[str], project: Path, name: str, version: str) -> dict:
    try:
        result = subprocess.run(
            [*command, "view", f"{name}@{version}", "time", "dist", "--json", "--prefer-online"],
            cwd=project, capture_output=True, text=True, timeout=30, creationflags=HEADLESS,
        )
        if result.returncode != 0:
            raise AdmissionError(f"configured-registry metadata query failed: {name}")
        metadata = json.loads(result.stdout)
        if not isinstance(metadata, dict):
            raise ValueError("metadata is not an object")
        dist = metadata.get("dist")
        times = metadata.get("time")
        timestamp = times.get(version) if isinstance(times, dict) else None
        digests = integrity_digests(dist.get("integrity")) if isinstance(dist, dict) else {}
        if (isinstance(dist, dict) and not STRONG_ALGORITHMS.intersection(digests)
                and seasoned(timestamp, datetime.now(timezone.utc))):
            shasum = dist.get("shasum")
            if shasum is None and "sha1" in digests:
                shasum = digests["sha1"].hex()
            if (not isinstance(shasum, str) or not re.fullmatch(r"[0-9a-f]{40}", shasum)
                    or ("sha1" in digests and digests["sha1"].hex() != shasum)):
                raise AdmissionError(f"configured-registry integrity evidence is missing or inconsistent: {name}")
            # Legacy registry records have SHA-1 only. Bind the admitted
            # tarball to the lock's strong integrity before any installation.
            with tempfile.TemporaryDirectory(prefix="seasoned-npm-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-")) as temporary:
                packed = subprocess.run(
                    [*command, "pack", f"{name}@{version}", "--ignore-scripts", "--json",
                     "--pack-destination", temporary],
                    cwd=project, capture_output=True, text=True, timeout=30, creationflags=HEADLESS,
                )
                if packed.returncode != 0:
                    raise AdmissionError(f"configured-registry artifact verification failed: {name}")
                records = json.loads(packed.stdout)
                if (not isinstance(records, list) or len(records) != 1
                        or not isinstance(records[0], dict)
                        or records[0].get("shasum") != shasum
                        or not STRONG_ALGORITHMS.intersection(
                            integrity_digests(records[0].get("integrity"))
                        )):
                    raise AdmissionError(f"configured-registry artifact identity is unsupported: {name}")
                dist["integrity"] = records[0]["integrity"]
        return metadata
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise AdmissionError(f"configured-registry metadata unavailable: {name}") from exc


def audit_lock(lock: dict, metadata, *, now: datetime | None = None,
               replace_default_registry: bool = False) -> int:
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:
        raise AdmissionError("age admission requires a timezone-aware clock")
    packages = lock.get("packages")
    if lock.get("lockfileVersion") != 3 or not isinstance(packages, dict) or "" not in packages:
        raise AdmissionError("a complete version-3 package lock is required")
    checked = 0
    for location, package in packages.items():
        if location == "":
            continue
        name = location.rsplit("node_modules/", 1)[-1]
        if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name):
            raise AdmissionError("unsupported locked package identity")
        if not isinstance(package, dict) or package.get("link"):
            raise AdmissionError("only locked registry dependencies are supported")
        version = package.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
            raise AdmissionError(f"missing locked version: {name}")
        release = metadata(name, version)
        times = release.get("time")
        timestamp = times.get(version) if isinstance(times, dict) else None
        if not seasoned(timestamp, now):
            raise AdmissionError(f"missing, future or unseasoned release timestamp: {name}")
        dist = release.get("dist")
        if (not STRONG_ALGORITHMS.intersection(integrity_digests(package.get("integrity")))
                or not isinstance(dist, dict) or dist.get("integrity") != package["integrity"]):
            raise AdmissionError(f"locked bytes differ from configured-registry metadata: {name}")
        resolved = package.get("resolved")
        if not isinstance(resolved, str):
            raise AdmissionError(f"locked source differs from configured-registry metadata: {name}")
        url = urlsplit(resolved)
        if url.scheme not in {"https", "http"} or url.username or url.password:
            raise AdmissionError(f"unsupported locked artifact source: {name}")
        if resolved != dist.get("tarball") and not (
            replace_default_registry and url.hostname == "registry.npmjs.org"
        ):
            raise AdmissionError(f"locked source differs from configured-registry metadata: {name}")
        checked += 1
    if checked == 0:
        raise AdmissionError("the lock contains no dependency closure")
    return checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="locked project to install")
    parser.add_argument("--health-module", action="append", default=[],
                        help="selected npm module to import after installation (repeatable)")
    args = parser.parse_args([] if argv is None else argv)
    project = args.directory.resolve() if args.directory else Path(__file__).resolve().parent
    modules = args.health_module or (["@resvg/resvg-js"] if args.directory is None else [])
    try:
        if not modules or any(
            not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name) for name in modules
        ):
            raise AdmissionError("selected dependency health modules are required")
        command = npm_command()
        originals = {
            name: (project / name).read_bytes()
            for name in ("package.json", "package-lock.json")
        }
        config_path = project / ".npmrc"
        config = config_path.read_bytes() if config_path.exists() else None
        with tempfile.TemporaryDirectory(
            prefix="seasoned-npm-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-"),
            dir=project.parent,
        ) as temporary:
            snapshot = Path(temporary)
            for name, body in originals.items():
                (snapshot / name).write_bytes(body)
            if config is not None:
                (snapshot / ".npmrc").write_bytes(config)
            replacement = subprocess.run(
                [*command, "config", "get", "replace-registry-host"], cwd=snapshot,
                capture_output=True, text=True, timeout=15, check=True, creationflags=HEADLESS,
            ).stdout.strip()
            count = audit_lock(
                json.loads(originals["package-lock.json"]),
                lambda name, version: read_metadata(command, snapshot, name, version),
                replace_default_registry=replacement == "npmjs",
            )
            print(f"Admitted {count} locked dependencies released at least seven days ago.")
            # npm consumes only the retained, admitted manifests, never the
            # concurrently writable project. Lifecycle scripts remain disabled.
            result = subprocess.call(
                [*command, "ci", "--ignore-scripts", "--include=optional", "--strict-peer-deps",
                 "--no-audit"],
                cwd=snapshot, creationflags=HEADLESS,
            )
            if result != 0:
                return result
            result = subprocess.call(
                [command[0], "--input-type=module", "-e",
                 "for (const name of process.argv.slice(1)) await import(name);", *modules],
                cwd=snapshot, creationflags=HEADLESS,
            )
            if result != 0:
                raise AdmissionError("selected dependency health check failed")
            if any(
                (root / name).read_bytes() != body
                for root in (project, snapshot) for name, body in originals.items()
            ) or any(
                (path.read_bytes() if path.exists() else None) != config
                for path in (config_path, snapshot / ".npmrc")
            ):
                raise AdmissionError("package manifest, lock or configuration changed during installation")
            installed = project / "node_modules"
            retained = snapshot / "previous-node-modules"
            if installed.exists():
                installed.rename(retained)
            published = False
            try:
                (snapshot / "node_modules").rename(installed)
                published = True
                if any(
                    (project / name).read_bytes() != body for name, body in originals.items()
                ) or (config_path.read_bytes() if config_path.exists() else None) != config:
                    raise AdmissionError("package manifest, lock or configuration changed during publication")
            except (AdmissionError, OSError):
                if published:
                    installed.rename(snapshot / "node_modules")
                if retained.exists():
                    retained.rename(installed)
                raise
        return 0
    except (AdmissionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"dependency-seasoning: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
