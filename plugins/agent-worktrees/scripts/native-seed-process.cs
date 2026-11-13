using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace AwNativeSeed {
    // The existing caller owns publication, resume, stream transfer and waiting.
    public sealed class SuspendedProcess : IDisposable {
        public Process Process { get; private set; }
        public Stream StandardOutput { get; private set; }
        public Stream StandardError { get; private set; }
        public string Birth { get; private set; }
        public IntPtr CreationHandle { get { return process; } }
        private IntPtr process, thread;
        private bool resumed, disposed;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct Startup {
            public int cb;
            public string reserved, desktop, title;
            public int x, y, width, height, charsX, charsY, fill, flags;
            public short show, reservedSize;
            public IntPtr reservedBytes, input, output, error;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedStartup { public Startup startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInformation { public IntPtr process, thread; public uint pid, tid; }
        [StructLayout(LayoutKind.Sequential)]
        private struct Security { public int size; public IntPtr descriptor; public int inherit; }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr GetStdHandle(int id);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool DuplicateHandle(IntPtr source, IntPtr handle, IntPtr target,
            out IntPtr duplicate, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CreatePipe(out IntPtr read, out IntPtr write,
            ref Security security, int size);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count,
            int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags,
            IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")]
        private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcessW(string application, StringBuilder command,
            IntPtr processSecurity, IntPtr threadSecurity, bool inherit, uint flags,
            IntPtr environment, string cwd, ref ExtendedStartup startup, out ProcessInformation info);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetProcessTimes(IntPtr handle, out long creation,
            out long exit, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr handle, uint code);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

        private static void Require(bool result) {
            if (!result) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        private static string Quote(string value) {
            if (value.IndexOf('\0') >= 0) throw new ArgumentException("NUL in native argument");
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char ch in value) {
                if (ch == '\\') { slashes++; continue; }
                result.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes);
                result.Append(ch);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }
        private static IntPtr Duplicate(IntPtr handle, List<IntPtr> inherited) {
            IntPtr copy;
            IntPtr current = GetCurrentProcess();
            Require(DuplicateHandle(current, handle, current, out copy, 0, true, 2));
            inherited.Add(copy);
            return copy;
        }
        private static IntPtr Output(bool redirect, int standard, List<IntPtr> inherited,
            out Stream stream) {
            stream = null;
            if (!redirect) return Duplicate(GetStdHandle(standard), inherited);
            IntPtr read, write;
            var security = new Security { size = Marshal.SizeOf<Security>(), inherit = 1 };
            Require(CreatePipe(out read, out write, ref security, 0));
            inherited.Add(write);
            var owner = new SafeFileHandle(read, true);
            try {
                Require(SetHandleInformation(read, 1, 0));
                stream = new FileStream(owner, FileAccess.Read);
                return write;
            } catch { owner.Dispose(); throw; }
        }

        public static SuspendedProcess Create(ProcessStartInfo info, IntPtr[] extraHandles) {
            if (info.UseShellExecute || info.RedirectStandardInput || info.Arguments.Length != 0
                || !Path.IsPathFullyQualified(info.FileName)
                || !Path.IsPathFullyQualified(info.WorkingDirectory)) {
                throw new ArgumentException("Explicit native executable, cwd and ArgumentList required");
            }
            var command = new StringBuilder(Quote(info.FileName));
            foreach (string value in info.ArgumentList) command.Append(' ').Append(Quote(value));
            var environment = new StringBuilder();
            var keys = new List<string>(info.Environment.Keys);
            keys.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (string key in keys) {
                string value = info.Environment[key];
                if (value == null) continue;
                if (key.Length == 0 || key.IndexOf('\0') >= 0 || value.IndexOf('\0') >= 0
                    || key.IndexOf('=', 1) >= 0) {
                    throw new ArgumentException("Invalid native environment entry");
                }
                environment.Append(key).Append('=').Append(value).Append('\0');
            }
            environment.Append("\0\0");
            var child = new SuspendedProcess();
            var inherited = new List<IntPtr>();
            IntPtr attributes = IntPtr.Zero, handles = IntPtr.Zero, block = IntPtr.Zero;
            bool initialized = false;
            try {
                var startup = new ExtendedStartup();
                startup.startup.cb = Marshal.SizeOf<ExtendedStartup>();
                startup.startup.flags = 0x100;
                startup.startup.input = Duplicate(GetStdHandle(-10), inherited);
                Stream output, error;
                startup.startup.output = Output(info.RedirectStandardOutput, -11, inherited, out output);
                child.StandardOutput = output;
                startup.startup.error = Output(info.RedirectStandardError, -12, inherited, out error);
                child.StandardError = error;
                foreach (IntPtr handle in extraHandles) Duplicate(handle, inherited);
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
                if (size == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
                attributes = Marshal.AllocHGlobal(size);
                Require(InitializeProcThreadAttributeList(attributes, 1, 0, ref size));
                initialized = true;
                handles = Marshal.AllocHGlobal(IntPtr.Size * inherited.Count);
                Marshal.Copy(inherited.ToArray(), 0, handles, inherited.Count);
                Require(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), handles,
                    new IntPtr(IntPtr.Size * inherited.Count), IntPtr.Zero, IntPtr.Zero));
                startup.attributes = attributes;
                block = Marshal.StringToHGlobalUni(environment.ToString());
                ProcessInformation created;
                uint flags = 0x4 | 0x400 | 0x80000;
                if (info.CreateNoWindow) flags |= 0x08000000;
                Require(CreateProcessW(info.FileName, command, IntPtr.Zero, IntPtr.Zero, true,
                    flags, block, info.WorkingDirectory, ref startup, out created));
                child.process = created.process;
                child.thread = created.thread;
                long birth, exit, kernel, user;
                Require(GetProcessTimes(child.process, out birth, out exit, out kernel, out user));
                child.Birth = "windows:" + birth.ToString(System.Globalization.CultureInfo.InvariantCulture);
                child.Process = Process.GetProcessById(checked((int)created.pid));
                // Pin .NET's own wait/exit-code handle before a fast child can exit.
                IntPtr waitHandle = child.Process.Handle;
                if (child.Process.StartTime.ToUniversalTime().ToFileTimeUtc() != birth) {
                    throw new InvalidOperationException("Native creation identity changed");
                }
                return child;
            } catch { child.Dispose(); throw; }
            finally {
                if (block != IntPtr.Zero) Marshal.FreeHGlobal(block);
                if (initialized) DeleteProcThreadAttributeList(attributes);
                if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);
                foreach (IntPtr handle in inherited) CloseHandle(handle);
            }
        }

        public void Resume() {
            lock (this) {
                if (disposed || resumed) throw new InvalidOperationException("Native resume already settled");
                if (ResumeThread(thread) != 1) {
                    throw new InvalidOperationException("Native suspension state changed");
                }
                resumed = true;
                CloseHandle(thread);
                thread = IntPtr.Zero;
            }
        }
        public void Dispose() {
            lock (this) {
                if (disposed) return;
                if (!resumed && process != IntPtr.Zero) {
                    uint state = WaitForSingleObject(process, 0);
                    if (state == 258) {
                        Require(TerminateProcess(process, 3));
                        state = WaitForSingleObject(process, 5000);
                    }
                    if (state != 0) {
                        throw new InvalidOperationException("Suspended child cleanup unconfirmed");
                    }
                }
                disposed = true;
                if (thread != IntPtr.Zero) CloseHandle(thread);
                if (process != IntPtr.Zero) CloseHandle(process);
                thread = process = IntPtr.Zero;
                if (StandardOutput != null) StandardOutput.Dispose();
                if (StandardError != null) StandardError.Dispose();
                if (Process != null) Process.Dispose();
            }
        }
    }
}
