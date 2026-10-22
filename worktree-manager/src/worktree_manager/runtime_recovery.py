"""Complete adopted recovery under the constructed Manager's own interpreter."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def verify_adoption(path: Path, root: Path) -> None:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema") != "copilot-extensions.manager-adoption"
        or type(evidence.get("version")) is not int or evidence["version"] != 1
        or evidence.get("root") != str(root)
    ):
        raise RuntimeError("Recovery adoption does not identify this Manager root")
    registry = Path(evidence["registry"])
    if registry.is_symlink() or registry.resolve() != registry.absolute():
        raise RuntimeError("Manager provider registry became indirect")
    if sorted(item.name for item in registry.glob("*.json")) != evidence["registrations"]:
        raise RuntimeError("Manager provider registrations changed before publication")
    for name, expected in evidence["evidence"]:
        item = Path(name)
        if item.is_symlink() or item.resolve() != item.absolute():
            raise RuntimeError(f"Manager adoption path became indirect: {item}")
        try:
            data = item.read_bytes()
        except FileNotFoundError:
            data = None
        observed = hashlib.sha256(data).hexdigest() if data is not None else None
        if observed != expected:
            raise RuntimeError(f"Manager adoption changed before publication: {item}")


def main(argv: list[str] | None = None) -> int:
    from . import mux_daemon_cutover, self_install

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--reused", action="store_true")
    args = parser.parse_args(argv)
    slot = Path(sys.prefix).resolve()
    root = args.root.resolve()
    if slot.parent != root / "versions" or not (slot / ".runtime-bootstrap").is_dir():
        raise RuntimeError("Adopted activation must execute from its constructed Manager slot")
    verify_adoption(args.evidence, root)

    def cutover(target: Path) -> None:
        result = mux_daemon_cutover.activate_after_update(root=root, slot=target, version=target.name)
        if result.get("action") in ("error", "refused") or result.get("result", {}).get("ok") is False:
            raise RuntimeError(f"Adopted Manager cutover failed: {result}")

    payload = slot / "payload"
    previous = self_install.current_version(root)
    version = self_install.payload_version(payload)
    if version is None:
        raise RuntimeError("Constructed Manager has no package version")
    result = self_install._complete_candidate(
        SimpleNamespace(slot=slot, reused=args.reused), payload, root,
        previous=previous, was_current=not self_install.needs_install(version, root),
        before_publish=lambda: verify_adoption(args.evidence, root),
        after_activate=cutover, transactional_activation=True,
    )
    print(json.dumps({"status": result.action, "slot": result.slot, "version": result.version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
