"""Read-only admission for a state-preserving native runtime uninstall."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

PLUGIN = "agent-pull-requests"
ENTRIES = (
    ".venv", "versions", "snapshots", "current-version", "last-known-good",
    "payload-dir", "stamped-version", "deploy-manifest.json",
)
HELPERS = (
    "resolve-runtime.ps1", "resolve-runtime.sh", "bootstrap-check.ps1", "bootstrap-check.sh",
    "bootstrap-killswitch-guard.ps1", "bootstrap-killswitch-guard.sh",
)


def real_path(path: Path) -> None:
    """Reject lexical traversal and indirection before reading ownership."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Uninstall requires an absolute, traversal-free install root")
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Uninstall refuses linked/reparse install ancestry")
    if not path.is_dir() or path == Path(path.anchor) or Path.home().is_relative_to(path):
        raise ValueError("Uninstall refuses a filesystem root, user home, or non-directory")
    info = path.stat()
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise ValueError("Uninstall requires exclusive operator custody of the install root")


def document(path: Path) -> dict:
    """Read an ordinary, singly owned JSON object, never a redirected marker."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Uninstall refuses a linked or non-file ownership marker")
    if getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Uninstall refuses a reparse ownership marker")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Uninstall requires object ownership metadata")
    return value


def admit(root: Path) -> None:
    """Validate the deployment owner without opening durable watcher state."""
    real_path(root)
    lock = root / ".install.lock"
    if os.path.lexists(lock):
        info = lock.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Uninstall refuses linked installer-lock custody")
    bin_dir = Path.home() / ".local" / "bin"
    for suffix in ("", ".ps1", ".cmd"):
        stub = bin_dir / (PLUGIN + suffix)
        if not os.path.lexists(stub):
            continue
        real_path(bin_dir)
        info = stub.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Uninstall refuses linked binstub custody")
        owner = bin_dir / (PLUGIN + ".ps1") if suffix == ".cmd" else stub
        content = owner.read_text(encoding="utf-8-sig").replace("\\", "/").casefold()
        if root.as_posix().casefold() not in content:
            raise ValueError("Uninstall refuses foreign binstub ownership")
    if os.path.lexists(root / "dev-claim.json"):
        raise ValueError("Uninstall requires explicit development-owner retirement")
    for name in ("install.json", "runtime-deployment.json"):
        if os.path.lexists(root / name):
            raise ValueError("Legacy uninstall refuses installation-cell or managed-deployment ownership")
    manifest = root / "deploy-manifest.json"
    if manifest.exists() or manifest.is_symlink():
        data = document(manifest)
        source = data.get("source")
        if (
            data.get("service") != PLUGIN or not isinstance(source, dict)
            or source.get("plugin") != PLUGIN
        ):
            raise ValueError("Uninstall refuses foreign deployment ownership")
        venv = Path(data.get("venv", ""))
        if not venv.is_absolute() or ".." in venv.parts or not venv.is_relative_to(root):
            raise ValueError("Uninstall refuses a foreign runtime path")
    elif any(os.path.lexists(root / entry) for entry in ENTRIES if entry not in ("versions", "snapshots")) or any(
        child.is_dir() and child.name[:1].isdigit()
        for name in ("versions", "snapshots") if (root / name).is_dir()
        for child in (root / name).iterdir()
    ):
        marker = root / "payload-dir"
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Uninstall requires ordinary stamped ownership")
        payload = Path(marker.read_text(encoding="utf-8-sig").strip())
        real_path(payload)
        if not payload.is_relative_to(root / "snapshots") or document(payload / "plugin.json").get("name") != PLUGIN:
            raise ValueError("Uninstall refuses foreign stamped ownership")


def main() -> int:
    """Keep canonical custody alive through the finite native cleanup."""
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("--shell", required=True)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    try:
        scripts = Path(__file__).resolve().parent
        helper = scripts / "installer_engine_uninstall.py"
        if not helper.is_file():
            helper = scripts.parents[2] / "libs" / "installer-engine" / helper.name
        spec = importlib.util.spec_from_file_location("native_uninstall_engine", helper)
        if spec is None or spec.loader is None:
            raise RuntimeError("Canonical uninstall engine unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result = module.main([
            "--payload-root", str(scripts.parent), "--plugin-id", PLUGIN,
            "--root", args.root, "--shell", args.shell,
            "--profile", str(scripts / "uninstall-profile.json"),
            "--check" if args.preview else "--remove-runtime",
        ])
        if result:
            return result
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"{PLUGIN}: {exc}", file=sys.stderr)
        return 1
    print("[dry-run] Uninstall preview complete" if args.preview else "Owned runtime removed; durable state retained")
    return 0


def check_under_custody(root: Path, payload_root: Path, plugin_id: str) -> None:
    """Bind this data-retaining profile to its own trusted payload."""
    if plugin_id != PLUGIN or payload_root.resolve() != Path(__file__).resolve().parents[1]:
        raise RuntimeError("Foreign uninstall guard ownership")


if __name__ == "__main__":
    raise SystemExit(main())
