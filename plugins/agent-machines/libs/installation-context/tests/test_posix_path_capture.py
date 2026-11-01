"""Bash controls with synthetic providers, not native filename qualification."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "installation-context.sh"


def _bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    root = Path(git).resolve().parents[1]
    for parts in (("bin", "bash.exe"), ("usr", "bin", "bash.exe")):
        candidate = root.joinpath(*parts)
        if candidate.is_file():
            return str(candidate)
    return None


def _function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    return source[start : source.index("\n}\n", start) + 3]


class PosixPathCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bash = _bash()
        if cls.bash is None:
            raise unittest.SkipTest("A native Bash or Git Bash is required")
        source = SCRIPT.read_text(encoding="utf-8")
        names = [
            "fail",
            "canonical_split_path",
            "canonical_missing_path",
            "canonical_path",
            "paths_equal",
            "path_is_within",
        ]
        for optional in (
            "path_output_into",
            "canonical_path_into",
            "dirname_into",
            "basename_into",
        ):
            if f"{optional}() {{" in source:
                names.append(optional)
        cls.functions = "\n".join(_function(source, name) for name in names)

    def _run(self, body: str, provider: str = "realpath") -> subprocess.CompletedProcess[str]:
        providers = """
fixture_canonical() {
    local argument value=""
    for argument in "$@"; do value="$argument"; done
    printf '%s\\n' "$value"
}
"""
        if provider == "realpath":
            providers += "realpath() { fixture_canonical \"$@\"; }\n"
        elif provider == "readlink":
            providers += (
                "realpath() { return 1; }\n"
                'readlink() { local value; for value in "$@"; do :; done; '
                'printf "%s" "$value"; }\n'
            )
        elif provider == "fallback":
            providers += "realpath() { return 1; }\nreadlink() { return 1; }\n"
        elif provider == "native":
            pass
        elif provider == "native-fallback":
            providers += (
                "realpath() { return 1; }\n"
                'readlink() { [[ "$1" != -fn ]] || return 1; command readlink "$@"; }\n'
            )
        else:
            raise AssertionError(provider)
        command = (
            "set -euo pipefail\nCANONICAL_PATH_MAX_LINK_DEPTH=40\n"
            "CANONICAL_PATH_PARTS=()\n"
            + self.functions
            + "\n"
            + providers
            + body
        )
        with tempfile.TemporaryDirectory(prefix="path-capture-") as temporary:
            environment = {
                **os.environ,
                "HOME": temporary,
                "LC_ALL": "C",
                "BASH_ENV": "",
                "ENV": "",
            }
            result = subprocess.run(
                [self.bash, "--noprofile", "--norc", "-s"],
                input=command.encode("utf-8"),
                cwd=temporary,
                env=environment,
                capture_output=True,
                timeout=10,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            result.stdout.decode("utf-8"),
            result.stderr.decode("utf-8"),
        )

    def test_fast_path_preserves_one_or_multiple_trailing_linefeeds(self) -> None:
        for provider in ("realpath", "readlink"):
            for suffix in ("$'\\n'", "$'\\n\\n'", "$'x\\n'"):
                with self.subTest(provider=provider, suffix=suffix):
                    result = self._run(
                        f'expected="$PWD/leaf"{suffix}\n'
                        'printf "%s\\0" "$expected"\n'
                        'canonical_path "$expected"\n',
                        provider,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected, actual = result.stdout.split("\0", 1)
                    self.assertEqual(actual, expected)

    def test_missing_path_fallback_preserves_trailing_linefeeds(self) -> None:
        result = self._run(
            'expected="$PWD/missing/leaf"$\'\\n\\n\'\n'
            'printf "%s\\0" "$expected"\n'
            'canonical_path "$expected"\n',
            "fallback",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected, actual = result.stdout.split("\0", 1)
        self.assertEqual(actual, expected)

    def test_equality_does_not_compare_a_trimmed_different_path(self) -> None:
        result = self._run('paths_equal "$PWD/leaf"$\'\\n\' "$PWD/leaf"\n')
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_containment_does_not_accept_a_trimmed_sibling(self) -> None:
        result = self._run('path_is_within "$PWD/allowed"$\'\\n\' "$PWD/allowed"\n')
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_comparisons_preserve_canonicalization_failure(self) -> None:
        for comparison in ("paths_equal", "path_is_within"):
            with self.subTest(comparison=comparison):
                result = self._run(
                    'canonical_path() { printf "resolution failed\\n" >&2; return 17; }\n'
                    f'{comparison} "$PWD/left" "$PWD/right"\n'
                )
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertIn("resolution failed", result.stderr)

    def test_directory_capture_removes_only_the_printer_linefeed(self) -> None:
        result = self._run(
            'dirname() { printf "%s\\n" "${2%/*}"; }\n'
            'expected="$PWD/parent"$\'\\n\\n\'\n'
            'dirname_into actual "$expected/leaf"\n'
            'printf "%s\\0%s" "$expected" "$actual"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected, actual = result.stdout.split("\0", 1)
        self.assertEqual(actual, expected)

    def test_failed_capture_keeps_the_target_and_error_status(self) -> None:
        result = self._run(
            'actual="retained"\n'
            'failed_path() { printf "partial"; printf "diagnostic\\n" >&2; return 23; }\n'
            'if path_output_into actual failed_path; then exit 99; else status=$?; fi\n'
            'printf "%s\\0%s" "$actual" "$status"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "retained\0" + "23")
        self.assertEqual(result.stderr, "diagnostic\n")

    def test_basename_capture_preserves_trailing_linefeeds(self) -> None:
        result = self._run(
            'expected=$\'leaf\\n\\n\'\n'
            'basename_into actual "$PWD/$expected"\n'
            'printf "%s\\0%s" "$expected" "$actual"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected, actual = result.stdout.split("\0", 1)
        self.assertEqual(actual, expected)

    def test_canonical_output_assignment_preserves_bytes(self) -> None:
        result = self._run(
            'expected="$PWD/leaf"$\'\\n\\n\'\n'
            'canonical_path_into actual "$expected"\n'
            'printf "%s\\0%s" "$expected" "$actual"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected, actual = result.stdout.split("\0", 1)
        self.assertEqual(actual, expected)

    def test_path_callers_do_not_use_trimming_substitutions(self) -> None:
        self.assertNotRegex(
            SCRIPT.read_text(encoding="utf-8"),
            r"\$\((?:canonical_path|dirname|basename)\s",
        )

    @unittest.skipIf(os.name == "nt", "Native POSIX filename semantics are required")
    def test_native_symlink_targets_preserve_trailing_linefeeds(self) -> None:
        for provider in ("native", "native-fallback"):
            for exists in (False, True):
                with self.subTest(provider=provider, exists=exists):
                    result = self._run(
                        'expected="$PWD/target"$\'\\n\\n\'\n'
                        + ('mkdir -- "$expected"\n' if exists else "")
                        + 'ln -s -- "$expected" "$PWD/link"\n'
                        'canonical_path_into actual "$PWD/link"\n'
                        'printf "%s\\0%s" "$expected" "$actual"\n',
                        provider,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected, actual = result.stdout.split("\0", 1)
                    self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
