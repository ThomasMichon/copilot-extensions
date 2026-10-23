"""Fixed construction participant; native admission owns progress and completion."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).absolute().parent.parent
SCRIPTS = SOURCE / "scripts"
sys.path[:0] = [
    str(SCRIPTS), str(SOURCE / "src"), str(SOURCE.parent / "agent-procutil" / "src"),
    str(SOURCE.parent / "single-instance-lease" / "src"),
]

from update_driver import borrowed_custody  # noqa: E402
from update_driver import foundation_state  # noqa: E402
from update_driver.foundation_inputs import FoundationInputError, inspect  # noqa: E402


def construct(
    *, root: Path, source_hash: str, uv: Path, base_python: Path,
    admitted_source: Path | None = None,
) -> Path:
    custody = borrowed_custody(root=root)
    if custody is None:
        raise FoundationInputError("foundation construction requires live installation custody")
    with custody:
        custody.assert_owned()
        operation = foundation_state.read(root)
        if operation is None or operation.state != "running" or operation.source_hash != source_hash:
            raise FoundationInputError("foundation construction does not match admitted native progress")
        selected = inspect(SOURCE)
        origin = SOURCE if admitted_source is None else admitted_source
        if selected.digest != source_hash or inspect(origin).digest != source_hash:
            raise FoundationInputError("foundation source changed after native admission")
        # Only this source's checked and packaged canonical helpers are imported.
        import installer_engine as engine
        import versioned_runtime as runtime

        if engine._package_roots(SOURCE) != selected.packages:
            raise FoundationInputError("canonical construction resolved a different foundation closure")
        target = root / foundation_state.ARTIFACT_DIRECTORY
        if target.resolve() != target:
            raise FoundationInputError("foundation target must not traverse a link")
        target.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="inputs-", dir=target) as temporary:
            frozen = engine.freeze_inputs(SOURCE, Path(temporary) / "source")
            if (
                inspect(frozen.plugin).digest != source_hash or inspect(SOURCE).digest != source_hash
                or inspect(origin).digest != source_hash
            ):
                raise FoundationInputError("foundation source changed while freezing inputs")
            custody.assert_owned()
            version = "foundation-" + source_hash
            with engine.numbered_candidate(
                target, version, plugin=frozen.plugin, uv=uv, base_python=base_python,
                health_module="update_driver.foundation_inputs", health_callable="check_health",
                process_retention=True,
            ) as candidate:
                custody.assert_owned()
                if (
                    foundation_state.read(root) != operation
                    or inspect(frozen.plugin).digest != source_hash
                    or inspect(SOURCE).digest != source_hash
                    or inspect(origin).digest != source_hash
                ):
                    raise FoundationInputError("foundation admission changed before selection")
                runtime.activate(target, version, link_free=True)
                return candidate.slot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--source-hash", required=True)
    parser.add_argument("--uv", required=True, type=Path)
    parser.add_argument("--base-python", required=True, type=Path)
    parser.add_argument("--admitted-source", type=Path)
    args = parser.parse_args()
    construct(
        root=args.root, source_hash=args.source_hash, uv=args.uv, base_python=args.base_python,
        admitted_source=args.admitted_source,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
