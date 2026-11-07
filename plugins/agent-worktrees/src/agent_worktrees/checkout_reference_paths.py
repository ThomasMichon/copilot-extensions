"""Conservative native-path classification for supported operational references."""

from pathlib import Path


def canonical(value: str | Path) -> Path:
    path = Path(value)
    if str(value).startswith(("\\\\", "//")):
        raise ValueError("network operational references require separate qualification")
    if not path.is_absolute():
        if ".." in path.parts:
            raise ValueError("relative parent-component references are unsupported")
        return path
    return path.resolve()


def under(value: str | Path, root: str | Path) -> bool:
    return canonical(value).is_relative_to(canonical(root))
