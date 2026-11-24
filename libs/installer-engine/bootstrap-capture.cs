using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace CopilotExtensions.BootstrapCapture
{
    [DataContract]
    internal sealed class Request
    {
        [DataMember(Name = "version", IsRequired = true)] public int Version { get; set; }
        [DataMember(Name = "nonce", IsRequired = true)] public string Nonce { get; set; }
        [DataMember(Name = "owner_pid", IsRequired = true)] public int OwnerPid { get; set; }
        [DataMember(Name = "owner_birth", IsRequired = true)] public string OwnerBirth { get; set; }
        [DataMember(Name = "executable", IsRequired = true)] public string Executable { get; set; }
        [DataMember(Name = "arguments", IsRequired = true)] public string[] Arguments { get; set; }
        [DataMember(Name = "cwd", IsRequired = true)] public string Cwd { get; set; }
        [DataMember(Name = "environment", IsRequired = true)] public Dictionary<string, string> Environment { get; set; }
        [DataMember(Name = "input")] public string Input { get; set; }
        [DataMember(Name = "timeout_ms", IsRequired = true)] public int Timeout { get; set; }
        [DataMember(Name = "log_path")] public string LogPath { get; set; }
    }

    [DataContract]
    internal sealed class JobReceipt
    {
        [DataMember(Name = "parent_pid")] public int ParentPid;
        [DataMember(Name = "parent_birth")] public string ParentBirth;
        [DataMember(Name = "parent_handle")] public string ParentHandle;
        [DataMember(Name = "worker_pid")] public int WorkerPid;
        [DataMember(Name = "worker_birth")] public string WorkerBirth;
        [DataMember(Name = "name")] public string Name;
    }

    [DataContract]
    internal sealed class Ready
    {
        [DataMember(Name = "version")] public int Version = 1;
        [DataMember(Name = "kind")] public string Kind = "ready";
        [DataMember(Name = "nonce")] public string Nonce;
        [DataMember(Name = "job")] public JobReceipt Job;
    }

    [DataContract]
    internal sealed class Result
    {
        [DataMember(Name = "version")] public int Version = 1;
        [DataMember(Name = "kind")] public string Kind = "result";
        [DataMember(Name = "nonce")] public string Nonce;
        [DataMember(Name = "exit_code")] public int ExitCode;
        [DataMember(Name = "stdout")] public string Stdout = "";
        [DataMember(Name = "stderr")] public string Stderr = "";
        [DataMember(Name = "error")] public string Error = "";
        [DataMember(Name = "native_error")] public int NativeError;
    }

    internal static class Wire
    {
        internal const int Limit = 1024 * 1024;
        internal static readonly Encoding Utf8 = new UTF8Encoding(false, true);
        private static class Channel
        {
            internal static readonly StreamWriter Output = new StreamWriter(Console.OpenStandardOutput(), Utf8) {
                AutoFlush = true,
            };
        }

        internal static T Decode<T>(string text)
        {
            if (text == null || text.Length > Limit * 2) throw new InvalidDataException("request");
            byte[] bytes = Convert.FromBase64String(text);
            if (bytes.Length > Limit) throw new InvalidDataException("request");
            using (var stream = new MemoryStream(bytes))
            {
                var serializer = new DataContractJsonSerializer(typeof(T),
                    new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });
                return (T)serializer.ReadObject(stream);
            }
        }

        internal static string Encode<T>(T value, bool base64)
        {
            using (var stream = new MemoryStream())
            {
                var serializer = new DataContractJsonSerializer(typeof(T),
                    new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });
                serializer.WriteObject(stream, value);
                var bytes = stream.ToArray();
                if (bytes.Length > Limit) throw new InvalidDataException("request");
                return base64 ? Convert.ToBase64String(bytes) : Utf8.GetString(bytes);
            }
        }

        internal static T Read<T>(string text)
        {
            if (text == null || Utf8.GetByteCount(text) > 16 * Limit)
                throw new InvalidDataException("response");
            using (var stream = new MemoryStream(Utf8.GetBytes(text)))
            {
                var serializer = new DataContractJsonSerializer(typeof(T),
                    new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });
                return (T)serializer.ReadObject(stream);
            }
        }

        internal static void Write<T>(T value)
        {
            using (var stream = new MemoryStream())
            {
                var serializer = new DataContractJsonSerializer(typeof(T),
                    new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true });
                serializer.WriteObject(stream, value);
                Channel.Output.WriteLine(Utf8.GetString(stream.ToArray()));
            }
        }

        internal static T Await<T>(Task<T> task, Stopwatch clock, long deadline)
        {
            while (!task.IsCompleted)
            {
                if (clock.ElapsedMilliseconds >= deadline) throw new TimeoutException("deadline");
                Thread.Sleep(5);
            }
            if (clock.ElapsedMilliseconds >= deadline) throw new TimeoutException("deadline");
            return task.GetAwaiter().GetResult();
        }
    }

    internal static class Native
    {
        [StructLayout(LayoutKind.Sequential)]
        internal struct BasicLimits
        {
            internal long ProcessTime, JobTime;
            internal uint Flags;
            internal UIntPtr MinimumWorkingSet, MaximumWorkingSet;
            internal uint ActiveProcessLimit;
            internal UIntPtr Affinity;
            internal uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct IoCounters
        {
            internal ulong ReadOperations, WriteOperations, OtherOperations;
            internal ulong ReadBytes, WriteBytes, OtherBytes;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct ExtendedLimits
        {
            internal BasicLimits Basic;
            internal IoCounters Io;
            internal UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct Accounting
        {
            internal long UserTime, KernelTime, PeriodUserTime, PeriodKernelTime;
            internal uint PageFaults, TotalProcesses, ActiveProcesses, TerminatedProcesses;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll")] internal static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] internal static extern IntPtr GetStdHandle(int id);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool GetNamedPipeServerProcessId(IntPtr pipe, out uint pid);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool GetProcessTimes(IntPtr process, out long birth, out long exit,
            out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool SetInformationJobObject(IntPtr job, int information,
            ref ExtendedLimits limits, uint size);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool QueryInformationJobObject(IntPtr job, int information,
            out Accounting accounting, uint size, IntPtr returnedLength);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool DuplicateHandle(IntPtr source, IntPtr handle, IntPtr target,
            out IntPtr duplicate, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] internal static extern void SetLastError(uint error);

        internal static void Check(bool success)
        {
            if (!success) throw new Win32Exception(Marshal.GetLastWin32Error());
        }

        internal static void Close(ref IntPtr handle)
        {
            var value = handle;
            handle = IntPtr.Zero;
            if (value != IntPtr.Zero) Check(CloseHandle(value));
        }

        internal static string Birth(IntPtr process)
        {
            long birth, exit, kernel, user;
            Check(GetProcessTimes(process, out birth, out exit, out kernel, out user));
            return "windows:" + birth.ToString(CultureInfo.InvariantCulture);
        }

        internal static void Live(IntPtr process)
        {
            var result = WaitForSingleObject(process, 0);
            if (result == 0) throw new InvalidOperationException("owner exited");
            if (result != 258) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    public static class JobRetirement
    {
        public static void TerminateAndWait(IntPtr job, int timeoutMilliseconds)
        {
            if (job == IntPtr.Zero || timeoutMilliseconds < 0)
                throw new ArgumentException("owned Job retirement");
            var clock = Stopwatch.StartNew();
            Native.Check(Native.TerminateJobObject(job, 1));
            while (true)
            {
                Native.Accounting accounting;
                Native.Check(Native.QueryInformationJobObject(job, 1, out accounting,
                    (uint)Marshal.SizeOf(typeof(Native.Accounting)), IntPtr.Zero));
                if (accounting.ActiveProcesses == 0) return;
                if (clock.ElapsedMilliseconds >= timeoutMilliseconds)
                    throw new TimeoutException("owned Job retirement is unconfirmed");
                Thread.Sleep(1);
            }
        }
    }

    internal sealed class Offer : IDisposable
    {
        private IntPtr parent;
        internal readonly JobReceipt Receipt;

        private Offer(IntPtr parentHandle, JobReceipt receipt)
        {
            parent = parentHandle;
            Receipt = receipt;
        }

        internal static Offer Create(Request request)
        {
            uint server;
            Native.Check(Native.GetNamedPipeServerProcessId(Native.GetStdHandle(-10), out server));
            if (server != request.OwnerPid) throw new InvalidOperationException("pipe owner");
            var parent = Native.OpenProcess(0x1000 | 0x100000 | 0x40, false, request.OwnerPid);
            if (parent == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            IntPtr job = IntPtr.Zero, remote = IntPtr.Zero;
            bool published = false;
            try
            {
                if (Native.Birth(parent) != request.OwnerBirth)
                    throw new InvalidOperationException("owner birth");
                Native.Live(parent);
                string name = "Local\\ce-owned-job-" + Guid.NewGuid().ToString("N");
                Native.SetLastError(0);
                job = Native.CreateJobObjectW(IntPtr.Zero, name);
                if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
                if (Marshal.GetLastWin32Error() == 183) throw new InvalidOperationException("Job collision");
                var limits = new Native.ExtendedLimits();
                limits.Basic.Flags = 0x2000;
                Native.Check(Native.SetInformationJobObject(job, 9, ref limits,
                    (uint)Marshal.SizeOf(typeof(Native.ExtendedLimits))));
                Native.Check(Native.DuplicateHandle(Native.GetCurrentProcess(), job, parent,
                    out remote, 0, false, 2));
                Native.Live(parent);
                Native.Check(Native.AssignProcessToJobObject(job, Native.GetCurrentProcess()));
                // Only the original caller may retain the continuing Job handle.
                Native.Close(ref job);
                var receipt = new JobReceipt {
                    ParentPid = request.OwnerPid, ParentBirth = request.OwnerBirth,
                    ParentHandle = remote.ToInt64().ToString(CultureInfo.InvariantCulture),
                    WorkerPid = Process.GetCurrentProcess().Id,
                    WorkerBirth = Native.Birth(Native.GetCurrentProcess()), Name = name,
                };
                var offer = new Offer(parent, receipt);
                published = true;
                return offer;
            }
            finally
            {
                if (!published)
                {
                    try
                    {
                        if (remote != IntPtr.Zero)
                        {
                            IntPtr recovered;
                            Native.Check(Native.DuplicateHandle(parent, remote,
                                Native.GetCurrentProcess(), out recovered, 0, false, 1 | 2));
                            Native.Close(ref recovered);
                        }
                    }
                    finally
                    {
                        try { Native.Close(ref job); }
                        finally { Native.Close(ref parent); }
                    }
                }
            }
        }

        internal void Abort()
        {
            if (parent == IntPtr.Zero) throw new InvalidOperationException("settled offer");
            IntPtr recovered;
            Native.Check(Native.DuplicateHandle(parent,
                new IntPtr(long.Parse(Receipt.ParentHandle, CultureInfo.InvariantCulture)),
                Native.GetCurrentProcess(), out recovered, 0, false, 1 | 2));
            Native.Close(ref recovered);
        }

        public void Dispose()
        {
            Native.Close(ref parent);
        }
    }

    internal static class Program
    {
        private static string Quote(string value)
        {
            if (value.Length > 0 && !Regex.IsMatch(value, "[\\s\"]")) return value;
            return "\"" + Regex.Replace(
                Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
        }

        private static async Task<string> Capture(Stream stream, Stream log)
        {
            using (var output = new MemoryStream())
            {
                var buffer = new byte[4096];
                var decoder = log == null ? null : Wire.Utf8.GetDecoder();
                var characters = log == null ? null : new char[Wire.Utf8.GetMaxCharCount(buffer.Length)];
                int count;
                while (true)
                {
                    count = await stream.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false);
                    if (log != null)
                    {
                        // Complete each pipe's characters before interleaving its output.
                        int decoded = decoder.GetChars(buffer, 0, count, characters, 0, count == 0);
                        if (decoded != 0)
                        {
                            byte[] bytes = Wire.Utf8.GetBytes(characters, 0, decoded);
                            lock (log) { log.Write(bytes, 0, bytes.Length); log.Flush(); }
                        }
                        if (count == 0) break;
                        continue;
                    }
                    if (count == 0) break;
                    if (output.Length + count > Wire.Limit)
                        throw new InvalidDataException("capture size");
                    output.Write(buffer, 0, count);
                }
                return Wire.Utf8.GetString(output.ToArray());
            }
        }

        private static async Task<bool> Input(Process process, string text)
        {
            byte[] bytes = Wire.Utf8.GetBytes(text ?? "");
            if (bytes.Length != 0)
                await process.StandardInput.BaseStream.WriteAsync(bytes, 0, bytes.Length).ConfigureAwait(false);
            process.StandardInput.Close();
            return true;
        }

        private static Result Run(Request request, string jobName, Stopwatch clock, long deadline)
        {
            var result = new Result { Nonce = request.Nonce };
            try
            {
                var start = new ProcessStartInfo {
                    FileName = request.Executable, WorkingDirectory = request.Cwd,
                    Arguments = string.Join(" ", Array.ConvertAll(request.Arguments, Quote)),
                    UseShellExecute = false, CreateNoWindow = true,
                    RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                };
                start.EnvironmentVariables.Clear();
                foreach (var pair in request.Environment)
                    start.EnvironmentVariables[pair.Key] = pair.Value;
                string inherited = start.EnvironmentVariables["COPILOT_EXTENSIONS_OWNED_JOB_SCOPES"];
                start.EnvironmentVariables["COPILOT_EXTENSIONS_OWNED_JOB_SCOPES"] =
                    string.IsNullOrEmpty(inherited) ? jobName : inherited + ";" + jobName;
                using (var log = request.LogPath == null ? null :
                    new FileStream(request.LogPath, FileMode.Create, FileAccess.Write, FileShare.Read))
                using (var process = Process.Start(start))
                {
                    var stdout = Capture(process.StandardOutput.BaseStream, log);
                    var stderr = Capture(process.StandardError.BaseStream, log);
                    var input = Input(process, request.Input);
                    while (true)
                    {
                        if (clock.ElapsedMilliseconds >= deadline) throw new TimeoutException("deadline");
                        if (stdout.IsFaulted) stdout.GetAwaiter().GetResult();
                        if (stderr.IsFaulted) stderr.GetAwaiter().GetResult();
                        if (input.IsFaulted) input.GetAwaiter().GetResult();
                        if (stdout.IsCompleted && stderr.IsCompleted && input.IsCompleted && process.WaitForExit(0))
                        {
                            result.ExitCode = process.ExitCode;
                            result.Stdout = stdout.GetAwaiter().GetResult();
                            result.Stderr = stderr.GetAwaiter().GetResult();
                            return result;
                        }
                        Thread.Sleep(5);
                    }
                }
            }
            catch (Exception failure)
            {
                result.ExitCode = 1;
                result.Error = failure is TimeoutException ? "deadline" : "capture";
                var native = failure as Win32Exception;
                result.NativeError = native == null ? 0 : native.NativeErrorCode;
                return result;
            }
        }

        private static void Validate(Request request)
        {
            if (request == null || request.Version != 1 || request.OwnerPid <= 0 ||
                request.Timeout < 1 || request.Timeout > 7200000 ||
                request.Nonce == null || !Regex.IsMatch(request.Nonce, "\\A[0-9a-f]{32}\\z") ||
                request.OwnerBirth == null || !Regex.IsMatch(request.OwnerBirth, "\\Awindows:[1-9][0-9]*\\z") ||
                request.Arguments == null || request.Arguments.Length > 128 ||
                request.Environment == null || request.Environment.Count > 4096 ||
                request.Executable == null || !Regex.IsMatch(request.Executable, "\\A(?:[A-Za-z]:[\\\\/]|\\\\\\\\)") ||
                request.Cwd == null || !Regex.IsMatch(request.Cwd, "\\A(?:[A-Za-z]:[\\\\/]|\\\\\\\\)"))
                throw new InvalidDataException("request");
            if (request.LogPath != null &&
                (!Regex.IsMatch(request.LogPath, "\\A(?:[A-Za-z]:[\\\\/]|\\\\\\\\)") ||
                 request.LogPath.IndexOf('\0') >= 0))
                throw new InvalidDataException("log");
            foreach (var value in request.Arguments)
                if (value == null || value.IndexOf('\0') >= 0) throw new InvalidDataException("argument");
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var pair in request.Environment)
                if (string.IsNullOrEmpty(pair.Key) || !names.Add(pair.Key) || pair.Value == null ||
                    pair.Key.IndexOf('\0') >= 0 || pair.Value.IndexOf('\0') >= 0)
                    throw new InvalidDataException("environment");
            var environment = new Dictionary<string, string>(request.Environment, StringComparer.OrdinalIgnoreCase);
            string scopes;
            if (environment.TryGetValue("COPILOT_EXTENSIONS_OWNED_JOB_SCOPES", out scopes) &&
                (!string.IsNullOrEmpty(scopes)) &&
                (scopes.Length > 4096 || scopes.Split(';').Length >= 64 ||
                 new HashSet<string>(scopes.Split(';'), StringComparer.Ordinal).Count != scopes.Split(';').Length ||
                 !Regex.IsMatch(scopes, "\\ALocal\\\\ce-owned-job-[0-9a-f]{32}(;Local\\\\ce-owned-job-[0-9a-f]{32})*\\z")))
                throw new InvalidDataException("Job scopes");
            request.Environment = environment;
        }

        private static int Main(string[] args)
        {
            if (args.Length == 1 && args[0] == "--capabilities")
            {
                try
                {
                    using (var output = new StreamWriter(Console.OpenStandardOutput(), Wire.Utf8))
                        output.WriteLine("{\"version\":1,\"capabilities\":[\"finite-task-v1\"]}");
                    return 0;
                }
                catch { return 1; }
            }
            if (args.Length != 0 && args[0] == "--task")
            {
                try { return Scheduled.Run(args); }
                catch { return 1; }
            }
            if (args.Length != 0) return 2;
            var clock = Stopwatch.StartNew();
            try
            {
                using (var input = new StreamReader(Console.OpenStandardInput(), Wire.Utf8, false, 4096))
                {
                    var request = Wire.Decode<Request>(Wire.Await(input.ReadLineAsync(), clock, 30000));
                    Validate(request);
                    long deadline = clock.ElapsedMilliseconds + request.Timeout;
                    string permission = Wire.Await(input.ReadLineAsync(), clock, deadline);
                    if (permission != "offer:" + request.Nonce) throw new InvalidDataException("permission");
                    using (var offer = Offer.Create(request))
                    {
                        bool sent = false;
                        try { Wire.Write(new Ready { Nonce = request.Nonce, Job = offer.Receipt }); sent = true; }
                        catch (IOException) { }
                        string decision = Wire.Await(input.ReadLineAsync(), clock, deadline);
                        if (decision == "abort:" + request.Nonce) { offer.Abort(); return 1; }
                        if (decision != request.Nonce || !sent) throw new InvalidDataException("adoption");
                        Wire.Write(Run(request, offer.Receipt.Name, clock, deadline));
                    }
                }
                return 0;
            }
            catch (Exception failure)
            {
                var native = failure as Win32Exception;
                Console.Error.WriteLine("bootstrap-capture: startup failed; native-error=" +
                    (native == null ? 0 : native.NativeErrorCode).ToString(CultureInfo.InvariantCulture));
                return 1;
            }
        }

        internal static class Scheduled
        {
            internal static int Run(string[] args)
            {
                if (args.Length < 6) return 2;
                int timeout, cleanup;
                if (!int.TryParse(args[1], out timeout) || timeout < 2 || timeout > 7200000 ||
                    !int.TryParse(args[2], out cleanup) || cleanup < 1 || cleanup >= timeout)
                    return 2;
                string log = args[3], cwd = args[4], executable = args[5];
                foreach (string path in new[] { log, cwd, executable })
                    if (!Regex.IsMatch(path, "\\A(?:[A-Za-z]:[\\\\/]|\\\\\\\\)") ||
                        path.IndexOf('\0') >= 0) return 2;
                var clock = Stopwatch.StartNew();
                long workDeadline = timeout - cleanup;
                Process worker = null;
                IntPtr job = IntPtr.Zero;
                string failure = null;
                int code = 1;
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(log));
                    var environment = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                    foreach (System.Collections.DictionaryEntry pair in Environment.GetEnvironmentVariables())
                        environment[(string)pair.Key] = (string)pair.Value;
                    var arguments = new string[args.Length - 6];
                    Array.Copy(args, 6, arguments, 0, arguments.Length);
                    string nonce = Guid.NewGuid().ToString("N");
                    var request = new Request {
                        Version = 1, Nonce = nonce, OwnerPid = Process.GetCurrentProcess().Id,
                        OwnerBirth = Native.Birth(Native.GetCurrentProcess()),
                        Executable = executable, Arguments = arguments, Cwd = cwd,
                        Environment = environment, Input = "", LogPath = log,
                        Timeout = (int)Math.Max(1, workDeadline - clock.ElapsedMilliseconds),
                    };
                    var start = new ProcessStartInfo {
                        FileName = Process.GetCurrentProcess().MainModule.FileName,
                        WorkingDirectory = cwd, UseShellExecute = false, CreateNoWindow = true,
                        RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                        StandardOutputEncoding = Wire.Utf8, StandardErrorEncoding = Wire.Utf8,
                    };
                    start.EnvironmentVariables["COMPLUS_NoGuiFromShim"] = "1";
                    worker = Process.Start(start);
                    worker.StandardInput.NewLine = "\n";
                    worker.StandardInput.AutoFlush = true;
                    var errors = worker.StandardError.ReadToEndAsync();
                    var ready = worker.StandardOutput.ReadLineAsync();
                    Write(worker, Wire.Encode(request, true), clock, workDeadline);
                    Write(worker, "offer:" + nonce, clock, workDeadline);
                    var receipt = Wire.Read<Ready>(Wire.Await(ready, clock, workDeadline));
                    if (receipt.Version != 1 || receipt.Kind != "ready" || receipt.Nonce != nonce ||
                        receipt.Job == null || receipt.Job.ParentPid != request.OwnerPid ||
                        receipt.Job.ParentBirth != request.OwnerBirth ||
                        receipt.Job.WorkerPid != worker.Id ||
                        receipt.Job.WorkerBirth != Native.Birth(worker.Handle) ||
                        receipt.Job.Name == null ||
                        !Regex.IsMatch(receipt.Job.Name, "\\ALocal\\\\ce-owned-job-[0-9a-f]{32}\\z") ||
                        receipt.Job.ParentHandle == null ||
                        !Regex.IsMatch(receipt.Job.ParentHandle, "\\A[1-9][0-9]*\\z"))
                        throw new InvalidDataException("custody");
                    job = new IntPtr(long.Parse(receipt.Job.ParentHandle, CultureInfo.InvariantCulture));
                    var completion = worker.StandardOutput.ReadLineAsync();
                    Write(worker, nonce, clock, workDeadline);
                    var result = Wire.Read<Result>(Wire.Await(completion, clock, workDeadline));
                    if (result.Version != 1 || result.Kind != "result" || result.Nonce != nonce ||
                        result.Error == null || result.Stdout == null || result.Stderr == null)
                        throw new InvalidDataException("completion");
                    code = result.ExitCode;
                    if (result.Error.Length != 0)
                    {
                        code = result.Error == "deadline" ? 124 : 1;
                        failure = result.Error + "; native-error=" +
                            result.NativeError.ToString(CultureInfo.InvariantCulture);
                    }
                    worker.StandardInput.Close();
                    Wait(worker, clock, workDeadline);
                    if (worker.ExitCode != 0) throw new InvalidOperationException("capture worker");
                    string diagnostic = Wire.Await(errors, clock, workDeadline);
                    if (diagnostic.Length != 0) failure = diagnostic;
                }
                catch (Exception error)
                {
                    code = error is TimeoutException ? 124 : 1;
                    failure = error.GetType().Name + ": " + error.Message;
                }
                finally
                {
                    try
                    {
                        if (job != IntPtr.Zero)
                            JobRetirement.TerminateAndWait(job, Remaining(clock, timeout));
                    }
                    catch (Exception error)
                    {
                        code = 1;
                        failure = "owned Job retirement is unconfirmed: " + error.Message;
                    }
                    finally
                    {
                        Native.Close(ref job);
                        if (worker != null)
                        {
                            worker.StandardInput.Close();
                            // Before an offer is received, closing stdin aborts the
                            // existing capture protocol; no command is admitted.
                            if (!worker.WaitForExit(Remaining(clock, timeout)))
                            {
                                code = 1;
                                failure = "capture worker retirement is unconfirmed";
                            }
                            worker.Dispose();
                        }
                    }
                }
                if (failure != null)
                {
                    try { File.AppendAllText(log, "bootstrap-task: " + failure + "\n", Wire.Utf8); }
                    catch (IOException) { code = 1; }
                    catch (UnauthorizedAccessException) { code = 1; }
                }
                return code;
            }

            private static int Remaining(Stopwatch clock, long deadline)
            {
                return (int)Math.Max(0, deadline - clock.ElapsedMilliseconds);
            }

            private static void Write(Process worker, string value, Stopwatch clock, long deadline)
            {
                var task = worker.StandardInput.WriteLineAsync(value);
                Wire.Await(task.ContinueWith(done => { done.GetAwaiter().GetResult(); return true; }),
                    clock, deadline);
            }

            private static void Wait(Process worker, Stopwatch clock, long deadline)
            {
                if (!worker.WaitForExit(Remaining(clock, deadline))) throw new TimeoutException("deadline");
            }
        }
    }
}
