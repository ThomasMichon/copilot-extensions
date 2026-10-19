"""Native-command stand-in consuming structured settings before real local Git acquisition."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

assert sys.argv[1:3] == ["plugin", "install"], "URI marketplace add must not be used"
home = Path(os.environ["COPILOT_HOME"])
assert "copilot-venue-plugins" in home.parts
name, marketplace = sys.argv[3].split("@")
settings = json.loads((home / "settings.json").read_text())
definition = settings["extraKnownMarketplaces"][marketplace]
origin = definition["source"]
assert origin["source"] in {"git", "github", "git-subdir", "url"}
assert origin["url"].startswith("file://"), "fixture must never access a network"
clone = Path.cwd() / "acquired"


def git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()


git("clone", "--quiet", origin["url"], str(clone))
revision = origin.get("sha") or origin.get("ref")
if revision:
    git("-C", str(clone), "checkout", "--quiet", revision)
root = (clone / origin.get("path", ".")).resolve()
root.relative_to(clone.resolve())
catalog = json.loads((root / "marketplace.json").read_text())
assert catalog["name"] == marketplace
entry = next(entry for entry in catalog["plugins"] if entry["name"] == name)
payload = (root / entry["source"]).resolve()
payload.relative_to(root)
destination = home / "installed-plugins" / marketplace / name
shutil.copytree(payload, destination, dirs_exist_ok=True)
cache = Path(os.environ["COPILOT_CACHE_HOME"]) / "marketplaces" / "opaque" / ".github" / "plugin"
cache.mkdir(parents=True)
(cache / "marketplace.json").write_text(json.dumps(catalog))
if os.environ.get("STRUCTURED_GIT_LOG"):
    Path(os.environ["STRUCTURED_GIT_LOG"]).write_text(json.dumps({
        "definition_before_acquisition": definition,
        "acquired_commit": git("-C", str(clone), "rev-parse", "HEAD"),
        "payload": (destination / "marker.txt").read_text(),
        "private_cache": os.environ["COPILOT_CACHE_HOME"],
    }))
