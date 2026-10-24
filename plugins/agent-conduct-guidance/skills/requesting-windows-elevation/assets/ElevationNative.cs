using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;

namespace AgentConduct {
    public static class ElevationNative {
        [StructLayout(LayoutKind.Sequential)]
        private struct Session { public int Id; public IntPtr Name; public int State; }
        [DllImport("wtsapi32.dll", SetLastError = true)]
        private static extern bool WTSEnumerateSessions(
            IntPtr server, int reserved, int version, out IntPtr sessions, out int count);
        [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool WTSQuerySessionInformation(
            IntPtr server, int id, int field, out IntPtr value, out int bytes);
        [DllImport("wtsapi32.dll")]
        private static extern void WTSFreeMemory(IntPtr value);
        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool GetTokenInformation(
            IntPtr token, int kind, out int value, int length, out int returned);
        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();
        [DllImport("user32.dll")]
        private static extern IntPtr GetProcessWindowStation();
        [DllImport("user32.dll")]
        private static extern IntPtr GetThreadDesktop(uint thread);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool GetUserObjectInformation(
            IntPtr handle, int kind, StringBuilder text, uint length, out uint needed);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(
            IntPtr job, int kind, IntPtr data, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool QueryInformationJobObject(
            IntPtr job, int kind, out Accounting data, uint length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateJobObject(IntPtr job, uint code);
        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimits {
            public long ProcessTime, JobTime;
            public uint Flags;
            public UIntPtr Minimum, Maximum;
            public uint ActiveLimit;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters { public ulong A, B, C, D, E, F; }
        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimits {
            public BasicLimits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct Accounting {
            public long User, Kernel, PeriodUser, PeriodKernel;
            public uint Faults, Total, Active, Terminated;
        }
        private static IntPtr ownedJob;
        private static readonly object logLock = new object();

        private static string SessionText(int id, int field) {
            IntPtr value;
            int bytes;
            if (!WTSQuerySessionInformation(IntPtr.Zero, id, field, out value, out bytes))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            try { return Marshal.PtrToStringUni(value) ?? ""; }
            finally { WTSFreeMemory(value); }
        }

        public static bool SupportsSameAccount(int elevationType, bool administrator) {
            return administrator || elevationType == 3;
        }

        public static bool CanElevateSameAccount() {
            using (var identity = WindowsIdentity.GetCurrent()) {
                bool administrator = new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
                int value, returned;
                if (!GetTokenInformation(identity.Token, 18, out value, 4, out returned))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                return SupportsSameAccount(value, administrator);
            }
        }

        private static string UserObjectName(IntPtr handle) {
            var text = new StringBuilder(256);
            uint needed;
            if (handle == IntPtr.Zero
                || !GetUserObjectInformation(handle, 2, text, 512, out needed))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return text.ToString();
        }

        public static string TryDesktopIdentity(string log) {
            try {
                return Process.GetCurrentProcess().SessionId + "|"
                    + UserObjectName(GetProcessWindowStation()) + "|"
                    + UserObjectName(GetThreadDesktop(GetCurrentThreadId()));
            } catch (Win32Exception error) {
                Append(log, "Elevation desktop identity is unavailable: " + error.NativeErrorCode);
                return null;
            }
        }

        private static string SessionSid(int id) {
            string user = SessionText(id, 5);
            if (user.Length == 0) return null;
            return new NTAccount(SessionText(id, 7), user).Translate(typeof(SecurityIdentifier)).Value;
        }

        public static int[] OwnedInteractiveSessions(IEnumerable<int> active, string sid, Func<int, string> lookup) {
            var result = new List<int>();
            foreach (int id in active) {
                try {
                    if (lookup(id) == sid) result.Add(id);
                } catch (Win32Exception error) when (error.NativeErrorCode == 5) {
                    Console.Error.WriteLine("elevation: excluded an inaccessible desktop session; owner match remains required.");
                } catch (IdentityNotMappedException) {
                    Console.Error.WriteLine("elevation: excluded a desktop with no resolvable owner SID.");
                }
            }
            return result.ToArray();
        }

        public static int[] InteractiveSessions(string sid) {
            IntPtr buffer;
            int count;
            if (!WTSEnumerateSessions(IntPtr.Zero, 0, 1, out buffer, out count))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            var active = new List<int>();
            try {
                int size = Marshal.SizeOf<Session>();
                for (int index = 0; index < count; index++) {
                    var session = Marshal.PtrToStructure<Session>(IntPtr.Add(buffer, index * size));
                    if (session.State != 0 || session.Id <= 0) continue;
                    active.Add(session.Id);
                }
            } finally { WTSFreeMemory(buffer); }
            return OwnedInteractiveSessions(active, sid, SessionSid);
        }

        public static void Append(string path, string line) {
            lock (logLock) { File.AppendAllText(path, line + Environment.NewLine, new UTF8Encoding(false)); }
        }

        public static void OwnWorker() {
            if (ownedJob != IntPtr.Zero) throw new InvalidOperationException("Worker already owns a Job.");
            ownedJob = CreateJobObject(IntPtr.Zero, null);
            if (ownedJob == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            var limits = new ExtendedLimits();
            limits.Basic.Flags = 0x2000; // Kill descendants when this worker's last handle closes.
            int size = Marshal.SizeOf<ExtendedLimits>();
            IntPtr memory = Marshal.AllocHGlobal(size);
            try {
                Marshal.StructureToPtr(limits, memory, false);
                if (!SetInformationJobObject(ownedJob, 9, memory, (uint)size)
                    || !AssignProcessToJobObject(ownedJob, Process.GetCurrentProcess().Handle))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            } finally { Marshal.FreeHGlobal(memory); }
            // Retain the handle until worker process exit, not a lexical Dispose
            // that would terminate the worker before its terminal marker is flushed.
        }

        private static uint ActiveProcesses() {
            Accounting data;
            if (!QueryInformationJobObject(
                ownedJob, 1, out data, (uint)Marshal.SizeOf<Accounting>(), IntPtr.Zero))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return data.Active;
        }

        private static Task Drain(StreamReader reader, string log) {
            return Task.Run(() => {
                string line;
                while ((line = reader.ReadLine()) != null) Append(log, line);
            });
        }

        public static int RunPayload(
            string shell, string script, string[] arguments, string cwd, string log, int seconds) {
            if (ownedJob == IntPtr.Zero) throw new InvalidOperationException("Worker has no containment.");
            var start = new ProcessStartInfo(shell) {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                WorkingDirectory = cwd
            };
            foreach (string value in new [] { "-NoLogo", "-NoProfile", "-NonInteractive", "-File", script })
                start.ArgumentList.Add(value);
            foreach (string value in arguments) start.ArgumentList.Add(value);
            using (var child = Process.Start(start)) {
                var elapsed = Stopwatch.StartNew();
                Append(log, "AGENT_ELEVATION_PAYLOAD_START pid=" + child.Id
                    + " utc=" + DateTime.UtcNow.ToString("O"));
                Task output = Drain(child.StandardOutput, log);
                Task error = Drain(child.StandardError, log);
                if (!child.WaitForExit(checked(seconds * 1000))) {
                    Append(log, "Elevation payload deadline expired; terminating the owned Job.");
                    if (!TerminateJobObject(ownedJob, 124))
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                    throw new InvalidOperationException("Owned Job termination unexpectedly returned.");
                }
                var deadline = Stopwatch.StartNew();
                while (ActiveProcesses() > 1 && deadline.ElapsedMilliseconds < 2000) Thread.Sleep(10);
                if (ActiveProcesses() > 1) {
                    Append(log, "Payload left descendants; terminating the owned Job, not reporting success.");
                    if (!TerminateJobObject(ownedJob, 125))
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                    throw new InvalidOperationException("Owned Job termination unexpectedly returned.");
                }
                if (!Task.WaitAll(new [] { output, error }, 5000))
                    throw new TimeoutException("Payload log drains did not settle.");
                Append(log, "AGENT_ELEVATION_PAYLOAD_EXIT code=" + child.ExitCode
                    + " elapsedMs=" + elapsed.ElapsedMilliseconds);
                return child.ExitCode;
            }
        }
    }
}
