"""Conservative admission of coverage-based test exclusions."""
from __future__ import annotations

import hashlib
import platform
from pathlib import Path, PurePosixPath
import sys

MARKER = "coverage_auditability"


def declaration(args: tuple, kwargs: dict) -> dict:
    if len(args) != 1 or args[0] not in ("auditable", "unauditable"):
        raise ValueError("coverage_auditability requires auditable or unauditable")
    if set(kwargs) - {"dependencies"}:
        raise ValueError("unknown coverage_auditability arguments")
    dependencies = kwargs.get("dependencies", ())
    if not isinstance(dependencies, (list, tuple)) or any(
        not isinstance(path, str) or not path or "\\" in path
        or PurePosixPath(path).is_absolute()
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(part.casefold() in ("", ".", "..", ".git") or ":" in part for part in path.split("/"))
        for path in dependencies
    ) or len(dependencies) != len(set(dependencies)):
        raise ValueError("audit dependencies must be unique repository-relative files")
    if args[0] == "auditable" and not dependencies:
        raise ValueError("auditable tests require a complete dependency declaration")
    return {"classification": args[0], "dependencies": sorted(dependencies)}


def profile(plugin: str, cov_source: str) -> dict:
    return {
        "kind": "python-inprocess/v2", "plugin": plugin, "cov_source": cov_source,
        "platform": sys.platform, "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "architecture": platform.machine(), "optimization": sys.flags.optimize,
    }


def file_digest(root: Path, name: str) -> str:
    path = root / name
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("audit dependency is missing or outside the source root")
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()

def dependency_names(root: Path, node: str, declared: dict, plugin: str = "") -> set[str]:
    test_file = node.split("::", 1)[0]
    if not (root / test_file).is_file() and plugin:
        test_file = f"plugins/{plugin}/{test_file}"
    names = set(declared["dependencies"]) | {test_file}
    parent = (root / test_file).parent
    while parent.is_relative_to(root):
        for name in ("conftest.py", "pyproject.toml", "pytest.ini", "setup.cfg", "uv.lock"):
            path = parent / name
            if path.is_file():
                names.add(path.relative_to(root).as_posix())
        if parent == root:
            break
        parent = parent.parent
    return names


def evidence(
    root: Path, node: str, metadata: dict, coverage: dict, current_profile: dict,
) -> dict | None:
    """Bind a reviewed closure to ordinary passing in-process coverage."""
    if metadata.get("portfolio_tier") not in ("T0", "T1"):
        return None
    if set(metadata.get("effects", ())) - {"filesystem"}:
        return None
    raw = metadata.get("auditability")
    if not isinstance(raw, dict) or raw.get("classification") != "auditable":
        return None
    try:
        declared = declaration(("auditable",), {"dependencies": raw.get("dependencies")})
        sources = {
            name for name in declared["dependencies"]
            if name.endswith(".py")
        }
        observed = {
            name for name, lines in coverage.items()
            if any(node in contexts for contexts in lines.values())
        }
        if not sources or sources != observed or not any(
            name == current_profile["cov_source"]
            or name.startswith(current_profile["cov_source"].rstrip("/") + "/")
            for name in sources
        ):
            return None
        names = dependency_names(root, node, declared, current_profile["plugin"])
        digests = {name: file_digest(root, name) for name in sorted(names)}
        if digests != metadata.get("audit_dependency_digests"):
            return None
        return {
            "profile": current_profile, "declaration": declared,
            "dependencies": digests,
        }
    except (OSError, ValueError, TypeError):
        return None


def baseline_exclusions(root: Path, baseline: dict, current_profile: dict, eligible: frozenset[str]) -> dict:
    if baseline.get("schema_version") != 4 or baseline.get("auditability_profile") != current_profile:
        return {}
    candidates = {}
    coverage = baseline.get("coverage", {})
    tests = baseline.get("tests", {})
    if not isinstance(coverage, dict) or not isinstance(tests, dict):
        return {}
    for node, metadata in tests.items():
        if node not in eligible or not isinstance(metadata, dict):
            continue
        proof = metadata.get("auditability_evidence")
        effects = metadata.get("effects", ())
        if (
            not isinstance(proof, dict) or proof.get("declaration") != metadata.get("auditability")
            or metadata.get("portfolio_tier") not in ("T0", "T1")
            or not isinstance(effects, (list, tuple))
            or any(effect != "filesystem" for effect in effects)
        ):
            continue
        declared = proof.get("declaration", {})
        if not isinstance(declared, dict) or declared.get("classification") != "auditable":
            continue
        try:
            declared = declaration(("auditable",), {"dependencies": declared.get("dependencies")})
        except ValueError:
            continue
        sources = {
            name for name in declared.get("dependencies", ())
            if name.endswith(".py")
        }
        observed = {
            name for name, lines in coverage.items()
            if isinstance(lines, dict) and any(
                isinstance(contexts, list) and node in contexts for contexts in lines.values()
            )
        }
        if sources and sources == observed and all(
            isinstance(coverage.get(name), dict)
            and any(isinstance(contexts, list) and node in contexts for contexts in coverage[name].values())
            for name in sources
        ):
            candidates[node] = proof
    return verified_exclusions(root, candidates, current_profile)


def verified_exclusions(
    root: Path, candidates: dict, current_profile: dict, *, cache: dict | None = None,
) -> dict:
    """Unknown, incomplete, cross-profile or changed evidence grants no exclusion."""
    if (
        not isinstance(current_profile, dict)
        or set(current_profile) != {
            "kind", "plugin", "cov_source", "platform", "python",
            "implementation", "architecture", "optimization",
        }
        or current_profile.get("kind") != "python-inprocess/v2"
    ):
        return {}
    digests = {} if cache is None else cache
    accepted = {}
    for node, proof in candidates.items():
        if not isinstance(node, str) or not isinstance(proof, dict):
            continue
        if set(proof) != {"profile", "declaration", "dependencies"} or proof["profile"] != current_profile:
            continue
        deps = proof["dependencies"]
        raw = proof["declaration"]
        try:
            if not isinstance(raw, dict) or raw.get("classification") != "auditable":
                continue
            declared = declaration(("auditable",), {"dependencies": raw.get("dependencies")})
            if not isinstance(deps, dict) or not deps or not (
                dependency_names(root, node, declared, current_profile["plugin"])
            ) <= set(deps):
                continue
            declaration(("auditable",), {"dependencies": list(deps)})
            for name in deps:
                if name not in digests:
                    digests[name] = file_digest(root, name)
            if any(digests[name] != digest for name, digest in deps.items()):
                continue
            accepted[node] = proof
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return accepted
