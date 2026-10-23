# Python-free foundation progress; callers supply actual installation execution custody.
if (-not ('CopilotExtensions.NativeFoundationState' -as [type])) {
    $compile = @{}
    if ($PSVersionTable.PSEdition -ne 'Core') {
        $compile.ReferencedAssemblies = @('System.Runtime.Serialization', 'System.Xml', 'System.Core')
    }
    Add-Type @compile -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Xml;
using System.Runtime.Serialization.Json;
using Microsoft.Win32.SafeHandles;

namespace CopilotExtensions {
    public static class NativeFoundationState {
        private const string Schema = "copilot-extensions.installation-foundation-operation";
        private const int MaxBytes = 32768;
        private static readonly string[] Fields = {
            "schema", "version", "operation_id", "source_hash", "boot_id", "state",
            "started_at", "finished_at", "exit_code", "error", "settled_boot_id", "settled_at"
        };
        private static readonly HashSet<string> Generated = new HashSet<string>(StringComparer.Ordinal) {
            ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
            "node_modules"
        };
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private sealed class LargeInteger {
            public readonly string Text;
            public readonly double Number;
            public LargeInteger(string text, double number) { Text = text; Number = number; }
            public override string ToString() { return Text; }
        }

        [DllImport("ntdll.dll")]
        private static extern int NtQuerySystemInformation(
            uint kind, [Out] byte[] data, uint size, out uint returned);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool MoveFileEx(string source, string destination, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string path, uint access, uint sharing, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(
            SafeFileHandle file, StringBuilder path, uint size, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInfo info);
        [StructLayout(LayoutKind.Sequential)]
        private struct FileInfo {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME Creation, Access, Write;
            public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }

        private static IOException Invalid(string reason) {
            return new IOException("foundation " + reason);
        }
        private static bool Hex(string value, int length) {
            if (value == null || value.Length != length) return false;
            foreach (char c in value)
                if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) return false;
            return true;
        }
        private static bool Boot(string value) {
            if (value == null) return false;
            int colon = value.IndexOf(':');
            if (colon < 0) return false;
            string platform = value.Substring(0, colon), id = value.Substring(colon + 1);
            Guid parsed;
            return (platform == "windows" || platform == "linux" || platform == "darwin")
                && Guid.TryParseExact(id, "D", out parsed) && parsed != Guid.Empty
                && parsed.ToString("D") == id;
        }
        public static string KernelBootIdentity() {
            var data = new byte[32];
            uint returned;
            if (NtQuerySystemInformation(90, data, (uint)data.Length, out returned) < 0
                || returned < 16) throw Invalid("native boot identity unavailable");
            var id = new byte[16];
            Array.Copy(data, id, 16);
            string value = "windows:" + new Guid(id).ToString("D");
            if (!Boot(value)) throw Invalid("native boot identity malformed");
            return value;
        }
        private static string Absolute(string path) {
            if (String.IsNullOrEmpty(path) || !Path.IsPathRooted(path)
                || !String.Equals(path, Path.GetFullPath(path), StringComparison.Ordinal))
                throw Invalid("path must be normalized and absolute");
            return path;
        }
        private static bool Ordinary(string path, bool directory, bool missing) {
            Absolute(path);
            bool present = true;
            for (string item = path; item != null; item = Path.GetDirectoryName(item)) {
                try {
                    FileAttributes attributes = File.GetAttributes(item);
                    if ((attributes & FileAttributes.ReparsePoint) != 0
                        || (item != path && (attributes & FileAttributes.Directory) == 0)
                        || (item == path && ((attributes & FileAttributes.Directory) != 0) != directory))
                        throw Invalid("path must be ordinary and unlinked");
                } catch (IOException error) {
                    if (!missing || (!(error is FileNotFoundException) && !(error is DirectoryNotFoundException)))
                        throw;
                    if (item == path) present = false;
                }
                string parent = Path.GetDirectoryName(item);
                if (parent == item) break;
            }
            return present;
        }
        public static bool CheckPath(string path, bool directory, bool missing) {
            return Ordinary(path, directory, missing);
        }
        private static FileStream OpenPlain(string path) {
            Ordinary(path, false, false);
            var handle = CreateFile(path, 0x80000000, 0x7, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
            try {
                if (handle.IsInvalid) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                FileInfo info;
                if (!GetFileInformationByHandle(handle, out info)
                    || (info.Attributes & (uint)(FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
                    throw Invalid("opened input is not ordinary");
                var buffer = new StringBuilder(32768);
                uint length = GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
                if (length == 0 || length >= buffer.Capacity) throw Invalid("opened input path unavailable");
                string final = buffer.ToString();
                if (final.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
                    final = @"\\" + final.Substring(8);
                else if (final.StartsWith(@"\\?\", StringComparison.Ordinal)) final = final.Substring(4);
                if (!String.Equals(final, path, StringComparison.OrdinalIgnoreCase))
                    throw Invalid("opened input path differs");
                Ordinary(path, false, false);
                return new FileStream(handle, FileAccess.Read);
            } catch {
                handle.Dispose();
                throw;
            }
        }
        private static Dictionary<string, object> Parse(byte[] bytes) {
            if (bytes.Length > MaxBytes) throw Invalid("progress exceeds size bound");
            string json = Utf8.GetString(bytes);
            bool quoted = false, escaped = false, ended = false;
            int depth = 0;
            foreach (char character in json) {
                if (ended) {
                    if (character != ' ' && character != '\r' && character != '\n' && character != '\t')
                        throw Invalid("trailing progress content");
                } else if (quoted) {
                    if (escaped) escaped = false;
                    else if (character == '\\') escaped = true;
                    else if (character == '"') quoted = false;
                } else if (character == '"') quoted = true;
                else if (character == '{') ++depth;
                else if (character == '}' && --depth == 0) ended = true;
            }
            if (!ended) throw Invalid("incomplete progress object");
            // The JSON reader preserves duplicate members and primitive types, unlike PS conversion.
            var quotas = new XmlDictionaryReaderQuotas();
            quotas.MaxDepth = 4;
            quotas.MaxStringContentLength = MaxBytes;
            quotas.MaxArrayLength = MaxBytes;
            var xml = new XmlDocument();
            using (var reader = JsonReaderWriterFactory.CreateJsonReader(bytes, quotas))
                xml.Load(reader);
            XmlElement root = xml.DocumentElement;
            if (root == null || root.GetAttribute("type") != "object" || root.Attributes.Count != 1)
                throw Invalid("unsupported operation schema");
            var values = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (XmlNode node in root.ChildNodes) {
                var element = node as XmlElement;
                if (element == null || Array.IndexOf(Fields, element.Name) < 0
                    || values.ContainsKey(element.Name) || element.ChildNodes.Count > 1
                    || (element.FirstChild != null && !(element.FirstChild is XmlText)))
                    throw Invalid("unknown, duplicate or nonprimitive progress field");
                object value;
                string kind = element.GetAttribute("type"), text = element.InnerText;
                if (kind == "null") value = null;
                else if (kind == "string") value = text;
                else if (kind == "number") {
                    if (!System.Text.RegularExpressions.Regex.IsMatch(
                        text, @"\A-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\z"))
                        throw Invalid("malformed progress number");
                    long integer;
                    double number;
                    if (Int64.TryParse(text, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out integer))
                        value = integer;
                    else if (Double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out number)) {
                        bool integerText = System.Text.RegularExpressions.Regex.IsMatch(text, @"\A-?[0-9]+\z");
                        value = integerText ? (object)new LargeInteger(text, number) : number;
                    }
                    else throw Invalid("malformed progress number");
                } else throw Invalid("malformed progress type");
                values.Add(element.Name, value);
            }
            Validate(values);
            return values;
        }
        private static string Text(Dictionary<string, object> values, string key) {
            return values[key] as string;
        }
        private static void Validate(Dictionary<string, object> value) {
            if (value.Count != Fields.Length || Text(value, "schema") != Schema
                || !(value["version"] is long) || (long)value["version"] != 1)
                throw Invalid("unsupported operation schema");
            if (!Hex(Text(value, "operation_id"), 32) || !Hex(Text(value, "source_hash"), 64)
                || !Boot(Text(value, "boot_id"))) throw Invalid("malformed operation identity");
            foreach (string key in new[] { "started_at", "finished_at", "settled_at" }) {
                object item = value[key];
                if (item == null && key != "started_at") continue;
                if (!(item is long) && !(item is double) && !(item is LargeInteger))
                    throw Invalid("malformed timestamp");
                double number = item is LargeInteger ? ((LargeInteger)item).Number
                    : Convert.ToDouble(item, CultureInfo.InvariantCulture);
                if (Double.IsNaN(number) || Double.IsInfinity(number) || number < 0)
                    throw Invalid("malformed timestamp");
            }
            object code = value["exit_code"], error = value["error"];
            if (code != null && !(code is long)) throw Invalid("malformed exit status");
            string reason = error as string;
            if (error != null && (reason == null
                || !System.Text.RegularExpressions.Regex.IsMatch(reason, @"\A[a-z][a-z0-9_]{0,63}\z")))
                throw Invalid("errors must be bounded reason codes");
            string state = Text(value, "state");
            bool zero = code is long && (long)code == 0;
            if ((state != "running" && state != "completed" && state != "failed" && state != "interrupted")
                || (state == "running" && (value["finished_at"] != null || code != null || error != null))
                || (state == "completed" && (value["finished_at"] == null || !zero || error != null))
                || (state == "failed" && (value["finished_at"] == null || code == null || zero || error == null))
                || (state == "interrupted" && (value["finished_at"] == null || code != null || error == null)))
                throw Invalid("inconsistent operation outcome");
            object settled = value["settled_boot_id"], at = value["settled_at"];
            string settledId = settled as string, bootId = Text(value, "boot_id");
            if ((settled == null) != (at == null)
                || (settled != null && (state != "interrupted" || !Boot(settledId)
                    || settledId == bootId || settledId.Split(':')[0] != bootId.Split(':')[0])))
                throw Invalid("malformed positive settlement");
        }
        private static byte[] Encode(Dictionary<string, object> values) {
            Validate(values);
            using (var stream = new MemoryStream()) {
                using (var writer = JsonReaderWriterFactory.CreateJsonWriter(stream, Utf8, false)) {
                    writer.WriteStartElement("root");
                    writer.WriteAttributeString("type", "object");
                    foreach (string key in Fields) {
                        object value = values[key];
                        writer.WriteStartElement(key);
                        writer.WriteAttributeString("type", value == null ? "null" : value is string ? "string" : "number");
                        if (value != null) writer.WriteString(value is double
                            ? ((double)value).ToString("R", CultureInfo.InvariantCulture)
                            : Convert.ToString(value, CultureInfo.InvariantCulture));
                        writer.WriteEndElement();
                    }
                    writer.WriteEndElement();
                    writer.Flush();
                }
                byte[] bytes = stream.ToArray();
                Parse(bytes);
                return bytes;
            }
        }
        private static Dictionary<string, object> ReadValue(string root) {
            if (!Ordinary(root, true, true)) return null;
            string path = Path.Combine(root, "foundation-operation.json");
            if (!Ordinary(path, false, true)) {
                string artifacts = Path.Combine(root, "foundation");
                if (Ordinary(artifacts, true, true))
                    throw Invalid("progress missing for existing artifacts");
                return null;
            }
            using (var stream = OpenPlain(path)) {
                if (stream.Length > MaxBytes) throw Invalid("progress exceeds size bound");
                var buffer = new byte[MaxBytes + 1];
                int used = 0, read;
                while (used < buffer.Length && (read = stream.Read(buffer, used, buffer.Length - used)) != 0)
                    used += read;
                Array.Resize(ref buffer, used);
                return Parse(buffer);
            }
        }
        public static string Read(string root) {
            var value = ReadValue(root);
            return value == null ? null : Utf8.GetString(Encode(value));
        }
        public static string ReconcileLost(string root, Action owned) {
            owned();
            var value = ReadValue(root);
            if (value != null && Text(value, "state") == "running") {
                value["state"] = "interrupted";
                value["finished_at"] = Now();
                value["error"] = "owner_lost_custody";
                Write(root, value, owned);
            }
            return value == null ? null : Utf8.GetString(Encode(value));
        }
        public static string RecoverAfterRestart(string root, Action owned) {
            owned();
            var value = ReadValue(root);
            if (!Fenced(value)) return value == null ? null : Utf8.GetString(Encode(value));
            string current = KernelBootIdentity(), prior = Text(value, "boot_id");
            if (current == prior || current.Split(':')[0] != prior.Split(':')[0])
                throw Invalid("recovery requires a different native kernel boot");
            value["state"] = "interrupted";
            if (value["finished_at"] == null) value["finished_at"] = Now();
            if (value["error"] == null) value["error"] = "owner_lost_custody";
            value["settled_boot_id"] = current;
            value["settled_at"] = Now();
            Write(root, value, owned);
            return Utf8.GetString(Encode(value));
        }
        private static double Now() {
            return (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
        }
        private static bool Fenced(Dictionary<string, object> value) {
            return value != null && (Text(value, "state") == "running" || Text(value, "state") == "interrupted")
                && value["settled_at"] == null;
        }
        private static void Write(string root, Dictionary<string, object> value, Action owned) {
            owned();
            byte[] bytes = Encode(value);
            string temporary = Path.Combine(root, ".foundation-operation-" + Guid.NewGuid().ToString("N") + ".tmp");
            try {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                    FileShare.None, 4096, FileOptions.WriteThrough)) {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
                owned();
                Ordinary(root, true, false);
                Ordinary(Path.Combine(root, "foundation-operation.json"), false, true);
                var elapsed = System.Diagnostics.Stopwatch.StartNew();
                while (!MoveFileEx(temporary, Path.Combine(root, "foundation-operation.json"), 0x1 | 0x8)) {
                    int error = Marshal.GetLastWin32Error();
                    if ((error != 5 && error != 32 && error != 33) || elapsed.ElapsedMilliseconds >= 2000)
                        throw new System.ComponentModel.Win32Exception(error, "foundation progress persistence failed");
                    owned();
                    Thread.Sleep(10);
                }
            } finally {
                if (File.Exists(temporary)) File.Delete(temporary);
            }
        }
        private static void ColdQueue(string root) {
            foreach (string name in new[] { "queue.sqlite3", "queue.sqlite3-wal", "queue.sqlite3-shm", "queue.sqlite3-journal" })
                if (Ordinary(Path.Combine(root, name), false, true))
                    throw Invalid("existing queue requires queue-aware foundation authority");
        }
        public static string BeginCold(string root, string sourceHash, Action owned) {
            owned();
            Ordinary(root, true, false);
            if (!Hex(sourceHash, 64)) throw Invalid("requires full source digest");
            if (Fenced(ReadValue(root))) throw Invalid("fenced; positive settlement required");
            ColdQueue(root);
            var value = new Dictionary<string, object>();
            foreach (string field in Fields) value.Add(field, null);
            value["schema"] = Schema;
            value["version"] = 1L;
            value["operation_id"] = Guid.NewGuid().ToString("N");
            value["source_hash"] = sourceHash;
            value["boot_id"] = KernelBootIdentity();
            value["state"] = "running";
            value["started_at"] = Now();
            Write(root, value, owned);
            return Utf8.GetString(Encode(value));
        }
        public static string Finish(string root, string operation, string plugin, object exitCode, object error, Action owned) {
            owned();
            var expected = Parse(Utf8.GetBytes(operation));
            var actual = ReadValue(root);
            if (actual == null || Text(actual, "state") != "running"
                || Utf8.GetString(Encode(actual)) != Utf8.GetString(Encode(expected)))
                throw Invalid("completion does not match active operation");
            if (exitCode is long && (long)exitCode == 0 && SourceDigest(plugin) != Text(actual, "source_hash"))
                throw Invalid("source changed after admission");
            actual["exit_code"] = exitCode;
            actual["error"] = error;
            actual["state"] = exitCode == null ? "interrupted" : (exitCode is long && (long)exitCode == 0 ? "completed" : "failed");
            actual["finished_at"] = Now();
            Write(root, actual, owned);
            return Utf8.GetString(Encode(actual));
        }
        private static string Sha(byte[] bytes) {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }
        private static List<string> SourceFiles(string plugin) {
            Absolute(plugin);
            if (Path.GetFileName(plugin) != "update-driver") throw Invalid("requires exact update-driver source");
            string common = Path.GetDirectoryName(plugin);
            var files = new List<string>();
            foreach (string name in new[] { "update-driver", "agent-procutil", "single-instance-lease" }) {
                string package = Path.Combine(common, name);
                Ordinary(package, true, false);
                Ordinary(Path.Combine(package, "pyproject.toml"), false, false);
                var pending = new Stack<string>();
                pending.Push(package);
                while (pending.Count != 0) {
                    string current = pending.Pop();
                    Ordinary(current, true, false);
                    foreach (string directory in Directory.GetDirectories(current)) {
                        Ordinary(directory, true, false);
                        string leaf = Path.GetFileName(directory);
                        if (!Generated.Contains(leaf) && !leaf.EndsWith(".egg-info", StringComparison.Ordinal)
                            && !leaf.EndsWith(".dist-info", StringComparison.Ordinal)) pending.Push(directory);
                    }
                    foreach (string file in Directory.GetFiles(current)) {
                        Ordinary(file, false, false);
                        if (!file.EndsWith(".pyc", StringComparison.Ordinal) && !file.EndsWith(".pyo", StringComparison.Ordinal))
                            files.Add(file);
                    }
                }
            }
            foreach (string support in new[] {
                "installer-engine.ps1", "installer-engine.sh",
                "installer_engine.py", "installer_engine_process.py", "installer_dependency_policy.py",
                "installer_dependency_config.py", "runtime_launcher.py", "versioned_runtime.py",
                "versioned_runtime_leases.py", "versioned_runtime_construction.py",
                "versioned_runtime_source.py", "versioned_runtime_publication.py"
            }) Ordinary(Path.Combine(plugin, "scripts", support), false, false);
            files.Sort(delegate(string left, string right) {
                byte[] first = Utf8.GetBytes(left.Substring(common.Length).Replace('\\', '/'));
                byte[] second = Utf8.GetBytes(right.Substring(common.Length).Replace('\\', '/'));
                for (int index = 0; index < Math.Min(first.Length, second.Length); ++index)
                    if (first[index] != second[index]) return first[index].CompareTo(second[index]);
                return first.Length.CompareTo(second.Length);
            });
            return files;
        }
        public static string SourceDigest(string plugin) {
            var files = SourceFiles(plugin);
            string common = Path.GetDirectoryName(plugin);
            using (var inventory = new MemoryStream()) {
                foreach (string file in files) {
                    string relative = Relative(file, common).Replace('\\', '/');
                    string hash;
                    using (var stream = OpenPlain(file))
                    using (var sha = SHA256.Create())
                        hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                    byte[] entry = Utf8.GetBytes(relative + "\0" + hash + "\n");
                    inventory.Write(entry, 0, entry.Length);
                }
                return Sha(inventory.ToArray());
            }
        }
        private static string Relative(string path, string common) {
            return path.Substring(common.EndsWith(Path.DirectorySeparatorChar.ToString(), StringComparison.Ordinal)
                ? common.Length : common.Length + 1);
        }
        public static void CheckExecutionRoot(string root, string plugin) {
            Ordinary(root, true, true);
            string common = Path.GetDirectoryName(plugin);
            foreach (string name in new[] { "update-driver", "agent-procutil", "single-instance-lease" }) {
                string package = Path.Combine(common, name);
                if (String.Equals(root, package, StringComparison.OrdinalIgnoreCase)
                    || root.StartsWith(package + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                    throw Invalid("execution root cannot mutate its source closure");
            }
        }
        public static string FreezeSource(string plugin, string destination, string expected, Action owned) {
            owned();
            CheckExecutionRoot(destination, plugin);
            if (SourceDigest(plugin) != expected) throw Invalid("source changed before native freeze");
            if (Ordinary(destination, true, true)) throw Invalid("native source freeze must be new");
            string common = Path.GetDirectoryName(plugin);
            Directory.CreateDirectory(destination);
            foreach (string source in SourceFiles(plugin)) {
                owned();
                string target = Path.Combine(destination, Relative(source, common));
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                Ordinary(Path.GetDirectoryName(target), true, false);
                using (var input = OpenPlain(source))
                using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    input.CopyTo(output);
                    output.Flush(true);
                }
            }
            owned();
            string frozen = Path.Combine(destination, "update-driver");
            if (SourceDigest(frozen) != expected || SourceDigest(plugin) != expected)
                throw Invalid("source changed during native freeze");
            return frozen;
        }
    }
}
'@
}

function Get-NativeFoundationProgress {
    param([Parameter(Mandatory)][string]$Root)
    return [CopilotExtensions.NativeFoundationState]::Read($Root)
}

function Repair-NativeFoundationAfterRestart {
    param(
        [Parameter(Mandatory)]$Custody,
        [Parameter(Mandatory)][string]$Root
    )
    if ($Custody -isnot [CopilotExtensions.NativeOperationLock]) {
        throw 'Foundation recovery requires actual native operation custody.'
    }
    $Custody.AssertOwned()
    $assertion = $Custody.CreateWitnessAssertion([IO.Path]::Combine($Root, 'execution.lock'))
    return [CopilotExtensions.NativeFoundationState]::RecoverAfterRestart($Root, $assertion)
}

function Start-NativeFoundationProgress {
    param(
        [Parameter(Mandatory)]$Custody,
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Plugin,
        [string]$ExpectedSourceHash
    )
    if ($Custody -isnot [CopilotExtensions.NativeOperationLock]) {
        throw 'Foundation progress requires actual native operation custody.'
    }
    $Custody.AssertOwned()
    $assertion = $Custody.CreateWitnessAssertion([IO.Path]::Combine($Root, 'execution.lock'))
    $digest = [CopilotExtensions.NativeFoundationState]::SourceDigest($Plugin)
    [CopilotExtensions.NativeFoundationState]::CheckExecutionRoot($Root, $Plugin)
    if ($PSBoundParameters.ContainsKey('ExpectedSourceHash') -and $digest -cne $ExpectedSourceHash) {
        throw 'Foundation source differs from its requested inventory.'
    }
    return [CopilotExtensions.NativeFoundationState]::BeginCold($Root, $digest, $assertion)
}

function Start-NativeFoundationProducer {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Plugin,
        [string]$ExpectedSourceHash
    )
    $digest = [CopilotExtensions.NativeFoundationState]::SourceDigest($Plugin)
    if ($PSBoundParameters.ContainsKey('ExpectedSourceHash') -and $digest -cne $ExpectedSourceHash) {
        throw 'Foundation source differs from its requested inventory.'
    }
    [CopilotExtensions.NativeFoundationState]::CheckExecutionRoot($Root, $Plugin)
    . (Join-Path $Plugin 'src\update_driver\directory.ps1')
    $library = [IO.Path]::GetDirectoryName($Plugin)
    . (Join-Path $library 'agent-procutil\src\agent_procutil\native_daemon.ps1')
    New-NativeExecutionDirectory -Root $Root
    $log = Join-Path $Root ('foundation-launch-' + [Guid]::NewGuid().ToString('N') + '.log')
    return Start-NativeDaemon -Script (Join-Path $Plugin 'scripts\foundation-produce.ps1') `
        -Arguments @($Root, $digest) -WorkingDirectory $Plugin -LogPath $log
}

function Complete-NativeFoundationProgress {
    param(
        [Parameter(Mandatory)]$Custody,
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Operation,
        [Parameter(Mandatory)][string]$Plugin,
        [Nullable[long]]$ExitCode,
        [string]$Reason
    )
    if ($Custody -isnot [CopilotExtensions.NativeOperationLock]) {
        throw 'Foundation progress requires actual native operation custody.'
    }
    $Custody.AssertOwned()
    $assertion = $Custody.CreateWitnessAssertion([IO.Path]::Combine($Root, 'execution.lock'))
    $errorCode = if ($PSBoundParameters.ContainsKey('Reason')) { $Reason } else { $null }
    return [CopilotExtensions.NativeFoundationState]::Finish($Root, $Operation, $Plugin, $ExitCode, $errorCode, $assertion)
}
