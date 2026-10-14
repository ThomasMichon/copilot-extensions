"""Retire only a disposable helper_closure_probe consumer and its isolated cache."""

import argparse
import json
from pathlib import Path

from helper_closure_probe import packages, python_at, require, run


PROBE = """
import hashlib
import json
from pathlib import Path
import sys
import zdd
from zdd.routing import read_table
if not Path(zdd.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
    raise RuntimeError("ZDD module escapes runtime")
root = Path(sys.argv[1])
for consumer in ("alpha", "beta"):
    if read_table(root / consumer, strict=True) != {"fixture": consumer}:
        raise RuntimeError("routing fixtures crossed consumer boundary")
print(hashlib.sha256(Path(zdd.__file__).read_bytes()).hexdigest())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    cache, first, second = (
        root / name for name in ("uv-cache", "agent-containers", "agent-bridge")
    )
    report = json.loads((root / "closure-results.json").read_text(encoding="utf-8"))
    require(report.get("same_distribution_closures_as_reference") is True,
            "retirement requires an accepted closure-parity report")
    require(cache.is_dir() and first.is_dir() and second.is_dir(),
            "disposable experiment directories are missing")
    require(not any(path.is_symlink() for path in (cache, first, second)),
            "disposable experiment directories are linked")
    require(all(path.resolve().parent == root for path in (cache, first, second)),
            "disposable experiment directories escape selected root")
    modules = report["consumers"][1]["required_modules"]
    first_modules = report["consumers"][0]["required_modules"]
    require(bool(modules) and bool(first_modules), "required module inventories are missing")
    expected = packages(second, modules)
    require(expected == report["consumers"][1]["packages"], "second closure changed")
    first_packages = packages(first, first_modules)
    require(first_packages == report["consumers"][0]["packages"], "first closure changed")
    fixture = root / "routing-fixtures"
    fixture.mkdir()
    for consumer in ("alpha", "beta"):
        directory = fixture / consumer
        directory.mkdir()
        (directory / "active.json").write_text(
            json.dumps({"fixture": consumer}), encoding="utf-8"
        )
    before, _ = run([python_at(second), "-I", "-B", "-c", PROBE, fixture], cwd=root)
    run([
        args.uv, "--cache-dir", cache, "pip", "uninstall",
        "--python", python_at(first), *[item["name"] for item in first_packages],
    ])
    require(packages(first) == [], "first consumer still has distributions")
    run([args.uv, "--cache-dir", cache, "cache", "clean"])
    require(packages(second, modules) == expected, "remaining closure changed after retirement")
    after, _ = run([python_at(second), "-I", "-B", "-c", PROBE, fixture], cwd=root)
    require(before == after, "remaining ZDD module changed after retirement")
    output, elapsed = run(
        [python_at(second), "-I", "-B", "-m", "agent_bridge", "--help"], cwd=root
    )
    require("usage" in output.lower(), "remaining actual CLI did not return help")
    report["consumer_and_cache_retirement"] = {
        "first_consumer_distribution_count_after_uninstall": 0,
        "remaining_consumer_distribution_count": len(expected),
        "remaining_closure_unchanged": True,
        "zdd_module_sha256_unchanged": before.strip(),
        "two_distinct_routing_fixtures_preserved": True,
        "remaining_cli_help_seconds": elapsed,
    }
    (root / "lifetime-results.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["consumer_and_cache_retirement"]))


if __name__ == "__main__":
    main()
