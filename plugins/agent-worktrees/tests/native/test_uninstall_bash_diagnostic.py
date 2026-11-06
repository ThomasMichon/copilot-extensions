"""Temporary copied-fixture diagnostic at the original rooted refusal."""
import json
import pytest

from test_native_uninstall_footprint import (
    _host, _native_arguments, _preserved, _run, native_install as _native_install,
)

native_install = _native_install


def test_bash_controller_diagnostic(native_install):
    home, payload, root, _, durable = native_install
    helper = payload / "scripts/installer_engine_uninstall.py"
    source = helper.read_text(encoding="utf-8")
    marker = '            raise UninstallRefused(f"Active rooted runtime consumer (pid={row[\'pid\']})")'
    assert source.count(marker) == 1
    diagnostic = '''
            def safe_args(row):
                result = []
                for value in _arguments(row):
                    normalized = re.sub(r"^/(?:cygdrive/)?([A-Za-z])/", r"\\1:/", value)
                    path = Path(normalized)
                    if path.is_absolute() and path.is_relative_to(Path.home()):
                        result.append("<home>/" + path.relative_to(Path.home()).as_posix())
                    elif value in {"-I", "-S", "-B", "-X", "utf8", "--", "--teardown",
                                   "--run-lifecycle", "uninstall", "--install-dir"}:
                        result.append(value)
                    elif path.is_absolute() and len(value) < 260:
                        result.append("<external-path>")
                    else:
                        result.append("<value>")
                return result
            facts = [{
                "pid": candidate["pid"], "ppid": candidate["ppid"],
                "image": Path(candidate["image"]).name,
                "refused": candidate["pid"] == row["pid"],
                "admitted": candidate["pid"] in controllers | redirectors | gates,
                "inspector": candidate["pid"] == os.getpid(),
                "inspection_host": candidate.get("inspectionHost", False),
                "inspector_tail": _arguments(candidate)[-len(sys.argv):] == sys.argv,
                "parent_match": _parent(candidate["pid"], candidate["image"], candidate["command"],
                                        payload, root, plugin, candidate["pid"], arguments=candidate.get("argv")),
                "argv": safe_args(candidate),
            } for candidate in rows if prefix in candidate["command"].replace("\\\\", "/").casefold()
               or prefix in " ".join(_argv(candidate["command"], candidate["image"])).replace("\\\\", "/").casefold()]
            (Path.home() / "controller-diagnostic.json").write_text(json.dumps(facts), encoding="utf-8")
'''
    helper.write_text(source.replace(marker, diagnostic + marker), encoding="utf-8")
    executable = _host("bash")
    for _ in range(2):
        result = _run(_native_arguments("bash", executable, payload, root), home)
        if result.returncode:
            break
    _preserved(durable)
    report = home / "controller-diagnostic.json"
    assert report.exists(), result.stdout + result.stderr
    pytest.fail(json.dumps(json.loads(report.read_text()), indent=2), pytrace=False)
