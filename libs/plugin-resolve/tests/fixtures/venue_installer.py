"""Independent synthetic native installer; never reaches a network or real home."""

import json
import hashlib
import os
import sys
from pathlib import Path

home = Path(os.environ["COPILOT_HOME"])
assert "copilot-venue-plugins" in home.parts
state = next(parent for parent in home.parents if parent.name == "copilot-venue-plugins")
target = state.parent.parent
settings = target / ".github" / "copilot" / "settings.local.json"
assert not Path.cwd().resolve().is_relative_to(target.resolve()), "native command inherited target overlay cwd"
if os.environ.get("VENUE_EXPECTED_SETTINGS_SHA256"):
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == os.environ["VENUE_EXPECTED_SETTINGS_SHA256"]
else:
    assert not settings.exists()
cache = os.environ.get("COPILOT_CACHE_HOME")
if cache:
    cache = Path(cache)
elif sys.platform == "win32":
    cache = Path(os.environ["LOCALAPPDATA"]) / "copilot"
elif sys.platform == "darwin":
    cache = Path.home() / "Library" / "Caches" / "copilot"
else:
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "copilot"
# Native cache leaf names are opaque; consumers must not infer them from the
# registered marketplace name or remote repository account.
catalog_path = cache / "marketplaces" / "opaque-clone" / ".github" / "plugin" / "marketplace.json"
if os.environ.get("VENUE_CALL_LOG"):
    with Path(os.environ["VENUE_CALL_LOG"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"argv": sys.argv[1:], "cwd": str(Path.cwd())}) + "\n")
if os.environ.get("VENUE_MARKETPLACE_CATALOG"):
    catalog = json.loads(Path(os.environ["VENUE_MARKETPLACE_CATALOG"]).read_text())
else:
    # The synthetic remote repository contains these independent test entries.
    catalog = {
        "name": home.parent.parent.name, "owner": {"name": "Synthetic Marketplace Publisher"},
        "plugins": [{"name": name, "source": f"./plugins/{name}", "version": "2.3.4"}
                    for name in ("remote", "native", "sample", "support", "external", "a", "b")],
    }
native_settings_path = home / "settings.json"
native = json.loads(native_settings_path.read_text()) if native_settings_path.exists() else {}
registered = native.setdefault("extraKnownMarketplaces", {})


def publish_catalog():
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    clone = catalog_path.parents[2]
    for variable, relative in (
        ("VENUE_LEGACY_CATALOG", ".claude-plugin/marketplace.json"),
        ("VENUE_NESTED_CATALOG", "plugins/sample/tests/marketplace.json"),
    ):
        if os.environ.get(variable):
            alternative = clone / relative
            alternative.parent.mkdir(parents=True, exist_ok=True)
            alternative.write_text(Path(os.environ[variable]).read_text(), encoding="utf-8")


if sys.argv[1:3] == ["plugin", "marketplace"]:
    if sys.argv[3] == "add":
        if catalog["name"] in registered:
            print(f'Failed to add marketplace: Marketplace "{catalog["name"]}" already registered',
                  file=sys.stderr)
            raise SystemExit(1)
        location = sys.argv[4]
        definition = {"source": {"source": "git", "url": location}} if "://" in location else {
            "source": {"source": "github", "repo": location},
        }
        registered[catalog["name"]] = definition
        native_settings_path.write_text(json.dumps(native), encoding="utf-8")
    else:
        assert sys.argv[3:] == ["update", catalog["name"]]
        assert catalog["name"] in registered
        if os.environ.get("VENUE_FAIL_REFRESH"):
            print("fatal: synthetic package extraction failed", file=sys.stderr)
            raise SystemExit(1)
    publish_catalog()
    raise SystemExit(0)
assert sys.argv[1:3] == ["plugin", "install"]
publish_catalog()
if os.environ.get("VENUE_FAIL_INSTALL"):
    if os.environ.get("VENUE_PARTIAL_PAYLOAD"):
        name, marketplace = sys.argv[3].split("@")
        partial = home / "installed-plugins" / marketplace / name
        partial.mkdir(parents=True, exist_ok=True)
        (partial / "plugin.json").write_text("{partial", encoding="utf-8")
        (partial / "partial-marker").write_text("retained", encoding="utf-8")
        print("fatal: synthetic package extraction failed", file=sys.stderr)
    else:
        print("fatal: synthetic repository unavailable", file=sys.stderr)
    print("token=SYNTHETIC_SECRET_VALUE", file=sys.stderr)
    raise SystemExit(1)
name, marketplace = sys.argv[3].split("@")
assert marketplace in registered
assert any(item["name"] == name for item in catalog["plugins"])
payload = home / "installed-plugins" / marketplace / name
payload.mkdir(parents=True, exist_ok=True)
manifest = {"name": name, "version": "2.3.4"}
if not os.environ.get("VENUE_OMIT_PLUGIN_AUTHOR"):
    manifest["author"] = {"name": "Synthetic Publisher"}
(payload / "plugin.json").write_text(json.dumps(manifest))
