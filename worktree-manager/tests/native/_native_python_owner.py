"""Native executable adapter for a synthetic Python runtime owner."""

import os
from pathlib import Path
import subprocess
import sys


def native_python_owner(root: Path, env: dict[str, str]) -> Path:
    compiler = next((
        Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework / "v4.0.30319/csc.exe"
        for framework in ("Framework64", "Framework")
        if (Path(os.environ["SystemRoot"]) / "Microsoft.NET" / framework
            / "v4.0.30319/csc.exe").is_file()
    ), None)
    assert compiler, "The Windows .NET Framework compiler is required for the native owner fixture"
    source = root / "owner.cs"
    source.write_text(r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

class PythonOwner {
    static string Quote(string value) {
        return "\"" + Regex.Replace(
            Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
    }

    static int Main(string[] args) {
        // Framework's redirected stdin writer must not prepend a console UTF-8 BOM.
        if (Console.InputEncoding.CodePage == Encoding.UTF8.CodePage) {
            Console.InputEncoding = new UTF8Encoding(false);
        }
        var start = new ProcessStartInfo {
            FileName = PYTHON,
            Arguments = String.Join(" ", new[] { OWNER }.Concat(args).Select(Quote)),
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        using (var child = Process.Start(start)) {
            if (Console.IsInputRedirected) {
                Task.Factory.StartNew(() => {
                    try { Console.OpenStandardInput().CopyTo(child.StandardInput.BaseStream); }
                    catch (IOException) {}
                    finally { child.StandardInput.Close(); }
                });
            } else {
                // Finite owner calls must not consume their caller's ConPTY input.
                child.StandardInput.Close();
            }
            var output = Task.Factory.StartNew(
                () => child.StandardOutput.BaseStream.CopyTo(Console.OpenStandardOutput()));
            var error = Task.Factory.StartNew(
                () => child.StandardError.BaseStream.CopyTo(Console.OpenStandardError()));
            child.WaitForExit();
            // A non-piped caller can keep stdin open after its finite owner exits.
            Task.WaitAll(output, error);
            return child.ExitCode;
        }
    }
}
'''.replace("PYTHON", '@"' + sys.executable.replace('"', '""') + '"')
   .replace("OWNER", '@"' + str(root / "owner.py").replace('"', '""') + '"'),
        encoding="utf-8")
    executable = root / "owner.exe"
    result = subprocess.run(
        [str(compiler), "/nologo", "/target:exe", "/out:" + str(executable), str(source)],
        env=env, capture_output=True, text=True, timeout=20, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return executable
