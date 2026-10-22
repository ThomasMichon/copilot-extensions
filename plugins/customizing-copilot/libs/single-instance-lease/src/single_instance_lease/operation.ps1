# Native bootstrap counterpart of operation.py; no Python discovery or installation.
if (-not ('CopilotExtensions.NativeOperationLock' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace CopilotExtensions {
    public sealed class NativeOperationLock : IDisposable {
        // lease.py's _WIN_LOCK_OFFSET; diagnostic bytes at offset zero stay readable.
        private const long LockOffset = 1L << 30;
        private static readonly object Guard = new object();
        private static readonly HashSet<string> Holders = new HashSet<string>();
        private FileStream stream;
        private string identity;
        private readonly string path;
        private readonly int thread;

        [StructLayout(LayoutKind.Sequential)]
        private struct FileInfo {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME Creation;
            public System.Runtime.InteropServices.ComTypes.FILETIME Access;
            public System.Runtime.InteropServices.ComTypes.FILETIME Write;
            public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileInfo info);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(
            SafeFileHandle handle, StringBuilder buffer, uint size, uint flags);

        private static void CheckPath(string value, bool allowMissingLeaf) {
            for (string current = value; current != null; current = Path.GetDirectoryName(current)) {
                try {
                    FileAttributes attributes = File.GetAttributes(current);
                    if ((attributes & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("operation path traverses a reparse point");
                    if (current == value && (attributes & FileAttributes.Directory) != 0)
                        throw new IOException("operation lock is not a plain file");
                } catch (FileNotFoundException) {
                    if (current != value || !allowMissingLeaf) throw;
                }
                string parent = Path.GetDirectoryName(current);
                if (parent == current) break;
            }
        }

        private string OpenIdentity() {
            FileInfo info;
            if (!GetFileInformationByHandle(stream.SafeFileHandle, out info))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if ((info.Attributes & (uint)(FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
                throw new IOException("operation lock is not a plain file");
            return info.Volume + ":" + info.IndexHigh + ":" + info.IndexLow;
        }

        private void CheckOpenPath() {
            CheckPath(path, false);
            var buffer = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandle(stream.SafeFileHandle, buffer, (uint)buffer.Capacity, 0);
            if (length == 0 || length >= buffer.Capacity)
                throw new IOException("cannot verify operation lock's final path");
            string final = buffer.ToString();
            if (final.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
                final = @"\\" + final.Substring(8);
            else if (final.StartsWith(@"\\?\", StringComparison.Ordinal))
                final = final.Substring(4);
            if (!String.Equals(final, path, StringComparison.OrdinalIgnoreCase))
                throw new IOException("operation lock path changed");
        }

        public NativeOperationLock(string value) {
            if (String.IsNullOrEmpty(value) || !Path.IsPathRooted(value))
                throw new ArgumentException("operation lock requires an absolute path");
            path = Path.GetFullPath(value);
            if (!String.Equals(value, path, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("operation lock requires a normalized absolute path");
            thread = Thread.CurrentThread.ManagedThreadId;
            lock (Guard) {
                CheckPath(path, true);
                stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
                bool reserved = false;
                try {
                    CheckOpenPath();
                    identity = OpenIdentity();
                    if (!Holders.Add(identity))
                        throw new IOException("another execution holds the operation lock");
                    reserved = true;
                    stream.Lock(LockOffset, 1);
                    CheckOpenPath();
                } catch {
                    stream.Dispose();
                    stream = null;
                    if (reserved) Holders.Remove(identity);
                    throw;
                }
            }
        }

        public void AssertOwned() {
            lock (Guard) {
                if (stream == null || thread != Thread.CurrentThread.ManagedThreadId ||
                    !Holders.Contains(identity))
                    throw new InvalidOperationException("operation custody is absent or belongs to another thread");
                CheckOpenPath();
                if (OpenIdentity() != identity)
                    throw new IOException("operation lock identity changed");
            }
        }

        public void Dispose() {
            lock (Guard) {
                if (stream == null) return;
                if (thread != Thread.CurrentThread.ManagedThreadId)
                    throw new InvalidOperationException("operation custody belongs to another thread");
                stream.Dispose();
                stream = null;
                Holders.Remove(identity);
            }
        }
    }
}
'@
}

function Enter-NativeOperationLock {
    param([Parameter(Mandatory)][string]$Path)
    if ($env:OS -ne 'Windows_NT') { throw 'Native PowerShell operation custody requires Windows.' }
    return New-Object CopilotExtensions.NativeOperationLock -ArgumentList $Path
}
