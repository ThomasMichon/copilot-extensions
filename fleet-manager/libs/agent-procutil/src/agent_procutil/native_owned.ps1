# Python-free strict Job custody for prerequisite acquisition and construction.
if (-not ('CopilotExtensions.NativeOwnedProcess' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace CopilotExtensions {
    public sealed class NativeOwnedProcessException : Exception {
        public readonly bool CleanupConfirmed;
        public NativeOwnedProcessException(string message, Exception inner, bool settled)
            : base(message, inner) { CleanupConfirmed = settled; }
    }

    public static class NativeOwnedProcess {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct Startup {
            public uint Size;
            public string Reserved, Desktop, Title;
            public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public ushort Show, ReservedSize;
            public IntPtr ReservedData, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct StartupEx { public Startup Info; public IntPtr Attributes; }
        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInfo { public IntPtr Process, Thread; public uint Pid, Tid; }
        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimits {
            public long ProcessTime, JobTime;
            public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet;
            public uint ActiveProcesses;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct Limits {
            public BasicLimits Basic;
            public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct Accounting {
            public long User, Kernel, PeriodUser, PeriodKernel;
            public uint Faults, Total, Active, Terminated;
        }
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int kind, ref Limits limits, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool QueryInformationJobObject(
            IntPtr job, int kind, out Accounting accounting, uint length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetHandleInformation(SafeFileHandle handle, uint mask, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool InitializeProcThreadAttributeList(
            IntPtr list, int count, uint flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UpdateProcThreadAttribute(
            IntPtr list, uint flags, IntPtr key, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")]
        private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcess(
            string application, System.Text.StringBuilder command, IntPtr processAttributes,
            IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string cwd,
            ref StartupEx startup, out ProcessInfo process);

        private static void Check(bool result) {
            if (!result) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        private static bool Empty(IntPtr job) {
            Accounting info;
            Check(QueryInformationJobObject(job, 1, out info, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero));
            return info.Active == 0;
        }
        private static bool Settle(IntPtr job, int milliseconds) {
            var elapsed = Stopwatch.StartNew();
            do {
                if (Empty(job)) return true;
                Thread.Sleep(10);
            } while (elapsed.ElapsedMilliseconds < milliseconds);
            return false;
        }

        public static int Run(string executable, string arguments, string cwd, string log, int timeout) {
            if (timeout <= 0 || timeout > 7200000)
                throw new ArgumentException("owned child requires a deadline of at most two hours");
            foreach (string path in new string[] { executable, cwd, log }) {
                if (String.IsNullOrEmpty(path) || !Path.IsPathRooted(path) ||
                    !String.Equals(Path.GetFullPath(path), path, StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("native process paths must be fully qualified and normalized");
            }
            IntPtr job = IntPtr.Zero, attributes = IntPtr.Zero, handles = IntPtr.Zero, jobs = IntPtr.Zero;
            bool initialized = false, assigned = false, settled = false;
            Exception primary = null;
            ProcessInfo child = new ProcessInfo();
            try {
                job = CreateJobObject(IntPtr.Zero, null);
                Check(job != IntPtr.Zero);
                var limits = new Limits();
                limits.Basic.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, never BREAKAWAY_OK.
                Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(Limits))));
                using (var output = new FileStream(log, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
                using (var input = CreateFile("NUL", 0x80000000, 3, IntPtr.Zero, 3, 0x80, IntPtr.Zero)) {
                    Check(!input.IsInvalid);
                    Check(SetHandleInformation(output.SafeFileHandle, 1, 1));
                    Check(SetHandleInformation(input, 1, 1));
                    IntPtr size = IntPtr.Zero;
                    InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);
                    if (size == IntPtr.Zero) throw new IOException("cannot size owned-child handle list");
                    attributes = Marshal.AllocHGlobal(size);
                    Check(InitializeProcThreadAttributeList(attributes, 2, 0, ref size));
                    initialized = true;
                    handles = Marshal.AllocHGlobal(IntPtr.Size * 2);
                    Marshal.WriteIntPtr(handles, output.SafeFileHandle.DangerousGetHandle());
                    Marshal.WriteIntPtr(handles, IntPtr.Size, input.DangerousGetHandle());
                    Check(UpdateProcThreadAttribute(
                        attributes, 0, new IntPtr(0x20002), handles, new IntPtr(IntPtr.Size * 2),
                        IntPtr.Zero, IntPtr.Zero));
                    jobs = Marshal.AllocHGlobal(IntPtr.Size);
                    Marshal.WriteIntPtr(jobs, job);
                    Check(UpdateProcThreadAttribute(
                        attributes, 0, new IntPtr(0x2000D), jobs, new IntPtr(IntPtr.Size),
                        IntPtr.Zero, IntPtr.Zero));
                    var startup = new StartupEx();
                    startup.Info.Size = (uint)Marshal.SizeOf(typeof(StartupEx));
                    startup.Info.Flags = 0x100;
                    startup.Info.Input = input.DangerousGetHandle();
                    startup.Info.Output = startup.Info.Error = output.SafeFileHandle.DangerousGetHandle();
                    startup.Attributes = attributes;
                    var command = new System.Text.StringBuilder("\"" + executable + "\" " + arguments);
                    Check(CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, true,
                        0x08080004, IntPtr.Zero, cwd, ref startup, out child));
                    // JOB_LIST assigns atomically at creation, before any resume/death gap.
                    assigned = true;
                    if (ResumeThread(child.Thread) == UInt32.MaxValue)
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                    var elapsed = Stopwatch.StartNew();
                    while (!Empty(job)) {
                        if (elapsed.ElapsedMilliseconds >= timeout)
                            throw new TimeoutException("owned native process tree exceeded its deadline");
                        Thread.Sleep(10);
                    }
                    settled = true;
                    Check(WaitForSingleObject(child.Process, 0) == 0);
                    uint code;
                    Check(GetExitCodeProcess(child.Process, out code));
                    return unchecked((int)code);
                }
            } catch (Exception error) {
                primary = error;
                try {
                    if (child.Process == IntPtr.Zero) settled = true;
                    else if (!settled && assigned) {
                        Check(TerminateJobObject(job, 125));
                        settled = Settle(job, 10000);
                    } else if (!settled) {
                        Check(TerminateProcess(child.Process, 125)); // Still suspended; it cannot have descendants.
                        settled = WaitForSingleObject(child.Process, 10000) == 0;
                    }
                } catch (Exception cleanup) {
                    throw new NativeOwnedProcessException("native child cleanup could not be attested",
                        new AggregateException(error, cleanup), false);
                }
                throw new NativeOwnedProcessException(error.Message, error, settled);
            } finally {
                Exception closeError = null;
                foreach (IntPtr handle in new IntPtr[] { child.Thread, child.Process, job }) {
                    if (handle != IntPtr.Zero && !CloseHandle(handle)) {
                        var error = new Win32Exception(Marshal.GetLastWin32Error());
                        closeError = closeError == null ? (Exception)error : new AggregateException(closeError, error);
                    }
                }
                if (initialized) DeleteProcThreadAttributeList(attributes);
                if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);
                if (jobs != IntPtr.Zero) Marshal.FreeHGlobal(jobs);
                if (closeError != null)
                    throw new NativeOwnedProcessException("native custody handle close failed",
                        primary == null ? closeError : new AggregateException(primary, closeError), settled);
            }
        }
    }
}
'@
}

function Invoke-NativeOwnedProcess {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$LogPath,
        [int]$TimeoutMilliseconds = 180000
    )
    if ($env:OS -ne 'Windows_NT') { throw 'Native PowerShell process custody requires Windows.' }
    $quoted = @($Arguments | ForEach-Object {
        if ($_ -and $_ -notmatch '[\s"]') { $_ }
        else { '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"' }
    })
    return [CopilotExtensions.NativeOwnedProcess]::Run(
        $FilePath, ($quoted -join ' '), $WorkingDirectory, $LogPath, $TimeoutMilliseconds
    )
}
