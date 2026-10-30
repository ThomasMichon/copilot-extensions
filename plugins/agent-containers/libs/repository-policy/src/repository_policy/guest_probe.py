"""Transport the canonical read-only evaluator to an unprovisioned receiver."""

from __future__ import annotations

import io
from importlib.metadata import distribution
from pathlib import Path
import zipfile

import yaml

from . import RepositoryPolicyError

_READER = """\
import json, os, sys, tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="repository-policy-probe-") as directory:
    archive = Path(directory) / "policy.zip"
    payload = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise SystemExit("Repository policy probe exceeds its transport limit")
    archive.write_bytes(payload)
    sys.path.insert(0, str(archive))
    from repository_policy import AgentHostingError, RepositoryPolicyError, require_agent_hosting
    try:
        if os.environ.get("COPILOT_EXTENSIONS_CONTEXT"):
            raise RepositoryPolicyError(
                "Fresh repository policy probe cannot validate an explicit installation context; "
                "use its selected receiver"
            )
        policy = require_agent_hosting(sys.argv[1])
    except AgentHostingError as error:
        print(json.dumps({"version": 1, "code": error.code, "error": str(error)}))
        raise SystemExit(3)
    print(json.dumps({**policy.to_dict(), "allowed": True}))
"""


def build_guest_policy_probe() -> tuple[str, bytes]:
    """Return Python source and a stdin archive; never install a guest package.

    The receiving Python runs with -I -S, so only this canonical resolver and
    PyYAML's portable source are used, not guest site packages or a second parser.
    """
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(Path(__file__).with_name("__init__.py"), "repository_policy/__init__.py")
        root = Path(yaml.__file__).parent
        for source in sorted(root.glob("*.py")):
            archive.write(source, "yaml/" + source.name)
        for name in ("agent-repository-policy", "PyYAML"):
            package = distribution(name)
            notices = [
                path for path in package.files or ()
                if path.name == "LICENSE" and ".dist-info" in str(path)
            ]
            if not notices:
                raise RepositoryPolicyError(f"Repository policy probe requires the {name} license")
            for index, notice in enumerate(notices):
                archive.write(package.locate_file(notice), f"licenses/{name}-{index}.txt")
    return _READER, payload.getvalue()
