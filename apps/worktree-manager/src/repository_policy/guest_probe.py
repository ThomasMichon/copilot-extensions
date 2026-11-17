"""Transport the canonical read-only evaluator to an unprovisioned receiver."""

from __future__ import annotations

import io
from importlib.metadata import distribution
import os
from pathlib import Path
from typing import cast
import zipfile

import yaml

from . import RepositoryPolicyError


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
                archive.write(
                    cast(os.PathLike[str], package.locate_file(notice)),
                    f"licenses/{name}-{index}.txt",
                )
    return Path(__file__).with_name("_guest_reader.py").read_text(encoding="utf-8"), payload.getvalue()
