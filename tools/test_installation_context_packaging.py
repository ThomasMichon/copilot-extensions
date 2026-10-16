"""Every runtime-loaded fragment must survive ordinary wheel installation."""
import ast
import tomllib
from pathlib import Path


def test_installation_context_wheel_declares_every_loaded_fragment():
    root = Path(__file__).resolve().parents[1] / "libs" / "installation-context"
    tree = ast.parse((root / "installation_context.py").read_text(encoding="utf-8"))
    fragments = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_FRAGMENT_FILES"
                for target in node.targets)
    )
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["setuptools"]["py-modules"]
    assert set(declared) == {"installation_context", *(Path(name).stem for name in fragments)}
    assert all((root / name).is_file() for name in fragments)
