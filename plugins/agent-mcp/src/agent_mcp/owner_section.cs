using System;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

namespace AgentMcp {
    // Loaded in the existing native creation owner, never as another process.
    public sealed class NativeOwnerSection : IDisposable {
        public string Binding { get; private set; }
        public IntPtr InheritedHandle {
            get {
                if (read == IntPtr.Zero) throw new InvalidOperationException("Owner section closed");
                return read;
            }
        }
        private IntPtr mapping, read;
        private readonly string principal;
        private bool published;
        private const int Size = 4096;

        [StructLayout(LayoutKind.Sequential)]
        private struct Security { public int size; public IntPtr descriptor; public int inherit; }
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr pointer);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool DuplicateHandle(IntPtr source, IntPtr handle, IntPtr target,
            out IntPtr duplicate, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFileMappingW(IntPtr file, ref Security security,
            uint protection, uint high, uint low, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr MapViewOfFile(IntPtr handle, uint access,
            uint high, uint low, UIntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UnmapViewOfFile(IntPtr view);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint GetProcessId(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetProcessTimes(IntPtr handle, out ulong creation,
            out ulong exit, out ulong kernel, out ulong user);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(
            string text, uint revision, out IntPtr descriptor, IntPtr size);
        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);

        private static void Require(bool result) {
            if (!result) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        private static string Principal(IntPtr process) {
            IntPtr token;
            Require(OpenProcessToken(process, 8, out token));
            try {
                using (var identity = new WindowsIdentity(token)) return identity.User.Value;
            } finally { Require(CloseHandle(token)); }
        }

        public NativeOwnerSection() {
            principal = Principal(GetCurrentProcess());
            byte[] random = new byte[32];
            using (var generator = RandomNumberGenerator.Create()) generator.GetBytes(random);
            Binding = BitConverter.ToString(random).Replace("-", "").ToLowerInvariant();
            IntPtr descriptor;
            Require(ConvertStringSecurityDescriptorToSecurityDescriptorW(
                "O:" + principal + "D:P(A;;GA;;;" + principal + ")", 1,
                out descriptor, IntPtr.Zero));
            try {
                var security = new Security {
                    size = Marshal.SizeOf<Security>(), descriptor = descriptor
                };
                mapping = CreateFileMappingW(new IntPtr(-1), ref security, 4, 0, Size,
                    "Local\\agent-mcp-owner-v1-" + Binding);
                int error = Marshal.GetLastWin32Error();
                if (mapping == IntPtr.Zero) throw new Win32Exception(error);
                if (error == 183) throw new InvalidOperationException("Owner section already exists");
                IntPtr current = GetCurrentProcess();
                Require(DuplicateHandle(current, mapping, current, out read, 4, true, 0));
            } catch { Dispose(); throw; }
            finally { LocalFree(descriptor); }
        }

        public bool Publish(IntPtr process, uint pid, string birth) {
            lock (this) {
                if (mapping == IntPtr.Zero || published)
                    throw new InvalidOperationException("Owner section closed or already published");
                ulong created, exit, kernel, user;
                Require(GetProcessTimes(process, out created, out exit, out kernel, out user));
                string actual = "windows:" + created.ToString(CultureInfo.InvariantCulture);
                if (pid == 0 || GetProcessId(process) != pid || actual != birth
                    || Principal(process) != principal || WaitForSingleObject(process, 0) != 258)
                    throw new InvalidOperationException("Native creation identity changed or exited");
                string json = "{\"schema\":\"agent-mcp.windows-native-owner\",\"version\":1,"
                    + "\"binding\":\"" + Binding + "\",\"pid\":"
                    + pid.ToString(CultureInfo.InvariantCulture) + ",\"birth\":\"" + actual + "\"}";
                byte[] encoded = Encoding.ASCII.GetBytes(json), packet = new byte[Size];
                byte[] prefix = Encoding.ASCII.GetBytes("AMO1");
                Array.Copy(prefix, packet, 4);
                int length = encoded.Length;
                for (int index = 0; index < 4; index++) packet[4 + index] = (byte)(length >> (index * 8));
                Array.Copy(encoded, 0, packet, 8, length);
                // Once writing is attempted its outcome cannot be replayed.
                published = true;
                IntPtr view = MapViewOfFile(mapping, 2, 0, 0, new UIntPtr(Size));
                if (view == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
                try { Marshal.Copy(packet, 0, view, Size); }
                finally { Require(UnmapViewOfFile(view)); }
                return true;
            }
        }

        public void Dispose() {
            lock (this) {
                try {
                    if (read != IntPtr.Zero) {
                        Require(CloseHandle(read));
                        read = IntPtr.Zero;
                    }
                } finally {
                    if (mapping != IntPtr.Zero) {
                        Require(CloseHandle(mapping));
                        mapping = IntPtr.Zero;
                    }
                }
            }
        }
    }
}
