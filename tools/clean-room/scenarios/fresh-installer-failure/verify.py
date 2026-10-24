"""Verify real fresh-install artifacts; never emit or repair lifecycle records."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path


def verify(logs: Path, runtime: Path, plugin: str, commit: str) -> dict[str, object]:
    """Reject unrelated failures and success-shaped remnants before crediting proof."""
    rc = int((logs / "installer.exit").read_text(encoding="utf-8"))
    pid = int((logs / "installer.pid").read_text(encoding="utf-8"))
    assert 0 < rc < 124 and pid > 1, "not a native, nonsignal installer failure"
    prepared = json.loads((logs / "prepared-source.json").read_text(encoding="utf-8"))
    identity = prepared["identity"]
    assert identity["source_commit"] == commit, "prepared source commit mismatch"
    generation = identity["runtime_version"]
    assert commit in generation, "prepared runtime is not source-qualified"
    output = (logs / "source-install.log").read_text(encoding="utf-8")
    assert "[OK]   Venv created" in output, "installer did not create its interpreter"
    assert re.search(
        r"^(?:error:|  cause:) Failed to fetch: "
        r"https://packagefeedproxy\.microsoft\.io/pypi/simple/[a-z0-9-]+/$",
        output, re.MULTILINE,
    ) and "Connection refused (os error 111)" in output, (
        "installer log does not identify the isolated dependency-feed failure"
    )
    events = [
        json.loads(line)
        for line in (logs / "lifecycle.log").read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == 2, "expected exactly two fresh lifecycle records"
    for event, outcome in zip(events, ("begin", "fail"), strict=True):
        assert event["service"] == plugin and event["action"] == "install"
        assert event["outcome"] == outcome, "missing, reordered or successful terminal record"
        assert type(event["pid"]) is int and event["pid"] == pid, "wrong installer PID"
        assert event["node"], "lifecycle node is absent"
        stamp = datetime.fromisoformat(event["ts"])
        assert stamp.tzinfo is not None, "lifecycle timestamp is not timezone-aware"
    assert events[0]["version"] == identity["package_version"]
    assert events[1]["version"] == generation
    assert datetime.fromisoformat(events[0]["ts"]) <= datetime.fromisoformat(events[1]["ts"])
    for relative in (
        "current-version", "last-known-good", "deploy-manifest.json",
        "active.json", "run/endpoint.json", "running-version.json", "venv",
    ):
        path = runtime / relative
        assert not path.exists() and not path.is_symlink(), f"unexpected publication: {relative}"
    assert not list(runtime.glob("versions/**/.install-complete.json")), (
        "failed installer published a completed runtime"
    )
    return {
        "plugin": plugin,
        "source_commit": commit,
        "runtime_generation": generation,
        "installer_pid": pid,
        "installer_exit": rc,
        "failure_kind": "dependency-feed-unreachable",
        "lifecycle_outcomes": [event["outcome"] for event in events],
        "published_runtime": False,
    }


def main() -> None:
    logs, runtime = Path(sys.argv[1]), Path(sys.argv[2])
    result = verify(logs, runtime, sys.argv[3], sys.argv[4])
    rendered = json.dumps(result, indent=2) + "\n"
    (logs / "failure-witness.json").write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
