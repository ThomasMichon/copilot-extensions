"""Retain exact historical Git checkpoints before local-only CI validation."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from agent_bridge_contract_git import clean_git_environment, require_local_object_reads
import agent_bridge_contract_registry as registry_codec

PACKET = "plugins/agent-bridge/contract/fixtures/http/current/http47-owned-cold-drain.json"
PACKETS = (
    PACKET,
    "plugins/agent-bridge/contract/fixtures/http/current/http48-retained-inbox.json",
    "plugins/agent-bridge/contract/fixtures/http/current/http49-retained-producer.json",
    "plugins/agent-bridge/contract/fixtures/http/current/http50-retained-recovery.json",
    "plugins/agent-bridge/contract/fixtures/http/current/http51-standard-native-send.json",
)


def _run(root: Path, *arguments: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root),
         "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", *arguments],
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=timeout,
        env=clean_git_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def prepare(root: Path, remote: str) -> str:
    """Prepare exact metadata locally or from the CI-selected repository.

    The validator remains read-only and verifies the frozen source closure.
    This preparation neither changes the worktree nor refreshes capture evidence.
    """
    parsed = urlsplit(remote)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment
        or not parsed.path.endswith(".git") or any(character.isspace() for character in remote)
    ):
        raise ValueError("capture preparation requires a credential-free HTTPS repository URL")
    commits = []
    for relative in PACKETS:
        packet = json.loads((root / relative).read_text(encoding="utf-8"))
        captured = packet.get("captured_from") if isinstance(packet, dict) else None
        commit = captured.get("commit") if isinstance(captured, dict) else None
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("capture preparation requires an exact full commit identity")
        if commit not in commits:
            commits.append(commit)
    require_local_object_reads()
    for commit in commits:
        if _run(root, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
            from bridge_capture_metadata import load
            if load(root, commit) is not None:
                continue
            result = _run(
                root, "fetch", "--no-tags", "--depth=1", "--no-recurse-submodules",
                "--no-auto-maintenance", "--no-write-fetch-head",
                remote, commit, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError("could not retain the exact capture checkpoint from the selected repository")
            if _run(root, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
                raise RuntimeError("capture checkpoint is still unavailable after preparation")

        def read(relative: str) -> bytes:
            result = _run(root, "cat-file", "blob", f"{commit}:{relative}")
            if result.returncode:
                raise ValueError(f"captured contract metadata is unavailable: {relative}")
            return result.stdout.encode("utf-8")

        registry_codec.load(root, read=read)
    return commits[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--remote", help="CI-selected HTTPS repository URL")
    mode.add_argument("--retain-metadata", metavar="COMMIT",
                      help="Preserve exact local capture registry and its Git object proof")
    arguments = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        if arguments.retain_metadata:
            from bridge_capture_metadata import retain
            snapshot = retain(root, arguments.retain_metadata)
            print(f"Retained {len(snapshot.inputs)} exact capture metadata input(s)")
            return 0
        commit = prepare(root, arguments.remote)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"prepare-bridge-contract-evidence: FAILED: {error}", file=sys.stderr)
        return 1
    print(f"Captured Bridge evidence metadata ready: HTTP-47/48/49/50/51 (primary {commit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
