"""Borrow the immutable runtime SDK's selector custody for cold retirement."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from functools import lru_cache
import importlib.util
from pathlib import Path
import stat


class SelectionCustodyUnavailable(RuntimeError):
    """Selection cannot be held against activation or rollback."""


@lru_cache(maxsize=1)
def _retained_leases(helper: Path):
    spec = importlib.util.spec_from_file_location("_vault_retained_runtime_leases", helper)
    if spec is None or spec.loader is None:
        raise ImportError("Retained selector custody is unavailable")
    module = importlib.util.module_from_spec(spec)
    exec(compile(helper.read_bytes(), str(helper), "exec"), module.__dict__)
    return module


@contextmanager
def selection_custody(root: Path, slot: Path):
    with ExitStack() as custody:
        try:
            root, slot = root.absolute(), slot.absolute()
            if slot.parent != root / "versions":
                raise ValueError("Cold retirement requires an immutable runtime slot")
            bootstrap = slot / ".runtime-bootstrap"
            helper = bootstrap / "versioned_runtime_leases.py"
            for path in (root, slot.parent, slot, bootstrap, helper):
                info = path.lstat()
                ordinary = stat.S_ISREG(info.st_mode) if path == helper else stat.S_ISDIR(info.st_mode)
                if (
                    not ordinary or path.resolve() != path
                    or getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    raise ValueError("Retained selector custody requires ordinary paths")
            custody.enter_context(_retained_leases(helper)._publication_lease(root, timeout=0))
        except (OSError, RuntimeError, ValueError, ImportError, AttributeError, SyntaxError, TypeError) as error:
            raise SelectionCustodyUnavailable("Retained selector custody is unavailable") from error
        yield
