"""Read an explicitly addressed adopter's suite intent without changing policy."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys


def _ordinary(path: Path) -> None:
    if ".." in path.parts:
        raise ValueError("Repository suite paths must not contain parent components")
    for candidate in reversed((path, *path.parents)):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Repository suite policy must not use linked paths: {candidate}")


def _string(value, name: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"Repository suite {name} must be a nonempty string without control characters")
    return value


def _yaml(text: str, parser_root: Path | None) -> dict:
    try:
        import yaml
    except ImportError as error:
        raise ValueError("Repository suite YAML parser is unavailable in the trusted toolkit") from error
    if parser_root is not None and not Path(yaml.__file__).resolve().is_relative_to(
        parser_root.resolve(),
    ):
        raise ValueError("Repository suite YAML parser is not from the prepared toolkit inputs")

    class PolicyError(ValueError):
        pass

    class UniqueLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            if any(key.tag == "tag:yaml.org,2002:merge" for key, _value in node.value):
                raise PolicyError("Repository suite YAML merge keys are unsupported")
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key in result:
                    raise PolicyError(
                        f"Duplicate repository suite configuration key at line {key_node.start_mark.line + 1}"
                    )
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    try:
        loader = UniqueLoader(text)
        try:
            node = loader.get_single_node()
            if node is None:
                return {}
            if not isinstance(node, yaml.MappingNode):
                raise PolicyError("Repository suite configuration must be a mapping")
            value = loader.construct_document(node)
        finally:
            loader.dispose()
    except PolicyError:
        raise
    except (
        yaml.YAMLError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError,
    ) as error:
        mark = getattr(error, "problem_mark", None)
        position = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise ValueError("Invalid repository suite YAML" + position) from None
    if not isinstance(value, dict):
        raise ValueError("Repository suite configuration must be a mapping")
    return value


def read(
    repository_root: Path | None, *, default_repository: str,
    parser_root: Path | None = None,
) -> dict:
    result = {
        "repository_root": None, "declaration": None,
        "repository": default_repository, "requested_reference": None,
        "reference": "main", "fallback_reason": "repository-unavailable",
    }
    if repository_root is None:
        return result
    root = Path(repository_root)
    if not root.is_absolute():
        raise ValueError("Repository suite observation requires an absolute repository root")
    _ordinary(root)
    result["repository_root"] = str(root)
    try:
        root_metadata = root.stat()
    except FileNotFoundError:
        return result
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("Repository suite root is not a directory")
    path = root / ".copilot-extensions" / "config.yaml"
    _ordinary(path)
    result.update(declaration=str(path), fallback_reason="selection-absent")
    try:
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Repository suite configuration must be an ordinary file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError("Repository suite configuration changed during observation")
            content = stream.read(65537)
    except FileNotFoundError:
        return result
    if len(content) > 65536:
        raise ValueError("Repository suite configuration exceeds its 64 KiB input budget")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("Repository suite configuration must be UTF-8") from error
    data = _yaml(text, parser_root)
    if "last_known_good" in data and (
        not isinstance(data["last_known_good"], list)
        or any(not isinstance(item, str) or not item for item in data["last_known_good"])
    ):
        raise ValueError("Repository suite last_known_good must be a list of nonempty references")
    if "current_version" not in data and "repository" not in data:
        return result
    if "repository" in data:
        repository = _string(data["repository"], "repository")
        if repository.replace("\\", "/").startswith("//") or repository.lower().startswith("file:"):
            raise ValueError("Repository suite declarations do not admit network filesystem or file: sources")
        if Path(repository).is_absolute():
            raise ValueError(
                "Repository suite declarations require a remote Git source; local object sources are caller-owned"
            )
        result["repository"] = repository
    if "current_version" in data:
        reference = _string(data["current_version"], "current_version")
        result.update(requested_reference=reference, reference=reference)
    result["fallback_reason"] = None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--default-repository", required=True)
    parser.add_argument("--parser-root", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        _ordinary(arguments.parser_root)
        if not arguments.parser_root.is_absolute() or not arguments.parser_root.is_dir():
            raise ValueError("Prepared repository parser requires an absolute directory")
        sys.path.insert(0, str(arguments.parser_root))
        value = read(
            arguments.repository_root, default_repository=arguments.default_repository,
            parser_root=arguments.parser_root,
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
