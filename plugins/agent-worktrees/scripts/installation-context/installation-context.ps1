[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Action,
    [string]$SourceJson,
    [string]$SourceFile,
    [string]$MarketplaceKey,
    [string]$PluginId,
    [string]$PayloadRoot,
    [string]$CopilotHome,
    [string]$ProjectRoot,
    [string]$DurableHome,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$ExpectedPluginId,
    [string]$ExpectedPayloadRoot,
    [string]$ExpectedPayloadVersion,
    [string]$ExpectedCellRoot,
    [string]$PayloadVersion,
    [string]$PayloadOrigin,
    [string]$PayloadOriginReceipt,
    [object]$ExpectedNamespaceGeneration,
    [object]$ExpectedInstallGeneration,
    [object]$ExpectedActivationGeneration,
    [string]$ExpectedCurrentVersion,
    [switch]$ExpectCurrentAbsent,
    [string]$SnapshotId,
    [string]$RuntimeVersion,
    [string]$NamespaceState = 'active',
    [string]$InstallState = 'active',
    [string]$ActivationMode,
    [string]$ActivationState,
    [string]$LegacyDisposition,
    [string]$LegacyRoot,
    [string]$LegacyProbeJson,
    [string]$LegacyProbeFile,
    [string]$PolicyPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
$OutputEncoding = $utf8NoBom
try { [Console]::OutputEncoding = $utf8NoBom } catch {}
$script:HeldLocks = @()
$script:RuntimeSlotLockTimeoutSeconds = 30
$script:RuntimeSlotCompletionLockTimeoutSeconds = 300
$script:ValidatedFileSha256 = @{}
$script:SnapshotMaxEntries = 100000
$script:SnapshotMaxPathBytes = 4096
$script:SnapshotMaxContentBytes = 4294967296

if ($env:OS -eq 'Windows_NT' -and -not ('CeFinalPath' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;

public static class CeFinalPath {
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private const uint FILE_NAME_NORMALIZED = 0;
    private const uint VOLUME_NAME_DOS = 0;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string name, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle handle, StringBuilder path, uint size, uint flags);

    public static string Resolve(string path) {
        using (SafeFileHandle handle = CreateFile(
            path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            IntPtr.Zero, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS,
            IntPtr.Zero)) {
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            StringBuilder buffer = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandle(
                handle, buffer, (uint)buffer.Capacity,
                FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
            if (length == 0 || length >= buffer.Capacity) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            string result = buffer.ToString();
            if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                return @"\\" + result.Substring(8);
            }
            if (result.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) {
                return result.Substring(4);
            }
            return result;
        }
    }
}
'@
}

if ($env:OS -eq 'Windows_NT' -and -not ('CeDirectoryState' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;

public static class CeDirectoryState {
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private const uint FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
    private const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400;
    private const int FILE_ATTRIBUTE_TAG_INFO_CLASS = 9;

    [StructLayout(LayoutKind.Sequential)]
    private struct FileAttributeTagInfo {
        public uint FileAttributes;
        public uint ReparseTag;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ByHandleFileInformation {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string name, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandleEx(
        SafeFileHandle handle, int infoClass, out FileAttributeTagInfo info,
        uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out ByHandleFileInformation info);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle handle, StringBuilder path, uint size, uint flags);

    public static string[] Inspect(string path) {
        using (SafeFileHandle handle = CreateFile(
            path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            IntPtr.Zero, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            IntPtr.Zero)) {
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            FileAttributeTagInfo attributes;
            if (!GetFileInformationByHandleEx(
                    handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, out attributes,
                    (uint)Marshal.SizeOf(typeof(FileAttributeTagInfo)))) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if ((attributes.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
                throw new IOException("path resolves to a reparse point");
            }
            if ((attributes.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
                throw new IOException("path does not resolve to a directory");
            }
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(handle, out information)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            StringBuilder buffer = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandle(
                handle, buffer, (uint)buffer.Capacity, 0);
            if (length == 0 || length >= buffer.Capacity) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            string finalPath = buffer.ToString();
            if (finalPath.StartsWith(
                    @"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                finalPath = @"\\" + finalPath.Substring(8);
            } else if (finalPath.StartsWith(
                    @"\\?\", StringComparison.OrdinalIgnoreCase)) {
                finalPath = finalPath.Substring(4);
            }
            string identity = string.Format(
                "{0:x8}:{1:x8}{2:x8}",
                information.VolumeSerialNumber,
                information.FileIndexHigh,
                information.FileIndexLow);
            ulong size = ((ulong)information.FileSizeHigh << 32) |
                information.FileSizeLow;
            string metadata = string.Format(
                "{0}|{1}|{2:x8}{3:x8}|{4:x8}{5:x8}|{6:x8}",
                identity,
                size,
                information.LastWriteTime.dwHighDateTime,
                information.LastWriteTime.dwLowDateTime,
                information.CreationTime.dwHighDateTime,
                information.CreationTime.dwLowDateTime,
                information.FileAttributes);
            return new string[] { identity, metadata, finalPath };
        }
    }
}
'@
}

if ($env:OS -ne 'Windows_NT' -and -not ('CePosixPath' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class CePosixPath {
    [DllImport("libc", SetLastError = true)]
    private static extern IntPtr realpath(string path, IntPtr resolved);

    [DllImport("libc")]
    private static extern void free(IntPtr pointer);

    public static string Resolve(string path) {
        IntPtr pointer = realpath(path, IntPtr.Zero);
        if (pointer == IntPtr.Zero) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        try {
            return Marshal.PtrToStringAnsi(pointer);
        }
        finally {
            free(pointer);
        }
    }
}
'@
}

if ($env:OS -ne 'Windows_NT' -and -not ('CePosixAccount' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class CePosixAccount {
    [StructLayout(LayoutKind.Sequential)]
    private struct Passwd {
        public IntPtr pw_name;
        public IntPtr pw_passwd;
        public uint pw_uid;
        public uint pw_gid;
        public IntPtr pw_gecos;
        public IntPtr pw_dir;
        public IntPtr pw_shell;
    }

    [DllImport("libc")]
    private static extern uint geteuid();

    [DllImport("libc", SetLastError = true)]
    private static extern IntPtr getpwuid(uint uid);

    public static string Home() {
        IntPtr pointer = getpwuid(geteuid());
        if (pointer == IntPtr.Zero) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        Passwd entry = (Passwd)Marshal.PtrToStructure(pointer, typeof(Passwd));
        string home = Marshal.PtrToStringAnsi(entry.pw_dir);
        if (string.IsNullOrEmpty(home)) {
            throw new Win32Exception("passwd entry is missing a home directory");
        }
        return home;
    }
}
'@
}

if (-not ('CeSafeFile' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;

public sealed class CeSafeFile : IDisposable {
    private const uint GENERIC_READ = 0x80000000;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    private const uint FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000;
    private const uint FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
    private const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400;
    private const int FILE_ATTRIBUTE_TAG_INFO_CLASS = 9;
    private const int O_RDONLY = 0;
    private const int O_NONBLOCK_LINUX = 0x800;
    private const int O_NOFOLLOW_LINUX = 0x20000;
    private const int O_NONBLOCK_DARWIN = 0x4;
    private const int O_NOFOLLOW_DARWIN = 0x100;
    private const uint S_IFMT = 0xF000;
    private const uint S_IFREG = 0x8000;

    [StructLayout(LayoutKind.Sequential)]
    private struct FileAttributeTagInfo {
        public uint FileAttributes;
        public uint ReparseTag;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ByHandleFileInformation {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LinuxTimespec {
        public long Seconds;
        public long Nanoseconds;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LinuxStat {
        public ulong Device;
        public ulong Inode;
        public ulong LinkCount;
        public uint Mode;
        public uint UserId;
        public uint GroupId;
        public uint Padding;
        public ulong SpecialDevice;
        public long Size;
        public long BlockSize;
        public long Blocks;
        public LinuxTimespec Accessed;
        public LinuxTimespec Modified;
        public LinuxTimespec Changed;
        public long Reserved0;
        public long Reserved1;
        public long Reserved2;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct DarwinTimespec {
        public long Seconds;
        public long Nanoseconds;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct DarwinStat {
        public int Device;
        public ushort Mode;
        public ushort LinkCount;
        public ulong Inode;
        public uint UserId;
        public uint GroupId;
        public int SpecialDevice;
        public DarwinTimespec Accessed;
        public DarwinTimespec Modified;
        public DarwinTimespec Changed;
        public DarwinTimespec Created;
        public long Size;
        public long Blocks;
        public int BlockSize;
        public uint Flags;
        public uint Generation;
        public int Spare;
        public long Reserved0;
        public long Reserved1;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string name, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandleEx(
        SafeFileHandle handle, int infoClass, out FileAttributeTagInfo info,
        uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out ByHandleFileInformation info);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle handle, StringBuilder path, uint size, uint flags);

    [DllImport("libc", EntryPoint = "open", SetLastError = true)]
    private static extern int PosixOpen(string path, int flags);

    [DllImport("libc", EntryPoint = "fstat", SetLastError = true)]
    private static extern int LinuxFstat(int descriptor, out LinuxStat information);

    [DllImport("libc", EntryPoint = "fstat", SetLastError = true)]
    private static extern int DarwinArm64Fstat(
        int descriptor, out DarwinStat information);

    [DllImport("libc", EntryPoint = "fstat$INODE64", SetLastError = true)]
    private static extern int DarwinX64Fstat(
        int descriptor, out DarwinStat information);

    [DllImport("libc", EntryPoint = "lstat", SetLastError = true)]
    private static extern int LinuxLstat(string path, out LinuxStat information);

    [DllImport("libc", EntryPoint = "lstat", SetLastError = true)]
    private static extern int DarwinArm64Lstat(
        string path, out DarwinStat information);

    [DllImport("libc", EntryPoint = "lstat$INODE64", SetLastError = true)]
    private static extern int DarwinX64Lstat(
        string path, out DarwinStat information);

    public FileStream Stream { get; private set; }
    public string Identity { get; private set; }
    public string Metadata { get; private set; }
    public string FinalPath { get; private set; }
    public int Descriptor { get; private set; }

    private CeSafeFile(
        FileStream stream, string identity, string metadata,
        string finalPath, int descriptor) {
        Stream = stream;
        Identity = identity;
        Metadata = metadata;
        FinalPath = finalPath;
        Descriptor = descriptor;
    }

    private static void GetWindowsMetadata(
        SafeFileHandle handle, out string identity, out string metadata) {
        ByHandleFileInformation information;
        if (!GetFileInformationByHandle(handle, out information)) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        identity = string.Format(
            "{0:x8}:{1:x8}{2:x8}",
            information.VolumeSerialNumber,
            information.FileIndexHigh,
            information.FileIndexLow);
        ulong size = ((ulong)information.FileSizeHigh << 32) |
            information.FileSizeLow;
        metadata = string.Format(
            "{0}|{1}|{2:x8}{3:x8}|{4:x8}{5:x8}|{6:x8}",
            identity,
            size,
            information.LastWriteTime.dwHighDateTime,
            information.LastWriteTime.dwLowDateTime,
            information.CreationTime.dwHighDateTime,
            information.CreationTime.dwLowDateTime,
            information.FileAttributes);
    }

    private static int DarwinFstat(
        int descriptor, out DarwinStat information) {
        if (RuntimeInformation.ProcessArchitecture == Architecture.X64) {
            return DarwinX64Fstat(descriptor, out information);
        }
        if (RuntimeInformation.ProcessArchitecture == Architecture.Arm64) {
            return DarwinArm64Fstat(descriptor, out information);
        }
        throw new PlatformNotSupportedException(
            "Darwin safe-file validation requires x86-64 or arm64.");
    }

    private static int DarwinLstat(
        string path, out DarwinStat information) {
        if (RuntimeInformation.ProcessArchitecture == Architecture.X64) {
            return DarwinX64Lstat(path, out information);
        }
        if (RuntimeInformation.ProcessArchitecture == Architecture.Arm64) {
            return DarwinArm64Lstat(path, out information);
        }
        throw new PlatformNotSupportedException(
            "Darwin safe-file validation requires x86-64 or arm64.");
    }

    private static void GetPosixMetadata(
        int descriptor, out string identity, out string metadata) {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) {
            DarwinStat information;
            if (DarwinFstat(descriptor, out information) != 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if (((uint)information.Mode & S_IFMT) != S_IFREG) {
                throw new IOException("path does not resolve to an ordinary file");
            }
            identity = string.Format(
                "{0}:{1}", information.Device, information.Inode);
            metadata = string.Format(
                "{0}|{1}|{2}|{3}|{4}|{5}",
                identity,
                information.Size,
                information.Modified.Seconds,
                information.Modified.Nanoseconds,
                information.Changed.Seconds,
                information.Changed.Nanoseconds);
            return;
        }
        LinuxStat linuxInformation;
        if (LinuxFstat(descriptor, out linuxInformation) != 0) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        if ((linuxInformation.Mode & S_IFMT) != S_IFREG) {
            throw new IOException("path does not resolve to an ordinary file");
        }
        identity = string.Format(
            "{0}:{1}", linuxInformation.Device, linuxInformation.Inode);
        metadata = string.Format(
            "{0}|{1}|{2}|{3}|{4}|{5}",
            identity,
            linuxInformation.Size,
            linuxInformation.Modified.Seconds,
            linuxInformation.Modified.Nanoseconds,
            linuxInformation.Changed.Seconds,
            linuxInformation.Changed.Nanoseconds);
    }

    public static string[] InspectPosixPath(string path) {
        string identity;
        string metadata;
        bool regular;
        if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX)) {
            DarwinStat information;
            if (DarwinLstat(path, out information) != 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            regular = ((uint)information.Mode & S_IFMT) == S_IFREG;
            identity = string.Format(
                "{0}:{1}", information.Device, information.Inode);
            metadata = string.Format(
                "{0}|{1}|{2}|{3}|{4}|{5}",
                identity,
                information.Size,
                information.Modified.Seconds,
                information.Modified.Nanoseconds,
                information.Changed.Seconds,
                information.Changed.Nanoseconds);
        } else {
            LinuxStat information;
            if (LinuxLstat(path, out information) != 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            regular = (information.Mode & S_IFMT) == S_IFREG;
            identity = string.Format(
                "{0}:{1}", information.Device, information.Inode);
            metadata = string.Format(
                "{0}|{1}|{2}|{3}|{4}|{5}",
                identity,
                information.Size,
                information.Modified.Seconds,
                information.Modified.Nanoseconds,
                information.Changed.Seconds,
                information.Changed.Nanoseconds);
        }
        return new string[] {
            regular ? "1" : "0",
            identity,
            metadata
        };
    }

    public string RefreshMetadata() {
        string identity;
        string metadata;
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) {
            GetWindowsMetadata(Stream.SafeFileHandle, out identity, out metadata);
        } else {
            GetPosixMetadata(Descriptor, out identity, out metadata);
        }
        Identity = identity;
        Metadata = metadata;
        return metadata;
    }

    public static CeSafeFile Open(string path) {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) {
            SafeFileHandle handle = CreateFile(
                path, GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                IntPtr.Zero, OPEN_EXISTING,
                FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN,
                IntPtr.Zero);
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            try {
                FileAttributeTagInfo attributes;
                if (!GetFileInformationByHandleEx(
                        handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, out attributes,
                        (uint)Marshal.SizeOf(typeof(FileAttributeTagInfo)))) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                if ((attributes.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
                    throw new IOException("path resolves to a reparse point");
                }
                if ((attributes.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
                    throw new IOException("path resolves to a directory");
                }
                StringBuilder buffer = new StringBuilder(32768);
                uint length = GetFinalPathNameByHandle(
                    handle, buffer, (uint)buffer.Capacity, 0);
                if (length == 0 || length >= buffer.Capacity) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                string finalPath = buffer.ToString();
                if (finalPath.StartsWith(
                        @"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                    finalPath = @"\\" + finalPath.Substring(8);
                } else if (finalPath.StartsWith(
                        @"\\?\", StringComparison.OrdinalIgnoreCase)) {
                    finalPath = finalPath.Substring(4);
                }
                string identity;
                string metadata;
                GetWindowsMetadata(handle, out identity, out metadata);
                FileStream stream = new FileStream(
                    handle, FileAccess.Read, 4096, false);
                handle = null;
                return new CeSafeFile(
                    stream, identity, metadata, finalPath, -1);
            }
            finally {
                if (handle != null) handle.Dispose();
            }
        }
        bool darwin = RuntimeInformation.IsOSPlatform(OSPlatform.OSX);
        int flags = O_RDONLY |
            (darwin ? O_NONBLOCK_DARWIN | O_NOFOLLOW_DARWIN :
                      O_NONBLOCK_LINUX | O_NOFOLLOW_LINUX);
        int descriptor = PosixOpen(path, flags);
        if (descriptor < 0) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        SafeFileHandle safeHandle = new SafeFileHandle(
            new IntPtr(descriptor), true);
        try {
            string identity;
            string metadata;
            GetPosixMetadata(descriptor, out identity, out metadata);
            FileStream stream = new FileStream(
                safeHandle, FileAccess.Read, 4096, false);
            safeHandle = null;
            return new CeSafeFile(
                stream, identity, metadata, path, descriptor);
        }
        finally {
            if (safeHandle != null) safeHandle.Dispose();
        }
    }

    public void Dispose() {
        if (Stream != null) {
            Stream.Dispose();
            Stream = null;
        }
    }
}
'@
}

if (-not ('CeStrictJson' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Text;

public static class CeStrictJson {
    private sealed class Parser {
        private readonly string text;
        private int index;

        internal Parser(string value) {
            if (value == null) throw new ArgumentNullException("value");
            text = value;
        }

        internal void Parse() {
            SkipWhitespace();
            ParseValue();
            SkipWhitespace();
            if (index != text.Length) Error("trailing data");
        }

        private void ParseValue() {
            SkipWhitespace();
            if (index >= text.Length) Error("missing value");
            switch (text[index]) {
                case '{': ParseObject(); return;
                case '[': ParseArray(); return;
                case '"': ParseString(); return;
                case 't': ParseLiteral("true"); return;
                case 'f': ParseLiteral("false"); return;
                case 'n': ParseLiteral("null"); return;
                default:
                    if (text[index] == '-' || char.IsDigit(text[index])) {
                        ParseNumber();
                        return;
                    }
                    Error("unexpected value");
                    return;
            }
        }

        private void ParseObject() {
            index++;
            SkipWhitespace();
            var keys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (Consume('}')) return;
            while (true) {
                if (index >= text.Length || text[index] != '"') Error("expected object key");
                string key = ParseString();
                if (!keys.Add(key)) {
                    Error("duplicate or case-conflicting object key '" + key + "'");
                }
                SkipWhitespace();
                Require(':');
                ParseValue();
                SkipWhitespace();
                if (Consume('}')) return;
                Require(',');
                SkipWhitespace();
            }
        }

        private void ParseArray() {
            index++;
            SkipWhitespace();
            if (Consume(']')) return;
            while (true) {
                ParseValue();
                SkipWhitespace();
                if (Consume(']')) return;
                Require(',');
                SkipWhitespace();
            }
        }

        private string ParseString() {
            Require('"');
            var result = new System.Text.StringBuilder();
            while (index < text.Length) {
                char value = text[index++];
                if (value == '"') return result.ToString();
                if (value < 0x20) Error("unescaped control character");
                if (value != '\\') {
                    result.Append(value);
                    continue;
                }
                if (index >= text.Length) Error("unterminated escape");
                char escape = text[index++];
                switch (escape) {
                    case '"': result.Append('"'); break;
                    case '\\': result.Append('\\'); break;
                    case '/': result.Append('/'); break;
                    case 'b': result.Append('\b'); break;
                    case 'f': result.Append('\f'); break;
                    case 'n': result.Append('\n'); break;
                    case 'r': result.Append('\r'); break;
                    case 't': result.Append('\t'); break;
                    case 'u':
                        int code = ParseHex4();
                        if (code >= 0xD800 && code <= 0xDBFF) {
                            if (index + 1 >= text.Length || text[index] != '\\' ||
                                text[index + 1] != 'u') Error("unpaired high surrogate");
                            index += 2;
                            int low = ParseHex4();
                            if (low < 0xDC00 || low > 0xDFFF) Error("invalid low surrogate");
                            result.Append((char)code);
                            result.Append((char)low);
                        } else if (code >= 0xDC00 && code <= 0xDFFF) {
                            Error("unpaired low surrogate");
                        } else {
                            result.Append((char)code);
                        }
                        break;
                    default: Error("invalid string escape"); break;
                }
            }
            Error("unterminated string");
            return "";
        }

        private int ParseHex4() {
            if (index + 4 > text.Length) Error("invalid unicode escape");
            int result = 0;
            for (int offset = 0; offset < 4; offset++) {
                char value = text[index++];
                int digit;
                if (value >= '0' && value <= '9') digit = value - '0';
                else if (value >= 'a' && value <= 'f') digit = value - 'a' + 10;
                else if (value >= 'A' && value <= 'F') digit = value - 'A' + 10;
                else { Error("invalid unicode escape"); return 0; }
                result = (result * 16) + digit;
            }
            return result;
        }

        private void ParseNumber() {
            if (Consume('-') && index >= text.Length) Error("invalid number");
            if (Consume('0')) {
                if (index < text.Length && char.IsDigit(text[index])) Error("leading zero");
            } else {
                if (index >= text.Length || text[index] < '1' || text[index] > '9') {
                    Error("invalid number");
                }
                while (index < text.Length && char.IsDigit(text[index])) index++;
            }
            if (Consume('.')) {
                if (index >= text.Length || !char.IsDigit(text[index])) Error("invalid fraction");
                while (index < text.Length && char.IsDigit(text[index])) index++;
            }
            if (index < text.Length && (text[index] == 'e' || text[index] == 'E')) {
                index++;
                if (index < text.Length && (text[index] == '+' || text[index] == '-')) index++;
                if (index >= text.Length || !char.IsDigit(text[index])) Error("invalid exponent");
                while (index < text.Length && char.IsDigit(text[index])) index++;
            }
        }

        private void ParseLiteral(string value) {
            if (index + value.Length > text.Length ||
                string.CompareOrdinal(text, index, value, 0, value.Length) != 0) {
                Error("invalid literal");
            }
            index += value.Length;
        }

        private bool Consume(char value) {
            if (index < text.Length && text[index] == value) {
                index++;
                return true;
            }
            return false;
        }

        private void Require(char value) {
            if (!Consume(value)) Error("expected '" + value + "'");
        }

        private void SkipWhitespace() {
            while (index < text.Length) {
                char value = text[index];
                if (value != ' ' && value != '\t' && value != '\r' && value != '\n') return;
                index++;
            }
        }

        private void Error(string message) {
            throw new FormatException(message + " at character " + index);
        }
    }

    public static void Validate(string value) {
        new Parser(value).Parse();
    }

    public static string ProtectStrings(string value, string marker) {
        var result = new StringBuilder(value.Length + marker.Length);
        bool inString = false;
        bool escaped = false;
        foreach (char current in value) {
            result.Append(current);
            if (!inString) {
                if (current == '"') {
                    result.Append(marker);
                    inString = true;
                }
                continue;
            }
            if (escaped) {
                escaped = false;
            } else if (current == '\\') {
                escaped = true;
            } else if (current == '"') {
                inString = false;
            }
        }
        return result.ToString();
    }
}
'@
}

if (-not ('CeAtomicFile' -as [type])) {
    Add-Type -TypeDefinition @'
using System.IO;

public static class CeAtomicFile {
    public static void Replace(string source, string destination) {
        if (File.Exists(destination)) {
            File.Replace(source, destination, null);
        } else {
            File.Move(source, destination);
        }
    }
}
'@
}

if (-not ('CeAtomicDirectory' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class CeAtomicDirectory {
    private const int ERROR_ACCESS_DENIED = 5;
    private const int ERROR_SHARING_VIOLATION = 32;
    private const int ERROR_FILE_EXISTS = 80;
    private const int ERROR_ALREADY_EXISTS = 183;
    private const int EEXIST = 17;
    private const int ENOTEMPTY_LINUX = 39;
    private const int ENOTEMPTY_DARWIN = 66;
    private const int AT_FDCWD = -100;
    private const uint MOVEFILE_WRITE_THROUGH = 0x00000008;
    private const uint RENAME_NOREPLACE = 1;
    private const uint RENAME_EXCL = 0x00000004;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CreateDirectoryW(string path, IntPtr security);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool MoveFileExW(string existing, string replacement, uint flags);

    [DllImport("libc", EntryPoint = "mkdir", SetLastError = true)]
    private static extern int PosixMkdir(string path, uint mode);

    [DllImport("libc", EntryPoint = "renameat2", SetLastError = true)]
    private static extern int RenameAt2(
        int oldDirectory, string oldPath, int newDirectory, string newPath, uint flags);

    [DllImport("libc", EntryPoint = "renamex_np", SetLastError = true)]
    private static extern int RenameExclusive(string oldPath, string newPath, uint flags);

    public static int CreateWindows(string path) {
        if (CreateDirectoryW(path, IntPtr.Zero)) {
            return 1;
        }
        int error = Marshal.GetLastWin32Error();
        if (error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS) {
            return 0;
        }
        if (error == ERROR_ACCESS_DENIED) {
            return -ERROR_ACCESS_DENIED;
        }
        if (error == ERROR_SHARING_VIOLATION) {
            return -ERROR_SHARING_VIOLATION;
        }
        throw new Win32Exception(error);
    }

    public static int CreatePosix(string path) {
        if (PosixMkdir(path, 448) == 0) {
            return 1;
        }
        int error = Marshal.GetLastWin32Error();
        if (error == EEXIST) {
            return 0;
        }
        throw new Win32Exception(error);
    }

    public static int MoveWindows(string source, string destination) {
        if (MoveFileExW(source, destination, MOVEFILE_WRITE_THROUGH)) {
            return 1;
        }
        int error = Marshal.GetLastWin32Error();
        if (error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS) {
            return 0;
        }
        throw new Win32Exception(error);
    }

    public static int MoveLinux(string source, string destination) {
        if (RenameAt2(AT_FDCWD, source, AT_FDCWD, destination, RENAME_NOREPLACE) == 0) {
            return 1;
        }
        int error = Marshal.GetLastWin32Error();
        if (error == EEXIST || error == ENOTEMPTY_LINUX) {
            return 0;
        }
        throw new Win32Exception(error);
    }

    public static int MoveDarwin(string source, string destination) {
        if (RenameExclusive(source, destination, RENAME_EXCL) == 0) {
            return 1;
        }
        int error = Marshal.GetLastWin32Error();
        if (error == EEXIST || error == ENOTEMPTY_DARWIN) {
            return 0;
        }
        throw new Win32Exception(error);
    }
}
'@
}

function Fail([string]$Message) {
    throw [System.InvalidOperationException]::new($Message)
}

function Get-UtcTimestamp {
    return [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ', [Globalization.CultureInfo]::InvariantCulture)
}

function Get-PosixFileIdentity([string]$Path, [switch]$Follow) {
    if ($Follow) {
        Fail 'Following named POSIX paths is unsupported for identity validation.'
    }
    if (-not (
        [Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
            [Runtime.InteropServices.OSPlatform]::Linux
        ) -or
        [Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
            [Runtime.InteropServices.OSPlatform]::OSX
        )
    )) {
        Fail 'Opened-file validation is unavailable on this platform.'
    }
    try {
        $parts = [CeSafeFile]::InspectPosixPath($Path)
    }
    catch {
        Fail "Cannot inspect file '$Path': $($_.Exception.Message)"
    }
    return [pscustomobject][ordered]@{
        regular = $parts[0] -ceq '1'
        identity = $parts[1]
        metadata = $parts[2]
    }
}

function Open-RegularFileHandle(
    [string]$Path,
    [string]$Label,
    [switch]$RequireExactPath
) {
    for ($attempt = 0; $attempt -lt 512; $attempt++) {
        try {
            $opened = [CeSafeFile]::Open($Path)
        }
        catch {
            if ($attempt -lt 511) { continue }
            Fail "Cannot open $($Label.ToLowerInvariant()) '$Path' safely: $($_.Exception.Message)"
        }
        try {
            if ($env:OS -eq 'Windows_NT') {
                if (-not (Paths-Equal $opened.FinalPath $Path)) {
                    Fail "$Label may not traverse a symbolic link or reparse point."
                }
                return [pscustomobject][ordered]@{
                    value = $opened
                    identity = $opened.Identity
                    metadata = $opened.Metadata
                }
            }
            $named = Get-PosixFileIdentity $Path
            if (-not $named.regular) {
                Fail "$Label must be an ordinary file."
            }
            if ($named.identity -ceq $opened.Identity) {
                return [pscustomobject][ordered]@{
                    value = $opened
                    identity = $opened.Identity
                    metadata = $opened.Metadata
                }
            }
            if (-not $RequireExactPath -and $attempt -eq 511) {
                return [pscustomobject][ordered]@{
                    value = $opened
                    identity = $opened.Identity
                    metadata = $opened.Metadata
                }
            }
        }
        catch {
            $opened.Dispose()
            throw
        }
        $opened.Dispose()
    }
    Fail "$Label changed while it was being opened."
}

function Assert-OpenedFileStillSafe(
    [string]$Path,
    [string]$Label,
    [string]$OpenedIdentity,
    [string]$OpenedMetadata,
    [switch]$RequireSameIdentity
) {
    $entry = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $entry) {
        Fail "$Label disappeared while it was being read."
    }
    if ($entry.PSIsContainer -or
        (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        Fail "$Label may not be a symbolic link, reparse point, or non-file object."
    }
    $current = Open-RegularFileHandle `
        $Path `
        $Label `
        -RequireExactPath:$RequireSameIdentity
    try {
        if ($RequireSameIdentity -and
            $current.identity -cne $OpenedIdentity) {
            Fail "$Label changed while it was being read."
        }
        if ($RequireSameIdentity -and
            $current.metadata -cne $OpenedMetadata) {
            Fail "$Label changed while it was being read."
        }
    }
    finally {
        $current.value.Dispose()
    }
}

function Read-RegularFileBytes(
    [string]$Path,
    [string]$Label,
    [switch]$RequireSameIdentity
) {
    $opened = Open-RegularFileHandle `
        $Path `
        $Label `
        -RequireExactPath:$RequireSameIdentity
    $memory = [IO.MemoryStream]::new()
    try {
        $initialMetadata = $opened.metadata
        $opened.value.Stream.CopyTo($memory)
        $finalMetadata = $opened.value.RefreshMetadata()
        if ($RequireSameIdentity -and $finalMetadata -cne $initialMetadata) {
            Fail "$Label changed while it was being read."
        }
        Assert-OpenedFileStillSafe `
            $Path `
            $Label `
            $opened.identity `
            $initialMetadata `
            -RequireSameIdentity:$RequireSameIdentity
        return [pscustomobject][ordered]@{
            bytes = [byte[]]$memory.ToArray()
            identity = $opened.identity
            metadata = $initialMetadata
        }
    }
    finally {
        $memory.Dispose()
        $opened.value.Dispose()
    }
}

function Get-FileSha256([string]$Path, [switch]$RequireSameIdentity) {
    $cacheKey = [IO.Path]::GetFullPath($Path)
    if ($script:ValidatedFileSha256.ContainsKey($cacheKey)) {
        $cached = $script:ValidatedFileSha256[$cacheKey]
        $current = Open-RegularFileHandle $Path 'File' -RequireExactPath
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $initialMetadata = $current.metadata
            $digest = ([BitConverter]::ToString(
                $sha.ComputeHash($current.value.Stream)
            )).Replace(
                '-',
                ''
            ).ToLowerInvariant()
            $finalMetadata = $current.value.RefreshMetadata()
            if ($finalMetadata -cne $initialMetadata) {
                Fail "File '$Path' changed while it was being hashed."
            }
            Assert-OpenedFileStillSafe `
                $Path `
                'File' `
                $current.identity `
                $initialMetadata `
                -RequireSameIdentity
            if ($current.identity -cne $cached.identity -or
                $initialMetadata -cne $cached.metadata -or
                $digest -cne $cached.sha256) {
                Fail "File '$Path' changed after it was validated."
            }
        }
        finally {
            $sha.Dispose()
            $current.value.Dispose()
        }
        return $digest
    }
    $opened = Open-RegularFileHandle `
        $Path `
        'File' `
        -RequireExactPath:$RequireSameIdentity
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $initialMetadata = $opened.metadata
        $digest = ([BitConverter]::ToString(
            $sha.ComputeHash($opened.value.Stream)
        )).Replace(
            '-',
            ''
        ).ToLowerInvariant()
        $finalMetadata = $opened.value.RefreshMetadata()
        if ($finalMetadata -cne $initialMetadata) {
            Fail "File '$Path' changed while it was being hashed."
        }
        Assert-OpenedFileStillSafe `
            $Path `
            'File' `
            $opened.identity `
            $initialMetadata `
            -RequireSameIdentity
        $script:ValidatedFileSha256[$cacheKey] = [pscustomobject][ordered]@{
            sha256 = $digest
            identity = $opened.identity
            metadata = $initialMetadata
        }
        return $digest
    }
    finally {
        $sha.Dispose()
        $opened.value.Dispose()
    }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace(
            '-',
            ''
        ).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SnapshotEntryKind($Entry, [string]$RelativePath) {
    if (($Entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "Snapshot content may not contain symbolic links or reparse points: '$RelativePath'."
    }
    if ($env:OS -eq 'Windows_NT') {
        return $(if ($Entry.PSIsContainer) { 'directory' } else { 'file' })
    }
    $statCommand = Get-Command stat -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $statCommand) {
        Fail 'Cannot classify snapshot content because the stat utility is unavailable.'
    }
    if ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
            [Runtime.InteropServices.OSPlatform]::Linux
        )) {
        $kind = & $statCommand.Source '--format=%F' '--' $Entry.FullName
        if ($LASTEXITCODE -ne 0) {
            Fail "Cannot inspect snapshot content '$($Entry.FullName)'."
        }
        if ($kind -ceq 'directory') { return 'directory' }
        if ($kind -ceq 'regular file' -or $kind -ceq 'regular empty file') {
            return 'file'
        }
    }
    elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
            [Runtime.InteropServices.OSPlatform]::OSX
        )) {
        $kind = & $statCommand.Source '-f' '%HT' $Entry.FullName
        if ($LASTEXITCODE -ne 0) {
            Fail "Cannot inspect snapshot content '$($Entry.FullName)'."
        }
        if ($kind -ceq 'Directory') { return 'directory' }
        if ($kind -ceq 'Regular File') { return 'file' }
    }
    else {
        Fail 'Snapshot content classification is unavailable on this platform.'
    }
    Fail "Snapshot content entries must be ordinary files or directories: '$RelativePath'."
}

function Get-SnapshotDirectoryState([string]$Path, [string]$RelativePath) {
    $entry = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $entry.PSIsContainer -or
        (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        Fail "Snapshot content may not traverse symbolic links or reparse points: '$RelativePath'."
    }
    if ($env:OS -eq 'Windows_NT') {
        try {
            $parts = [CeDirectoryState]::Inspect($Path)
        }
        catch {
            Fail "Cannot inspect snapshot directory '$Path': $($_.Exception.Message)"
        }
        if (-not (Paths-Equal $parts[2] $Path)) {
            Fail "Snapshot content may not traverse symbolic links or reparse points: '$RelativePath'."
        }
        return [pscustomobject][ordered]@{
            identity = $parts[0]
            metadata = $parts[1]
        }
    }
    try {
        $parts = [CeSafeFile]::InspectPosixPath($Path)
    }
    catch {
        Fail "Cannot inspect snapshot directory '$Path': $($_.Exception.Message)"
    }
    if ((Get-SnapshotEntryKind $entry $RelativePath) -cne 'directory') {
        Fail "Snapshot content changed during hashing: '$RelativePath'."
    }
    return [pscustomobject][ordered]@{
        identity = $parts[1]
        metadata = $parts[2]
    }
}

function Get-SnapshotTreeState(
    [string]$SnapshotRoot,
    [long]$MaxEntries,
    [long]$MaxPathBytes,
    [long]$MaxContentBytes
) {
    if ($MaxEntries -lt 0 -or $MaxPathBytes -lt 0 -or $MaxContentBytes -lt 0) {
        Fail 'Snapshot content limits must be non-negative integers.'
    }
    $files = [Collections.Generic.SortedDictionary[string, object]]::new(
        [StringComparer]::Ordinal
    )
    $entries = [Collections.Generic.SortedDictionary[string, string]]::new(
        [StringComparer]::Ordinal
    )
    $directoryStates = [Collections.Generic.SortedDictionary[string, string]]::new(
        [StringComparer]::Ordinal
    )
    $directories = [Collections.Stack]::new()
    $directories.Push([pscustomobject]@{
        path = $SnapshotRoot
        relative = ''
    })
    [long]$entryCount = 0
    [long]$totalContentBytes = 0
    while ($directories.Count -gt 0) {
        $directory = $directories.Pop()
        $before = Get-SnapshotDirectoryState $directory.path $directory.relative
        $enumerator = [IO.Directory]::EnumerateFileSystemEntries(
            $directory.path
        ).GetEnumerator()
        try {
            while ($enumerator.MoveNext()) {
                $entryPath = [string]$enumerator.Current
                $entryName = [IO.Path]::GetFileName($entryPath)
                $relative = $(if ($directory.relative) {
                    $directory.relative + '/' + $entryName
                } else {
                    $entryName
                })
                try {
                    $pathBytes = $strictUtf8.GetBytes($relative)
                }
                catch {
                    Fail 'Snapshot content path is not valid UTF-8.'
                }
                if ($pathBytes.Length -gt $MaxPathBytes) {
                    Fail "Snapshot content relative path exceeds the $MaxPathBytes-byte UTF-8 limit: '$relative'."
                }
                $entryCount++
                if ($entryCount -gt $MaxEntries) {
                    Fail "Snapshot content exceeds the $MaxEntries-entry limit."
                }
                $entry = Get-Item -LiteralPath $entryPath `
                    -Force -ErrorAction Stop
                $kind = Get-SnapshotEntryKind $entry $relative
                $sortKey = ([BitConverter]::ToString($pathBytes)).Replace('-', '')
                $entries.Add($sortKey, $kind)
                if ($kind -ceq 'directory') {
                    $directories.Push([pscustomobject]@{
                        path = $entry.FullName
                        relative = $relative
                    })
                    continue
                }
                [long]$fileLength = $entry.Length
                if ($fileLength -gt ($MaxContentBytes - $totalContentBytes)) {
                    Fail "Snapshot content exceeds the $MaxContentBytes-byte regular-file limit."
                }
                $totalContentBytes += $fileLength
                if ($directory.relative -or
                    $entry.Name -cne 'snapshot-provenance.json') {
                    $files.Add($sortKey, [pscustomobject][ordered]@{
                        path = $entry.FullName
                        relativeBytes = $pathBytes
                    })
                }
            }
        }
        finally {
            if ($enumerator -is [IDisposable]) {
                $enumerator.Dispose()
            }
        }
        $after = Get-SnapshotDirectoryState $directory.path $directory.relative
        if ($before.identity -cne $after.identity -or
            $before.metadata -cne $after.metadata) {
            Fail "Snapshot content tree changed during hashing: '$($directory.relative)'."
        }
        $directoryKey = if ($directory.relative) {
            ([BitConverter]::ToString(
                $strictUtf8.GetBytes($directory.relative)
            )).Replace('-', '')
        } else {
            ''
        }
        $directoryStates.Add(
            $directoryKey,
            [string]$before.identity + '|' + [string]$before.metadata
        )
    }
    return [pscustomobject][ordered]@{
        files = $files
        entries = $entries
        directories = $directoryStates
        entryCount = $entryCount
        totalContentBytes = $totalContentBytes
    }
}

function Assert-SnapshotTreeStateEqual($Before, $After) {
    if ($Before.entryCount -ne $After.entryCount -or
        $Before.totalContentBytes -ne $After.totalContentBytes -or
        $Before.entries.Count -ne $After.entries.Count -or
        $Before.directories.Count -ne $After.directories.Count) {
        Fail 'Snapshot content tree changed during hashing.'
    }
    foreach ($pair in $Before.entries.GetEnumerator()) {
        if (-not $After.entries.ContainsKey($pair.Key) -or
            $After.entries[$pair.Key] -cne $pair.Value) {
            Fail 'Snapshot content tree changed during hashing.'
        }
    }
    foreach ($pair in $Before.directories.GetEnumerator()) {
        if (-not $After.directories.ContainsKey($pair.Key) -or
            $After.directories[$pair.Key] -cne $pair.Value) {
            Fail 'Snapshot content tree changed during hashing.'
        }
    }
}

function Get-SnapshotContentSha256(
    [string]$SnapshotRoot,
    [long]$MaxEntries = $script:SnapshotMaxEntries,
    [long]$MaxPathBytes = $script:SnapshotMaxPathBytes,
    [long]$MaxContentBytes = $script:SnapshotMaxContentBytes
) {
    $before = Get-SnapshotTreeState `
        $SnapshotRoot `
        $MaxEntries `
        $MaxPathBytes `
        $MaxContentBytes
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $before.files.Values) {
            $entry = Get-Item -LiteralPath $file.path -Force
            if ((Get-SnapshotEntryKind $entry (
                    $strictUtf8.GetString($file.relativeBytes)
                )) -cne 'file') {
                Fail 'Snapshot content changed during hashing.'
            }
            $fileDigest = Get-FileSha256 $file.path -RequireSameIdentity
            $prefix = [byte[]]@(0x46, 0x00)
            $separator = [byte[]]@(0x00)
            $digestBytes = [Text.Encoding]::ASCII.GetBytes($fileDigest)
            $newline = [byte[]]@(0x0A)
            [void]$sha.TransformBlock($prefix, 0, $prefix.Length, $null, 0)
            [void]$sha.TransformBlock(
                $file.relativeBytes,
                0,
                $file.relativeBytes.Length,
                $null,
                0
            )
            [void]$sha.TransformBlock($separator, 0, 1, $null, 0)
            [void]$sha.TransformBlock(
                $digestBytes,
                0,
                $digestBytes.Length,
                $null,
                0
            )
            [void]$sha.TransformBlock($newline, 0, 1, $null, 0)
        }
        $after = Get-SnapshotTreeState `
            $SnapshotRoot `
            $MaxEntries `
            $MaxPathBytes `
            $MaxContentBytes
        Assert-SnapshotTreeStateEqual $before $after
        [void]$sha.TransformFinalBlock([byte[]]@(), 0, 0)
        return ([BitConverter]::ToString($sha.Hash)).Replace(
            '-',
            ''
        ).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Write-AtomicJson([string]$Path, $Value, [switch]$SkipLockCheck) {
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $temporary = Join-Path $directory ('.' + (Split-Path -Leaf $Path) + '.tmp-' + [guid]::NewGuid().ToString('N'))
    try {
        $json = $Value | ConvertTo-Json -Depth 12
        [IO.File]::WriteAllText($temporary, $json + "`n", $utf8NoBom)
        if (-not $SkipLockCheck -and $script:HeldLocks.Count -gt 0) {
            Assert-AllLocksOwned
        }
        [CeAtomicFile]::Replace($temporary, $Path)
        [void]$script:ValidatedFileSha256.Remove(
            [IO.Path]::GetFullPath($Path)
        )
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Write-AtomicText([string]$Path, [string]$Value) {
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $temporary = Join-Path $directory (
        '.' + (Split-Path -Leaf $Path) + '.tmp-' +
        [guid]::NewGuid().ToString('N')
    )
    try {
        [IO.File]::WriteAllText($temporary, $Value + "`n", $utf8NoBom)
        if ($script:HeldLocks.Count -gt 0) {
            Assert-AllLocksOwned
        }
        [CeAtomicFile]::Replace($temporary, $Path)
        [void]$script:ValidatedFileSha256.Remove(
            [IO.Path]::GetFullPath($Path)
        )
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Read-LockOwner([string]$OwnerPath) {
    try {
        $bytes = [IO.File]::ReadAllBytes($OwnerPath)
    }
    catch [System.IO.FileNotFoundException] {
        throw [System.Management.Automation.ItemNotFoundException]::new(
            "Installation lock owner receipt disappeared: $OwnerPath"
        )
    }
    catch [System.IO.DirectoryNotFoundException] {
        throw [System.Management.Automation.ItemNotFoundException]::new(
            "Installation lock owner receipt disappeared: $OwnerPath"
        )
    }
    catch [System.UnauthorizedAccessException] {
        Start-Sleep -Milliseconds 10
        if (-not (Test-Path -LiteralPath $OwnerPath -PathType Leaf)) {
            throw [System.Management.Automation.ItemNotFoundException]::new(
                "Installation lock owner receipt disappeared: $OwnerPath"
            )
        }
        throw [System.Management.Automation.ItemNotFoundException]::new(
            "Installation lock owner receipt changed during read: $OwnerPath"
        )
    }
    catch [System.IO.IOException] {
        Start-Sleep -Milliseconds 10
        if (-not (Test-Path -LiteralPath $OwnerPath -PathType Leaf)) {
            throw [System.Management.Automation.ItemNotFoundException]::new(
                "Installation lock owner receipt disappeared: $OwnerPath"
            )
        }
        throw [System.Management.Automation.ItemNotFoundException]::new(
            "Installation lock owner receipt changed during read: $OwnerPath"
        )
    }
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
        $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Fail "UTF-8 BOM is not allowed in '$OwnerPath'."
    }
    try {
        $text = $strictUtf8.GetString($bytes)
    }
    catch [Text.DecoderFallbackException] {
        Fail "Invalid UTF-8 in installation lock owner receipt '$OwnerPath'."
    }
    catch [ArgumentException] {
        Fail "Invalid UTF-8 in installation lock owner receipt '$OwnerPath'."
    }
    return Read-JsonText $text "installation lock owner receipt '$OwnerPath'"
}

function Assert-LockOwnerShape(
    $Owner,
    [string]$Kind,
    [string]$MarketplaceIdValue,
    [string]$PluginIdValue
) {
    $version = Get-PropertyValue $Owner 'version'
    if (($version -isnot [byte] -and $version -isnot [int16] -and
         $version -isnot [int32] -and $version -isnot [int64]) -or
        (Get-StringProperty $Owner 'schema') -ne 'copilot-extensions.installation-lock' -or
        $version -ne 1 -or
        (Get-StringProperty $Owner 'kind') -ne $Kind -or
        (Get-StringProperty $Owner 'marketplaceId') -cne $MarketplaceIdValue -or
        (Get-StringProperty $Owner 'pluginId') -cne $PluginIdValue -or
        -not (Get-StringProperty $Owner 'token') -or
        -not (Get-StringProperty $Owner 'host')) {
        Fail 'Installation lock owner receipt is invalid.'
    }
    $ownerPid = Get-PropertyValue $Owner 'pid'
    if ($ownerPid -isnot [byte] -and $ownerPid -isnot [int16] -and
        $ownerPid -isnot [int32] -and $ownerPid -isnot [int64]) {
        Fail 'Installation lock pid must be an integer.'
    }
    if ([long]$ownerPid -lt 1) {
        Fail 'Installation lock pid must be at least 1.'
    }
}

function Assert-LockOwned {
    if ($script:HeldLocks.Count -eq 0) {
        Fail 'Installation lock is not held.'
    }
    $held = $script:HeldLocks[$script:HeldLocks.Count - 1]
    if (-not (Test-Path -LiteralPath $held.path -PathType Container)) {
        Fail "Installation lock '$($held.path)' is not held."
    }
    $owner = Read-LockOwner (Join-Path $held.path 'owner.json')
    if ((Get-StringProperty $owner 'token') -ne $held.token) {
        Fail "Installation lock '$($held.path)' ownership changed during mutation."
    }
}

function Assert-AllLocksOwned {
    if ($script:HeldLocks.Count -eq 0) {
        Fail 'Installation lock is not held.'
    }
    foreach ($held in $script:HeldLocks) {
        if (-not (Test-Path -LiteralPath $held.path -PathType Container)) {
            Fail "Installation lock '$($held.path)' is not held."
        }
        $owner = Read-LockOwner (Join-Path $held.path 'owner.json')
        if ((Get-StringProperty $owner 'token') -ne $held.token) {
            Fail "Installation lock '$($held.path)' ownership changed during mutation."
        }
    }
}

function Pop-HeldLock {
    if ($script:HeldLocks.Count -le 1) {
        $script:HeldLocks = @()
        return
    }
    $script:HeldLocks = @($script:HeldLocks[0..($script:HeldLocks.Count - 2)])
}

function Try-CreateLockDirectory([string]$Path) {
    if ($env:OS -eq 'Windows_NT') {
        return [CeAtomicDirectory]::CreateWindows($Path)
    }
    return [CeAtomicDirectory]::CreatePosix($Path)
}

function Acquire-Lock(
    [string]$Path,
    [string]$Kind,
    [string]$MarketplaceIdValue,
    [string]$PluginIdValue = '',
    [int]$TimeoutSeconds = 5
) {
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $token = [guid]::NewGuid().ToString('N')
    $hostName = [Environment]::MachineName.Split('.')[0].ToLowerInvariant()
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $accessDeniedSince = $null
    $lastLockResult = $null
    while ($stopwatch.Elapsed -lt [TimeSpan]::FromSeconds($TimeoutSeconds)) {
        $lockResult = Try-CreateLockDirectory $Path
        $lastLockResult = $lockResult
        if ($lockResult -eq 1) {
            $script:HeldLocks += [pscustomobject]@{
                path = $Path
                token = $token
            }
            $owner = [ordered]@{
                schema = 'copilot-extensions.installation-lock'
                version = 1
                kind = $Kind
                marketplaceId = $MarketplaceIdValue
                pluginId = $PluginIdValue
                token = $token
                host = $hostName
                pid = $PID
                acquiredAt = Get-UtcTimestamp
            }
            try {
                Write-AtomicJson (Join-Path $Path 'owner.json') $owner -SkipLockCheck
            }
            catch {
                Remove-Item -LiteralPath (Join-Path $Path 'owner.json') -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
                Pop-HeldLock
                throw
            }
            return
        }
        if ($lockResult -eq -5) {
            if ($null -eq $accessDeniedSince) {
                $accessDeniedSince = $stopwatch.Elapsed
            }
            elseif (($stopwatch.Elapsed - $accessDeniedSince) -ge
                [TimeSpan]::FromSeconds(1)) {
                throw [ComponentModel.Win32Exception]::new(
                    5,
                    "Access denied while creating installation lock directory '$Path'."
                )
            }
            Start-Sleep -Milliseconds 10
            continue
        }
        $accessDeniedSince = $null
        if ($lockResult -eq -32) {
            Start-Sleep -Milliseconds 10
            continue
        }
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            Start-Sleep -Milliseconds 10
            continue
        }
        $ownerPath = Join-Path $Path 'owner.json'
        if (-not (Test-Path -LiteralPath $ownerPath -PathType Leaf)) {
            Start-Sleep -Milliseconds 10
            continue
        }
        try {
            $owner = Read-LockOwner $ownerPath
        }
        catch [System.Management.Automation.ItemNotFoundException] {
            continue
        }
        Assert-LockOwnerShape $owner $Kind $MarketplaceIdValue $PluginIdValue
        $ownerHost = Get-StringProperty $owner 'host'
        $ownerPid = [long](Get-PropertyValue $owner 'pid')
        if ($ownerHost -eq $hostName) {
            $ownerProcess = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
            if ($null -eq $ownerProcess) {
                Start-Sleep -Milliseconds 10
                if (-not (Test-Path -LiteralPath $Path -PathType Container) -or
                    -not (Test-Path -LiteralPath $ownerPath -PathType Leaf)) {
                    continue
                }
                try {
                    $currentOwner = Read-LockOwner $ownerPath
                }
                catch [System.Management.Automation.ItemNotFoundException] {
                    continue
                }
                Assert-LockOwnerShape $currentOwner $Kind $MarketplaceIdValue $PluginIdValue
                if ((Get-StringProperty $currentOwner 'token') -cne
                    (Get-StringProperty $owner 'token')) {
                    continue
                }
                Fail "Installation lock '$Path' has a stale owner (host=$ownerHost, pid=$ownerPid); explicit repair is required."
            }
            Start-Sleep -Milliseconds 10
            continue
        }
        Fail "Installation lock '$Path' is busy (host=$ownerHost, pid=$ownerPid)."
    }
    if ($lastLockResult -eq -5) {
        throw [ComponentModel.Win32Exception]::new(
            5,
            "Access denied while creating installation lock directory '$Path'."
        )
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Path 'owner.json') -PathType Leaf)) {
        $lockDirectory = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
        if ($null -ne $lockDirectory -and
            ([DateTime]::UtcNow - $lockDirectory.LastWriteTimeUtc).TotalSeconds -ge 5) {
            Fail "Installation lock '$Path' has no owner receipt; explicit repair is required."
        }
        Fail "Installation lock '$Path' remained busy."
    }
    Fail "Installation lock '$Path' remained busy."
}

function Remove-LockArtifact([string]$Path, [string]$Label) {
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        try {
            Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
            return
        }
        catch [System.UnauthorizedAccessException] {
            if ($stopwatch.Elapsed -ge [TimeSpan]::FromSeconds(1)) {
                Fail "Cannot release installation lock ${Label} '$Path': $($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds 10
        }
        catch [System.IO.IOException] {
            if ($stopwatch.Elapsed -ge [TimeSpan]::FromSeconds(1)) {
                Fail "Cannot release installation lock ${Label} '$Path': $($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds 10
        }
    }
}

function Release-Lock {
    Assert-LockOwned
    $held = $script:HeldLocks[$script:HeldLocks.Count - 1]
    Remove-LockArtifact (Join-Path $held.path 'owner.json') 'owner receipt'
    Remove-LockArtifact $held.path 'directory'
    Pop-HeldLock
}

function Get-PropertyValue($Object, [string]$Name, $Default = $null) {
    if ($null -eq $Object) { return $Default }
    $exact = $null
    foreach ($property in $Object.PSObject.Properties) {
        if ([string]::Equals($property.Name, $Name, [StringComparison]::Ordinal)) {
            $exact = $property
            continue
        }
        if ([string]::Equals($property.Name, $Name, [StringComparison]::OrdinalIgnoreCase)) {
            Fail "JSON property '$($property.Name)' conflicts with exact case '$Name'."
        }
    }
    if ($null -ne $exact) { return $exact.Value }
    return $Default
}

function Get-StringProperty($Object, [string]$Name, [string]$Default = '') {
    $value = Get-PropertyValue $Object $Name $Default
    if ($null -eq $value) { return $Default }
    if ($value -isnot [string]) {
        Fail "JSON field '$Name' must be a string."
    }
    if ($value.Contains([char]0)) {
        Fail "JSON field '$Name' may not contain NUL."
    }
    return $value
}

function Get-ReceiptTimestamp($Object, [string]$Name, [string]$Default) {
    $value = Get-PropertyValue $Object $Name $Default
    if ($value -is [DateTime]) {
        return $value.ToUniversalTime().ToString(
            'yyyy-MM-ddTHH:mm:ssZ',
            [Globalization.CultureInfo]::InvariantCulture
        )
    }
    if ($value -isnot [string]) {
        Fail "JSON field '$Name' must be a string."
    }
    return $value
}

function Canonical-Path([string]$Path, [switch]$MustExist) {
    if ([string]::IsNullOrWhiteSpace($Path)) { Fail 'A required path is empty.' }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    $fullPath = [IO.Path]::GetFullPath($expanded)
    if ($MustExist -and -not (Test-Path -LiteralPath $fullPath)) {
        Fail "Path does not exist: $Path"
    }
    $existing = $fullPath
    $tail = New-Object Collections.Generic.List[string]
    while (-not (Test-Path -LiteralPath $existing)) {
        $leaf = Split-Path -Leaf $existing
        if (-not $leaf) { break }
        $tail.Insert(0, $leaf)
        $parent = Split-Path -Parent $existing
        if (-not $parent -or $parent -eq $existing) { break }
        $existing = $parent
    }
    if (Test-Path -LiteralPath $existing) {
        if ($env:OS -eq 'Windows_NT') {
            try { $fullPath = [CeFinalPath]::Resolve($existing) }
            catch { Fail "Cannot resolve final Windows path '$existing': $($_.Exception.Message)" }
        }
        else {
            try { $fullPath = [CePosixPath]::Resolve($existing) }
            catch { Fail "Cannot resolve final POSIX path '$existing': $($_.Exception.Message)" }
        }
        foreach ($segment in $tail) { $fullPath = Join-Path $fullPath $segment }
        $fullPath = [IO.Path]::GetFullPath($fullPath)
    }
    $pathRoot = [IO.Path]::GetPathRoot($fullPath)
    if ($fullPath -eq $pathRoot) { return $fullPath }
    return $fullPath.TrimEnd('\', '/')
}

function Test-FullyQualifiedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    if ($env:OS -eq 'Windows_NT') {
        return (
            $Path -cmatch '^[A-Za-z]:[\\/]' -or
            $Path -cmatch '^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)'
        )
    }
    return $Path.StartsWith('/', [StringComparison]::Ordinal)
}

function Path-IsWithin([string]$Child, [string]$Parent) {
    $childPath = Canonical-Path $Child
    $parentPath = Canonical-Path $Parent
    if (Paths-Equal $childPath $parentPath) { return $true }
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    $prefix = $parentPath.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    return $childPath.StartsWith($prefix, $comparison)
}

function Paths-Equal([string]$Left, [string]$Right) {
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    return [string]::Equals(
        (Canonical-Path $Left),
        (Canonical-Path $Right),
        $comparison
    )
}

function Restore-ProtectedJsonValue($Value, [string]$Marker) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [string]) {
        if (-not $Value.StartsWith($Marker, [StringComparison]::Ordinal)) {
            Fail 'JSON string protection marker is missing.'
        }
        return $Value.Substring($Marker.Length)
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $result = [ordered]@{}
        foreach ($property in $Value.PSObject.Properties) {
            if (-not $property.Name.StartsWith($Marker, [StringComparison]::Ordinal)) {
                Fail 'JSON property protection marker is missing.'
            }
            $name = $property.Name.Substring($Marker.Length)
            $result[$name] = Restore-ProtectedJsonValue $property.Value $Marker
        }
        return [pscustomobject]$result
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        $items = @()
        foreach ($item in $Value) {
            $items += ,(Restore-ProtectedJsonValue $item $Marker)
        }
        return ,$items
    }
    return $Value
}

function ConvertFrom-StrictJson([string]$Value) {
    do {
        $marker = "__CE_JSON_$([Guid]::NewGuid().ToString('N'))__"
    } while ($Value.Contains($marker))
    $protected = [CeStrictJson]::ProtectStrings($Value, $marker)
    $parsed = $protected | ConvertFrom-Json
    return Restore-ProtectedJsonValue $parsed $marker
}

function Read-Json([string]$Path) {
    $canonical = Canonical-Path $Path -MustExist
    try {
        $validated = Read-RegularFileBytes `
            $canonical `
            'JSON document' `
            -RequireSameIdentity
        $bytes = [byte[]]$validated.bytes
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
            $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            Fail "UTF-8 BOM is not allowed in '$canonical'."
        }
        $text = $strictUtf8.GetString($bytes)
        [CeStrictJson]::Validate($text)
        $value = ConvertFrom-StrictJson $text
        $cacheKey = [IO.Path]::GetFullPath($canonical)
        $cacheEntry = [pscustomobject][ordered]@{
            sha256 = Get-BytesSha256 $bytes
            identity = $validated.identity
            metadata = $validated.metadata
        }
        if ($script:ValidatedFileSha256.ContainsKey($cacheKey)) {
            $previous = $script:ValidatedFileSha256[$cacheKey]
            if ($previous.sha256 -cne $cacheEntry.sha256 -or
                $previous.identity -cne $cacheEntry.identity -or
                $previous.metadata -cne $cacheEntry.metadata) {
                Fail "File '$canonical' changed after it was validated."
            }
        }
        $script:ValidatedFileSha256[$cacheKey] = $cacheEntry
        return $value
    }
    catch {
        Fail "Invalid JSON in '$canonical': $($_.Exception.Message)"
    }
}

function Read-JsonText([string]$Value, [string]$Label) {
    if ($Value.Length -gt 0 -and $Value[0] -eq [char]0xFEFF) {
        Fail "UTF-8 BOM is not allowed in $Label."
    }
    try {
        [CeStrictJson]::Validate($Value)
        return ConvertFrom-StrictJson $Value
    }
    catch {
        Fail "Invalid ${Label}: $($_.Exception.Message)"
    }
}

function Read-SourceDescriptor {
    if ($SourceJson -and $SourceFile) {
        Fail 'Specify only one of -SourceJson and -SourceFile.'
    }
    if ($SourceFile) { return Read-Json $SourceFile }
    if ($SourceJson) {
        return Read-JsonText $SourceJson '-SourceJson'
    }
    return $null
}

function Normalize-GitPath([string]$Path) {
    $candidate = $Path.Replace('\', '/')
    if (-not $candidate.StartsWith('/')) { $candidate = "/$candidate" }
    $segments = New-Object Collections.Generic.List[string]
    foreach ($segment in $candidate.Split([char]'/', [StringSplitOptions]::None)) {
        if (-not $segment) {
            if ($segments.Count -gt 0) { $segments.Add('') }
            continue
        }
        if ($segment -eq '.') { continue }
        if ($segment -eq '..') {
            if ($segments.Count -gt 0) { $segments.RemoveAt($segments.Count - 1) }
            continue
        }
        $segments.Add($segment)
    }
    while ($segments.Count -gt 0 -and -not $segments[$segments.Count - 1]) {
        $segments.RemoveAt($segments.Count - 1)
    }
    return '/' + ($segments -join '/')
}

function ConvertTo-PortableGitPath([string]$Path) {
    $safe = '/-._~!$&''()*+,;=:@%[]'
    $encoded = New-Object Text.StringBuilder
    foreach ($value in [Text.Encoding]::UTF8.GetBytes($Path.Replace('\', '/'))) {
        $character = [char]$value
        if (($value -ge 0x30 -and $value -le 0x39) -or
            ($value -ge 0x41 -and $value -le 0x5A) -or
            ($value -ge 0x61 -and $value -le 0x7A) -or
            $safe.IndexOf($character) -ge 0) {
            [void]$encoded.Append($character)
        }
        else {
            [void]$encoded.AppendFormat(
                [Globalization.CultureInfo]::InvariantCulture,
                '%{0:X2}',
                $value
            )
        }
    }
    return $encoded.ToString()
}

function Normalize-GitUrl([string]$Url) {
    foreach ($character in $Url.ToCharArray()) {
        $code = [int]$character
        if ($code -lt 32 -or $code -eq 127) {
            Fail 'A git source URL may not contain control characters.'
        }
    }
    if ([string]::IsNullOrWhiteSpace($Url)) { Fail 'A git source requires url.' }
    $candidate = $Url.Trim()
    if ([regex]::IsMatch($candidate, '%(?![0-9A-Fa-f]{2})')) {
        Fail 'Git URL has a malformed percent-escape.'
    }
    if ($candidate -match '^[^/@:]+@([^/:]+):(.+)$') {
        $candidate = "ssh://$($Matches[1])/$($Matches[2])"
    }
    $authorityMatch = [regex]::Match(
        $candidate,
        '^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]+)'
    )
    if (-not $authorityMatch.Success) {
        Fail "Git URL must be absolute and include a host: $Url"
    }
    $scheme = $authorityMatch.Groups[1].Value.ToLowerInvariant()
    $authority = $authorityMatch.Groups[2].Value
    $at = $authority.LastIndexOf('@')
    if ($at -ge 0) { $authority = $authority.Substring($at + 1) }
    $hostCore = ''
    $rawPort = ''
    $portPresent = $false
    $bracketMatch = [regex]::Match($authority, '^(\[[^]]+\])(?::([0-9]*))?$')
    $hostMatch = [regex]::Match($authority, '^([^:]+)(?::([0-9]*))?$')
    if ($bracketMatch.Success) {
        $hostCore = $bracketMatch.Groups[1].Value.Trim('[', ']').ToLowerInvariant()
        $portPresent = $bracketMatch.Groups[2].Success
        $rawPort = $bracketMatch.Groups[2].Value
    }
    elseif ($hostMatch.Success) {
        $hostCore = $hostMatch.Groups[1].Value.ToLowerInvariant()
        $portPresent = $hostMatch.Groups[2].Success
        $rawPort = $hostMatch.Groups[2].Value
    }
    else {
        Fail "Invalid git URL '$Url'."
    }
    if ($hostCore.Contains(':')) {
        $parsedAddress = $null
        if ($hostCore -notmatch '^[0-9a-f:]+$' -or
            -not [Net.IPAddress]::TryParse($hostCore, [ref]$parsedAddress) -or
            $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetworkV6) {
            Fail "Git URL has an invalid host: $Url"
        }
    }
    elseif ($hostCore -notmatch '^[a-z0-9._-]+$') {
        Fail "Git URL has an invalid host: $Url"
    }
    $hostName = $hostCore
    if ($hostCore.Contains(':')) { $hostName = "[$hostCore]" }
    $port = ''
    if ($portPresent -and $rawPort) {
        [int]$portNumber = 0
        if (-not [int]::TryParse($rawPort, [ref]$portNumber) -or
            $portNumber -gt 65535) {
            Fail "Invalid git URL '$Url'."
        }
        if (-not (($scheme -eq 'http' -and $portNumber -eq 80) -or
                  ($scheme -eq 'https' -and $portNumber -eq 443))) {
            $port = ":$portNumber"
        }
    }
    try { $uri = [Uri]$candidate }
    catch { Fail "Invalid git URL '$Url'." }
    if (-not $uri.IsAbsoluteUri -or [string]::IsNullOrWhiteSpace($uri.Host)) {
        Fail "Git URL must be absolute and include a host: $Url"
    }
    $pathEnd = $candidate.Length
    foreach ($separator in @('?', '#')) {
        $index = $candidate.IndexOf(
            $separator,
            $authorityMatch.Length,
            [StringComparison]::Ordinal
        )
        if ($index -ge 0 -and $index -lt $pathEnd) {
            $pathEnd = $index
        }
    }
    $rawPath = $candidate.Substring(
        $authorityMatch.Length,
        $pathEnd - $authorityMatch.Length
    )
    $path = ConvertTo-PortableGitPath $rawPath
    $path = [regex]::Replace($path, '%([0-9A-Fa-f]{2})', {
        param($match)
        $value = [Convert]::ToInt32($match.Groups[1].Value, 16)
        $character = [char]$value
        if (($character -ge 'A' -and $character -le 'Z') -or
            ($character -ge 'a' -and $character -le 'z') -or
            ($character -ge '0' -and $character -le '9') -or
            $character -in @('-', '.', '_', '~')) {
            return [string]$character
        }
        return '%' + $match.Groups[1].Value.ToUpperInvariant()
    })
    $path = Normalize-GitPath $path
    if ($path.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
        $path = $path.Substring(0, $path.Length - 4)
    }
    return "$scheme`://$hostName$port$path"
}

function Normalize-Source(
    $Descriptor,
    [string]$BaseDirectory,
    [switch]$FromReceipt
) {
    if ($null -eq $Descriptor) { Fail 'A source descriptor is required.' }
    $kind = Get-StringProperty $Descriptor 'kind'
    if (-not $kind) { $kind = Get-StringProperty $Descriptor 'source' }
    $kind = $kind.Trim().ToLowerInvariant()
    if ($kind -eq 'local') { $kind = 'directory' }
    if ($kind -eq 'url') { $kind = 'git' }
    $ref = Get-StringProperty $Descriptor 'ref'
    $canonicalInput = Get-StringProperty $Descriptor 'canonical'
    if ($FromReceipt -and -not $canonicalInput) {
        Fail 'A receipt source requires canonical identity.'
    }
    $canonical = ''

    switch ($kind) {
        'github' {
            if ($canonicalInput) {
                if (-not $canonicalInput.StartsWith('github:', [StringComparison]::Ordinal)) {
                    Fail "Invalid canonical GitHub source '$canonicalInput'."
                }
                $repo = $canonicalInput.Substring(7)
            }
            else {
                $repo = Get-StringProperty $Descriptor 'repo'
                if (-not $repo) { $repo = Get-StringProperty $Descriptor 'url' }
            }
            $repo = $repo.Trim()
            $repo = $repo -replace '^(?i:https?://github\.com/)', ''
            $repo = $repo -replace '^(?i:ssh://git@github\.com/)', ''
            $repo = $repo -replace '^(?i:git@github\.com:)', ''
            $repo = $repo.Trim('/')
            if ($repo.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
                $repo = $repo.Substring(0, $repo.Length - 4)
            }
            if ($repo -notmatch '^[^/]+/[^/]+$') {
                Fail "GitHub source requires owner/repository, got '$repo'."
            }
            # GitHub owner/repository identity is case-insensitive.
            $repo = $repo.ToLowerInvariant()
            $canonical = "github:$repo"
        }
        'git' {
            if ($canonicalInput) {
                if (-not $canonicalInput.StartsWith('git:', [StringComparison]::Ordinal)) {
                    Fail "Invalid canonical git source '$canonicalInput'."
                }
                $gitUrl = $canonicalInput.Substring(4)
            }
            else {
                $gitUrl = Get-StringProperty $Descriptor 'url'
            }
            $canonical = "git:$(Normalize-GitUrl $gitUrl)"
        }
        'opaque' {
            if ($canonicalInput) {
                $canonical = $canonicalInput
            }
            else {
                $opaqueId = Get-StringProperty $Descriptor 'id'
                if (-not $opaqueId) { $opaqueId = Get-StringProperty $Descriptor 'value' }
                if ([string]::IsNullOrWhiteSpace($opaqueId)) {
                    Fail 'An opaque source requires a non-empty id.'
                }
                $canonical = "opaque:$opaqueId"
            }
            if (-not $canonical.StartsWith('opaque:', [StringComparison]::Ordinal)) {
                Fail "Invalid canonical opaque source '$canonical'."
            }
        }
        'directory' {
            $stableId = (Get-StringProperty $Descriptor 'stableId').Trim()
            if ($canonicalInput) {
                if ($canonicalInput.StartsWith('directory-id:', [StringComparison]::Ordinal)) {
                    $receiptStableId = $canonicalInput.Substring(13).Trim()
                    if (-not $receiptStableId) {
                        Fail 'A canonical directory-id source requires a non-empty id.'
                    }
                    $canonical = "directory-id:$receiptStableId"
                }
                elseif ($canonicalInput.StartsWith('directory:', [StringComparison]::Ordinal)) {
                    $receiptDirectory = $canonicalInput.Substring(10)
                    if ($FromReceipt) {
                        $canonical = "directory:$(Canonical-Path $receiptDirectory)"
                    }
                    else {
                        $canonical = "directory:$(Canonical-Path $receiptDirectory -MustExist)"
                    }
                }
                else {
                    Fail "Invalid canonical directory source '$canonicalInput'."
                }
            }
            elseif ($stableId) {
                $canonical = "directory-id:$stableId"
            }
            else {
                $directoryPath = Get-StringProperty $Descriptor 'path'
                if ([string]::IsNullOrWhiteSpace($directoryPath)) {
                    Fail 'A directory source requires a non-empty path or stableId.'
                }
                if (-not [IO.Path]::IsPathRooted($directoryPath)) {
                    if (-not $BaseDirectory) {
                        Fail 'A relative directory source requires a declaration base directory.'
                    }
                    $directoryPath = Join-Path $BaseDirectory $directoryPath
                }
                $canonical = "directory:$(Canonical-Path $directoryPath -MustExist)"
            }
            if (-not ($canonical.StartsWith('directory:', [StringComparison]::Ordinal) -or
                      $canonical.StartsWith('directory-id:', [StringComparison]::Ordinal))) {
                Fail "Invalid canonical directory source '$canonical'."
            }
        }
        default { Fail "Unsupported source kind '$kind'." }
    }

    return [pscustomobject][ordered]@{
        kind = $kind
        canonical = $canonical
        ref = $ref
    }
}

function Source-Record($Source) {
    $builder = New-Object Text.StringBuilder
    foreach ($field in @(
        @('version', '1'),
        @('kind', [string]$Source.kind),
        @('source', [string]$Source.canonical),
        @('ref', [string]$Source.ref)
    )) {
        $length = [Text.Encoding]::UTF8.GetByteCount([string]$field[1])
        [void]$builder.Append("$($field[0]):$length`:$($field[1])`n")
    }
    return $builder.ToString()
}

function Readable-Slug([string]$Value) {
    $builder = New-Object Text.StringBuilder
    $previousDash = $false
    foreach ($character in $Value.ToCharArray()) {
        $code = [int]$character
        if ($code -ge 65 -and $code -le 90) {
            $character = [char]($code + 32)
            $code += 32
        }
        if (($code -ge 97 -and $code -le 122) -or
            ($code -ge 48 -and $code -le 57)) {
            [void]$builder.Append($character)
            $previousDash = $false
        }
        elseif ($builder.Length -gt 0 -and -not $previousDash) {
            [void]$builder.Append('-')
            $previousDash = $true
        }
    }
    $slug = $builder.ToString().Trim('-')
    if (-not $slug) { return 'marketplace' }
    return $slug
}

function Source-Identity($Source, [string]$ReadableName) {
    $record = Source-Record $Source
    $bytes = [Text.Encoding]::UTF8.GetBytes($record)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
    $slug = Readable-Slug $ReadableName
    return [pscustomobject][ordered]@{
        kind = $Source.kind
        canonical = $Source.canonical
        ref = $Source.ref
        record = $record
        sha256 = $digest
        fingerprint = "sha256:$digest"
        marketplaceId = "$slug--$($digest.Substring(0, 16))"
    }
}

function Get-DefaultHome([string]$Child) {
    $userHomePath = $env:USERPROFILE
    if (-not $userHomePath) { $userHomePath = $HOME }
    if (-not $userHomePath) { Fail 'Cannot determine the user home.' }
    return Join-Path $userHomePath $Child
}

function Get-Declarations([string]$Key, [string]$ResolvedCopilotHome, [string]$ResolvedProjectRoot) {
    $declarations = @()
    $settingsPaths = @(
        [pscustomobject]@{
            path = (Join-Path $ResolvedCopilotHome 'settings.json')
            label = 'user'
            baseDirectory = $ResolvedCopilotHome
        },
        [pscustomobject]@{
            path = (Join-Path $ResolvedCopilotHome 'settings.local.json')
            label = 'user-local'
            baseDirectory = $ResolvedCopilotHome
        }
    )
    if ($ResolvedProjectRoot) {
        foreach ($relativeSettings in @(
            '.claude/settings.json',
            '.claude/settings.local.json',
            '.github/copilot/settings.json',
            '.github/copilot/settings.local.json'
        )) {
            $settingsPaths += [pscustomobject]@{
                path = (Join-Path $ResolvedProjectRoot $relativeSettings)
                label = "project:$ResolvedProjectRoot"
                baseDirectory = $ResolvedProjectRoot
            }
        }
    }
    foreach ($entry in $settingsPaths) {
        if (-not (Test-Path -LiteralPath $entry.path -PathType Leaf)) { continue }
        $settings = Read-Json $entry.path
        $marketplaces = Get-PropertyValue $settings 'extraKnownMarketplaces'
        if ($null -eq $marketplaces) { continue }
        $declaration = Get-PropertyValue $marketplaces $Key
        if ($null -eq $declaration) { continue }
        $descriptor = Get-PropertyValue $declaration 'source'
        if ($null -eq $descriptor) { Fail "Marketplace '$Key' has no source in '$($entry.path)'." }
        $source = Normalize-Source $descriptor $entry.baseDirectory
        $declarations += [pscustomobject]@{
            source = $source
            declaredIn = $entry.label
            settingsPath = Canonical-Path $entry.path
        }
    }
    if ($declarations.Count -eq 0) {
        Fail "No user or explicit project extraKnownMarketplaces declaration found for installed key '$Key'."
    }
    $distinct = @($declarations | Group-Object { "$($_.source.kind)`n$($_.source.canonical)`n$($_.source.ref)" })
    if ($distinct.Count -ne 1) {
        $locations = ($declarations | ForEach-Object { $_.settingsPath }) -join ', '
        Fail "Conflicting declarations for marketplace key '$Key' in: $locations. Supply explicit management provenance."
    }
    return $declarations
}

function Resolve-InstalledEvidence(
    [string]$ResolvedPayload,
    [string]$ResolvedCopilotHome,
    [string]$ResolvedProjectRoot
) {
    $installed = Canonical-Path (Join-Path $ResolvedCopilotHome 'installed-plugins')
    $prefix = $installed + [IO.Path]::DirectorySeparatorChar
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    if (-not $ResolvedPayload.StartsWith($prefix, $comparison)) { return $null }
    $relative = $ResolvedPayload.Substring($prefix.Length)
    $parts = @($relative -split '[\\/]')
    if ($parts.Count -ne 2 -or -not $parts[0] -or -not $parts[1]) {
        Fail "Installed payload must be exactly <copilot-home>/installed-plugins/<key>/<plugin>: $ResolvedPayload"
    }
    $key = $parts[0]
    $plugin = $parts[1]
    $declarations = @(Get-Declarations $key $ResolvedCopilotHome $ResolvedProjectRoot)
    return [pscustomobject]@{
        source = $declarations[0].source
        pluginId = $plugin
        readableName = $key
        locator = [pscustomobject][ordered]@{
            kind = 'installed'
            copilotHome = $ResolvedCopilotHome
            marketplaceKey = $key
            declaredIn = @($declarations | ForEach-Object { $_.declaredIn })
        }
    }
}

function Resolve-DirectoryEvidence([string]$ResolvedPayload, [string]$RequestedPluginId) {
    $cursor = $ResolvedPayload
    while ($cursor) {
        foreach ($relativeManifest in @(
            '.github/plugin/marketplace.json',
            'marketplace.json',
            '.plugin/marketplace.json',
            '.claude-plugin/marketplace.json'
        )) {
            $manifestPath = Join-Path $cursor $relativeManifest
            if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { continue }
            $manifest = Read-Json $manifestPath
            $metadata = Get-PropertyValue $manifest 'metadata'
            $pluginRoot = Get-StringProperty $metadata 'pluginRoot'
            $sourceBase = $cursor
            if ($pluginRoot) {
                if ([IO.Path]::IsPathRooted($pluginRoot)) {
                    Fail "Marketplace metadata.pluginRoot must be relative in '$manifestPath'."
                }
                if (@($pluginRoot -split '[\\/]') -contains '..') {
                    Fail "Marketplace metadata.pluginRoot may not escape '$cursor'."
                }
                $sourceBase = Join-Path $cursor $pluginRoot
                if (-not (Path-IsWithin $sourceBase $cursor)) {
                    Fail "Marketplace metadata.pluginRoot escapes '$cursor'."
                }
            }
            $plugins = @(Get-PropertyValue $manifest 'plugins' @())
            $matches = @()
            foreach ($plugin in $plugins) {
                $name = Get-StringProperty $plugin 'name'
                if ($RequestedPluginId -and $name -cne $RequestedPluginId) { continue }
                $sourcePath = Get-StringProperty $plugin 'source'
                if (-not $sourcePath) { continue }
                if ([IO.Path]::IsPathRooted($sourcePath) -or
                    @($sourcePath -split '[\\/]') -contains '..') {
                    Fail "Marketplace plugin source must be relative and remain beneath '$cursor'."
                }
                $candidate = $sourcePath
                if (-not [IO.Path]::IsPathRooted($candidate)) {
                    $candidate = Join-Path $sourceBase $candidate
                }
                if (-not (Path-IsWithin $candidate $cursor)) {
                    Fail "Marketplace plugin source escapes '$cursor'."
                }
                if ((Test-Path -LiteralPath $candidate) -and (Paths-Equal $candidate $ResolvedPayload)) {
                    $matches += $plugin
                }
            }
            if ($matches.Count -ne 1) {
                Fail "Marketplace manifest '$manifestPath' does not contain exactly one plugin entry resolving to '$ResolvedPayload'."
            }
            $pluginIdFromManifest = Get-StringProperty $matches[0] 'name'
            $source = Normalize-Source ([pscustomobject]@{ source = 'directory'; path = $cursor }) $cursor
            return [pscustomobject]@{
                source = $source
                pluginId = $pluginIdFromManifest
                readableName = Get-StringProperty $manifest 'name' 'marketplace'
                locator = [pscustomobject][ordered]@{
                    kind = 'directory'
                    marketplaceRoot = Canonical-Path $cursor -MustExist
                }
            }
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or (Paths-Equal $parent $cursor)) { break }
        $cursor = Canonical-Path $parent
    }
    return $null
}

function Locator-Matches($Locator, $ReceiptLocator) {
    if ((Get-StringProperty $Locator 'kind') -ne
        (Get-StringProperty $ReceiptLocator 'kind')) { return $false }
    if ($Locator.kind -eq 'installed') {
        return ((Get-StringProperty $ReceiptLocator 'marketplaceKey') -eq $Locator.marketplaceKey -and
                (Paths-Equal (Get-StringProperty $ReceiptLocator 'copilotHome') $Locator.copilotHome))
    }
    if ($Locator.kind -eq 'directory') {
        return (Paths-Equal (Get-StringProperty $ReceiptLocator 'marketplaceRoot') $Locator.marketplaceRoot)
    }
    return $false
}

function Find-ExistingSource(
    [string]$ResolvedDurableHome,
    [string]$Fingerprint,
    [string]$DesiredId,
    $Locator
) {
    $marketplaces = Join-Path $ResolvedDurableHome 'marketplaces'
    if (-not (Test-Path -LiteralPath $marketplaces -PathType Container)) { return @() }
    $results = @()
    foreach ($cellDirectory in @(Get-ChildItem -LiteralPath $marketplaces -Directory -ErrorAction SilentlyContinue)) {
        $file = Get-Item -LiteralPath (Join-Path $cellDirectory.FullName 'namespace.json') -ErrorAction SilentlyContinue
        if ($null -eq $file) { continue }
        $validated = Validate-NamespaceReceipt $file.FullName $ResolvedDurableHome
        $receipt = $validated.receipt
        $receiptFingerprint = $validated.identity.fingerprint
        $receiptId = $validated.marketplaceId
        if ($cellDirectory.Name -ceq $DesiredId -and $receiptFingerprint -cne $Fingerprint) {
            Fail "Marketplace id '$DesiredId' is already occupied by a different full source fingerprint."
        }
        if ($receiptFingerprint -cne $Fingerprint) { continue }
        $locatorMatch = $null -eq $Locator
        if ($null -ne $Locator) {
            foreach ($known in @(Get-PropertyValue $receipt 'locators' @())) {
                if (Locator-Matches $Locator $known) { $locatorMatch = $true; break }
            }
        }
        $results += [pscustomobject][ordered]@{
            marketplaceId = $receiptId
            namespaceReceipt = Canonical-Path $file.FullName
            sameId = ($receiptId -ceq $DesiredId)
            locatorMatch = $locatorMatch
        }
    }
    return $results
}

function Assert-PositiveInteger($Value, [string]$Name) {
    if ($Value -isnot [byte] -and $Value -isnot [int16] -and
        $Value -isnot [int32] -and $Value -isnot [int64]) {
        Fail "$Name must be an integer."
    }
    if ([int64]$Value -lt 1) { Fail "$Name must be at least 1." }
}

function Assert-ReceiptState($Value, [string]$Name) {
    if ([string]$Value -cnotin @('active', 'inactive', 'orphaned', 'removing')) {
        Fail "$Name must be active, inactive, orphaned, or removing."
    }
}

function Assert-ReceiptGeneration($Value, [string]$Name) {
    $maximumText = [int64]::MaxValue.ToString([Globalization.CultureInfo]::InvariantCulture)
    if ($Value -is [Numerics.BigInteger]) {
        if ($Value -gt [Numerics.BigInteger]::Parse($maximumText)) {
            Fail "$Name exceeds the portable signed 64-bit maximum."
        }
    }
    elseif ($Value -is [decimal] -or $Value -is [uint64]) {
        if ([decimal]$Value -gt [decimal]::Parse(
            $maximumText,
            [Globalization.CultureInfo]::InvariantCulture
        )) {
            Fail "$Name exceeds the portable signed 64-bit maximum."
        }
    }
    elseif ($Value -is [double] -or $Value -is [single]) {
        if ([double]$Value -gt [double]::Parse(
            $maximumText,
            [Globalization.CultureInfo]::InvariantCulture
        )) {
            Fail "$Name exceeds the portable signed 64-bit maximum."
        }
    }
    Assert-PositiveInteger $Value $Name
}

function Validate-NamespaceReceipt(
    [string]$ReceiptPath,
    [string]$ResolvedDurableHome
) {
    $hostPlatform = $(if ($env:OS -eq 'Windows_NT') { 'windows' } else { 'posix' })
    if (-not (Test-EnvironmentPathRooted $ReceiptPath $hostPlatform)) {
        Fail 'The namespace receipt pointer must be absolute.'
    }
    $lexicalMarketplacesRoot = Join-Path $ResolvedDurableHome 'marketplaces'
    Assert-NotReparsePoint $lexicalMarketplacesRoot 'The marketplaces root'
    $marketplacesRoot = Canonical-Path $lexicalMarketplacesRoot
    if (-not (Paths-Equal (Split-Path -Parent $marketplacesRoot) $ResolvedDurableHome)) {
        Fail 'The marketplaces root escapes the durable installation home.'
    }
    $lexicalCellRoot = Split-Path -Parent $ReceiptPath
    Assert-NotReparsePoint $lexicalCellRoot 'The marketplace cell root'
    $cellRoot = Canonical-Path $lexicalCellRoot
    if (-not (Paths-Equal (Split-Path -Parent $cellRoot) $marketplacesRoot)) {
        Fail "Namespace receipt '$ReceiptPath' is outside the durable marketplaces root."
    }
    Assert-NotReparsePoint $ReceiptPath 'namespace.json'
    $actualReceipt = Canonical-Path $ReceiptPath -MustExist
    if (-not (Paths-Equal (Split-Path -Parent $actualReceipt) $cellRoot)) {
        Fail 'namespace.json escapes its canonical marketplace cell.'
    }
    $marketplaceId = Split-Path -Leaf $cellRoot
    $lexicalCanonicalReceipt = Join-Path $cellRoot 'namespace.json'
    Assert-NotReparsePoint $lexicalCanonicalReceipt 'namespace.json'
    $canonicalReceipt = Canonical-Path $lexicalCanonicalReceipt
    if (-not (Paths-Equal $actualReceipt $canonicalReceipt)) {
        Fail "namespace.json is not at its exact canonical receipt location '$canonicalReceipt'."
    }
    $namespace = Read-Json $actualReceipt
    $namespaceVersion = Get-PropertyValue $namespace 'version'
    Assert-PositiveInteger $namespaceVersion 'namespace.json version'
    if ((Get-StringProperty $namespace 'schema') -cne 'copilot-extensions.marketplace-namespace' -or
        $namespaceVersion -ne 1) {
        Fail "Namespace receipt '$actualReceipt' has an unsupported schema or version."
    }
    if ((Get-StringProperty $namespace 'marketplaceId') -cne $marketplaceId) {
        Fail "Namespace receipt '$actualReceipt' does not match its cell directory."
    }
    $idMatch = [regex]::Match($marketplaceId, '^(.+)--([0-9a-f]{16})$')
    if (-not $idMatch.Success) {
        Fail "Invalid source-derived marketplace id '$marketplaceId'."
    }
    Assert-ReceiptGeneration (Get-PropertyValue $namespace 'generation') 'namespace.json generation'
    Assert-ReceiptState (Get-StringProperty $namespace 'state') 'namespace.json state'
    $sourceReceipt = Get-PropertyValue $namespace 'source'
    $normalized = Normalize-Source ([pscustomobject]@{
        kind = Get-StringProperty $sourceReceipt 'kind'
        canonical = Get-StringProperty $sourceReceipt 'canonical'
        ref = Get-StringProperty $sourceReceipt 'ref'
    }) '' -FromReceipt
    $identity = Source-Identity $normalized $idMatch.Groups[1].Value
    if ($identity.marketplaceId -cne $marketplaceId) {
        Fail "Namespace receipt '$actualReceipt' id does not match its normalized source."
    }
    if ((Get-StringProperty $sourceReceipt 'fingerprint') -cne $identity.fingerprint) {
        Fail "Namespace receipt '$actualReceipt' fingerprint does not match its normalized source."
    }
    return [pscustomobject]@{
        receipt = $namespace
        receiptPath = $actualReceipt
        cellRoot = $cellRoot
        marketplaceId = $marketplaceId
        identity = $identity
    }
}

function Assert-PluginId([string]$Value) {
    if ($Value -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$' -or
        $Value -in @('.', '..')) {
        Fail "Invalid filesystem-safe plugin id '$Value'."
    }
    $baseName = $Value.Split([char]'.')[0].ToUpperInvariant()
    if ($baseName -in @('CON', 'PRN', 'AUX', 'NUL') -or
        $baseName -match '^(COM|LPT)[1-9]$') {
        Fail "Invalid filesystem-safe plugin id '$Value'."
    }
}

function Assert-SnapshotId([string]$Value) {
    foreach ($character in $Value.ToCharArray()) {
        if ([char]::IsControl($character)) {
            Fail "Invalid filesystem-safe snapshot id '$Value'."
        }
    }
    if ($Value -notmatch '\A[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?\z' -or
        $Value -in @('.', '..')) {
        Fail "Invalid filesystem-safe snapshot id '$Value'."
    }
    $baseName = $Value.Split([char]'.')[0].ToUpperInvariant()
    if ($baseName -in @('CON', 'PRN', 'AUX', 'NUL') -or
        $baseName -match '^(COM|LPT)[1-9]$') {
        Fail "Invalid filesystem-safe snapshot id '$Value'."
    }
}

function Assert-RuntimeVersion([string]$Value) {
    if ([Text.Encoding]::UTF8.GetByteCount($Value) -gt 128) {
        Fail 'Runtime version exceeds the portable 128-character limit.'
    }
    foreach ($character in $Value.ToCharArray()) {
        if ([char]::IsControl($character)) {
            Fail "Invalid filesystem-safe runtime version '$Value'."
        }
    }
    if ($Value -notmatch '\A[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?\z' -or
        $Value -in @('.', '..')) {
        Fail "Invalid filesystem-safe runtime version '$Value'."
    }
    $baseName = $Value.Split([char]'.')[0].ToUpperInvariant()
    if ($baseName -in @('CON', 'PRN', 'AUX', 'NUL') -or
        $baseName -match '^(COM|LPT)[1-9]$') {
        Fail "Invalid filesystem-safe runtime version '$Value'."
    }
}

function Resolve-RelativeRoot([string]$PluginRootPath, [string]$Relative, [string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Relative) -or [IO.Path]::IsPathRooted($Relative)) {
        Fail "roots.$Name must be a non-empty relative path."
    }
    $segments = @($Relative -split '[\\/]')
    if ($segments -contains '..' -or $segments -contains '.') {
        Fail "roots.$Name may not escape or use dot segments."
    }
    $resolved = Canonical-Path (Join-Path $PluginRootPath $Relative)
    $prefix = (Canonical-Path $PluginRootPath) + [IO.Path]::DirectorySeparatorChar
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    if (-not $resolved.StartsWith($prefix, $comparison)) {
        Fail "roots.$Name escapes pluginRoot."
    }
    return $resolved
}

function Validate-ContextReceipt(
    [string]$ReceiptPath,
    [string]$ResolvedDurableHome,
    [string]$MarketplaceExpectation,
    [string]$PluginExpectation,
    [string]$PayloadExpectation,
    [string]$CellExpectation
) {
    $hostPlatform = $(if ($env:OS -eq 'Windows_NT') { 'windows' } else { 'posix' })
    if (-not (Test-EnvironmentPathRooted $ReceiptPath $hostPlatform)) {
        Fail 'The installation-context receipt pointer must be absolute.'
    }
    Assert-NotReparsePoint $ReceiptPath 'install.json'
    foreach ($expectation in @(
        @('expected payload root', $PayloadExpectation),
        @('expected cell root', $CellExpectation)
    )) {
        if ($expectation[1] -and
            -not (Test-EnvironmentPathRooted ([string]$expectation[1]) $hostPlatform)) {
            Fail "$($expectation[0]) must be absolute."
        }
    }
    $actualReceipt = Canonical-Path $ReceiptPath -MustExist
    $install = Read-Json $actualReceipt
    $installVersion = Get-PropertyValue $install 'version'
    Assert-PositiveInteger $installVersion 'install.json version'
    if ((Get-StringProperty $install 'schema') -cne 'copilot-extensions.plugin-installation' -or
        $installVersion -ne 1) {
        Fail 'install.json has an unsupported schema or version.'
    }
    $marketplaceId = Get-StringProperty $install 'marketplaceId'
    $receiptPluginId = Get-StringProperty $install 'pluginId'
    if (-not $marketplaceId -or -not $receiptPluginId) { Fail 'install.json identity is incomplete.' }
    if ($marketplaceId -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}$') {
        Fail "Invalid source-derived marketplace id '$marketplaceId'."
    }
    Assert-PluginId $receiptPluginId
    $lexicalMarketplacesRoot = Join-Path $ResolvedDurableHome 'marketplaces'
    Assert-NotReparsePoint $lexicalMarketplacesRoot 'The marketplaces root'
    $marketplacesRoot = Canonical-Path $lexicalMarketplacesRoot
    if (-not (Paths-Equal (Split-Path -Parent $marketplacesRoot) $ResolvedDurableHome)) {
        Fail 'The marketplaces root escapes the durable installation home.'
    }
    $lexicalCellRoot = Join-Path $marketplacesRoot $marketplaceId
    Assert-NotReparsePoint $lexicalCellRoot 'The marketplace cell root'
    $cellRoot = Canonical-Path $lexicalCellRoot
    if (-not (Paths-Equal (Split-Path -Parent $cellRoot) $marketplacesRoot)) {
        Fail 'The marketplace cell root escapes the marketplaces root.'
    }
    $lexicalPluginsRoot = Join-Path $cellRoot 'plugins'
    Assert-NotReparsePoint $lexicalPluginsRoot 'The cell plugins root'
    $pluginsRoot = Canonical-Path $lexicalPluginsRoot
    if (-not (Paths-Equal (Split-Path -Parent $pluginsRoot) $cellRoot)) {
        Fail 'The cell plugins root escapes the marketplace cell.'
    }
    $lexicalPluginRoot = Join-Path $pluginsRoot $receiptPluginId
    Assert-NotReparsePoint $lexicalPluginRoot 'The plugin root'
    $expectedPluginRoot = Canonical-Path $lexicalPluginRoot
    if (-not (Paths-Equal (Split-Path -Parent $expectedPluginRoot) $pluginsRoot)) {
        Fail 'The plugin root escapes the cell plugins root.'
    }
    $lexicalCanonicalReceipt = Join-Path $expectedPluginRoot 'install.json'
    Assert-NotReparsePoint $lexicalCanonicalReceipt 'install.json'
    $canonicalReceipt = Canonical-Path $lexicalCanonicalReceipt
    if (-not (Paths-Equal $actualReceipt $canonicalReceipt)) {
        Fail "install.json is not at its exact canonical receipt location '$canonicalReceipt'."
    }
    if (-not (Paths-Equal (Get-StringProperty $install 'pluginRoot') $expectedPluginRoot)) {
        Fail 'install.json pluginRoot does not match its canonical cell/plugin location.'
    }
    if ($MarketplaceExpectation -and $marketplaceId -cne $MarketplaceExpectation) {
        Fail "Expected marketplace '$MarketplaceExpectation', receipt names '$marketplaceId'."
    }
    if ($PluginExpectation -and $receiptPluginId -cne $PluginExpectation) {
        Fail "Expected plugin '$PluginExpectation', receipt names '$receiptPluginId'."
    }
    if ($CellExpectation -and -not (Paths-Equal $cellRoot $CellExpectation)) {
        Fail "Expected cell '$CellExpectation', receipt belongs to '$cellRoot'."
    }
    Assert-ReceiptGeneration (Get-PropertyValue $install 'generation') 'install.json generation'
    Assert-ReceiptState (Get-StringProperty $install 'state') 'install.json state'

    $lexicalNamespacePath = Join-Path $cellRoot 'namespace.json'
    Assert-NotReparsePoint $lexicalNamespacePath 'namespace.json'
    $namespacePath = Canonical-Path $lexicalNamespacePath
    if (-not (Paths-Equal (Get-StringProperty $install 'namespaceReceipt') $namespacePath)) {
        Fail 'install.json namespaceReceipt is not the exact namespace receipt in the same cell.'
    }
    $validatedNamespace = Validate-NamespaceReceipt $lexicalNamespacePath $ResolvedDurableHome
    if ($validatedNamespace.marketplaceId -cne $marketplaceId) {
        Fail 'namespace.json marketplaceId does not match install.json.'
    }
    $identity = $validatedNamespace.identity

    $payload = Get-PropertyValue $install 'payload'
    $payloadPath = Get-StringProperty $payload 'root'
    if (-not [IO.Path]::IsPathRooted($payloadPath)) { Fail 'payload.root must be absolute.' }
    if ([string]::IsNullOrWhiteSpace((Get-StringProperty $payload 'version'))) {
        Fail 'payload.version must be a non-empty string.'
    }
    if ((Get-StringProperty $payload 'origin') -cnotin
        @('installed', 'directory', 'staged', 'explicit')) {
        Fail 'payload.origin must be installed, directory, staged, or explicit.'
    }
    $payloadOriginReceipt = Get-PropertyValue $payload 'originReceipt'
    if ($null -ne $payloadOriginReceipt) {
        if ($payloadOriginReceipt -isnot [string]) {
            Fail 'payload.originReceipt must be a string.'
        }
        if (-not [IO.Path]::IsPathRooted($payloadOriginReceipt)) {
            Fail 'payload.originReceipt must be absolute.'
        }
    }
    $payloadPath = Canonical-Path $payloadPath
    if ($PayloadExpectation -and -not (Paths-Equal $payloadPath $PayloadExpectation)) {
        Fail "Expected payload '$PayloadExpectation', receipt names '$payloadPath'."
    }
    if ($env:COPILOT_PLUGIN_ROOT) {
        if (-not [IO.Path]::IsPathRooted($env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT must be absolute.'
        }
        if (-not (Paths-Equal $payloadPath $env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT conflicts with the validated payload root.'
        }
    }

    $rootsReceipt = Get-PropertyValue $install 'roots'
    $rootNames = @('versions', 'snapshots', 'state', 'run', 'logs', 'cache', 'launchers')
    $roots = [ordered]@{}
    foreach ($name in $rootNames) {
        $roots[$name + 'Root'] = Resolve-RelativeRoot $expectedPluginRoot (Get-StringProperty $rootsReceipt $name) $name
    }
    return [pscustomobject][ordered]@{
        action = 'validate'
        marketplaceId = $marketplaceId
        marketplaceSlot = $marketplaceId
        sourceFingerprint = $identity.fingerprint
        source = [pscustomobject][ordered]@{
            kind = $identity.kind
            canonical = $identity.canonical
            ref = $identity.ref
        }
        pluginId = $receiptPluginId
        payloadRoot = $payloadPath
        cellRoot = $cellRoot
        pluginRoot = $expectedPluginRoot
        versionsRoot = $roots.versionsRoot
        snapshotsRoot = $roots.snapshotsRoot
        stateRoot = $roots.stateRoot
        runRoot = $roots.runRoot
        logsRoot = $roots.logsRoot
        cacheRoot = $roots.cacheRoot
        launchersRoot = $roots.launchersRoot
        reposRoot = Canonical-Path (Join-Path $cellRoot 'repos')
        namespaceReceipt = $namespacePath
        installReceipt = $actualReceipt
        namespaceGeneration = Get-PropertyValue $validatedNamespace.receipt 'generation'
        generation = Get-PropertyValue $install 'generation'
        state = Get-PropertyValue $install 'state'
    }
}

function Assert-ExactChoice([string]$Value, [string[]]$Allowed, [string]$Name) {
    if ($Allowed -cnotcontains $Value) {
        Fail "$Name must be one of: $($Allowed -join ', ')."
    }
}

function Assert-MarketplaceId([string]$Value) {
    if ($Value -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}$') {
        Fail "Invalid source-derived marketplace id '$Value'."
    }
}

function Assert-JsonObject($Value, [string]$Name) {
    if ($null -eq $Value -or
        ($Value.GetType().Name -ne 'PSCustomObject' -and
         $Value -isnot [System.Collections.IDictionary])) {
        Fail "$Name must be an object."
    }
}

function Has-ExactProperty($Object, [string]$Name) {
    if ($null -eq $Object) { return $false }
    foreach ($property in $Object.PSObject.Properties) {
        if ($property.Name -ceq $Name) { return $true }
        if ($property.Name -ieq $Name) {
            Fail "JSON property '$($property.Name)' conflicts with exact case '$Name'."
        }
    }
    return $false
}

function Get-OptionalBooleanField($Object, [string]$Name, [string]$Label) {
    if (-not (Has-ExactProperty $Object $Name)) {
        return [pscustomobject][ordered]@{
            present = $false
            value = $false
        }
    }
    $value = Get-PropertyValue $Object $Name
    if ($value -isnot [bool]) {
        Fail "$Label must be a boolean."
    }
    return [pscustomobject][ordered]@{
        present = $true
        value = [bool]$value
    }
}

function Normalize-ShortHost([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    return $Value.Split('.')[0].ToLowerInvariant()
}

function Read-ExactUtcTimestampValue($Value, [string]$Name) {
    if ($Value -isnot [string]) {
        Fail "$Name must be a string."
    }
    if ($Value -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') {
        Fail "$Name must be an RFC3339 UTC timestamp with seconds and Z."
    }
    $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
    $parsed = [DateTime]::MinValue
    if (-not [DateTime]::TryParseExact(
        $Value,
        'yyyy-MM-ddTHH:mm:ssZ',
        [Globalization.CultureInfo]::InvariantCulture,
        $styles,
        [ref]$parsed
    )) {
        Fail "$Name must be an RFC3339 UTC timestamp with seconds and Z."
    }
    return $parsed.ToUniversalTime().ToString(
        'yyyy-MM-ddTHH:mm:ssZ',
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function Test-SameEnvironment($Left, $Right) {
    if ($null -eq $Left -or $null -eq $Right) { return $false }
    if ((Get-StringProperty $Left 'platform') -cne (Get-StringProperty $Right 'platform')) {
        return $false
    }
    if ((Get-StringProperty $Left 'homeRealPath') -cne (Get-StringProperty $Right 'homeRealPath')) {
        return $false
    }
    $leftDistro = Get-PropertyValue $Left 'wslDistro'
    $rightDistro = Get-PropertyValue $Right 'wslDistro'
    if ($null -eq $leftDistro -and $null -eq $rightDistro) { return $true }
    if ($leftDistro -isnot [string] -or $rightDistro -isnot [string]) { return $false }
    return $leftDistro -ceq $rightDistro
}

function Test-EnvironmentPathRooted([string]$Path, [string]$Platform) {
    if ($Platform -ceq 'windows') {
        return (
            $Path -match '^[A-Za-z]:[\\/]' -or
            $Path -match '^\\\\[^\\/]+[\\/][^\\/]+'
        )
    }
    return $Path.StartsWith('/', [StringComparison]::Ordinal)
}

function Get-CurrentEnvironment {
    if ($env:OS -eq 'Windows_NT') {
        if (-not $env:USERPROFILE) { Fail 'USERPROFILE is required on Windows.' }
        if (-not [IO.Path]::IsPathRooted($env:USERPROFILE)) {
            Fail 'USERPROFILE must be absolute.'
        }
        $homeRealPath = Canonical-Path $env:USERPROFILE -MustExist
        return [pscustomobject][ordered]@{
            platform = 'windows'
            homeRealPath = $homeRealPath
            wslDistro = $null
        }
    }

    try {
        $passwdHome = [CePosixAccount]::Home()
    }
    catch {
        Fail "Cannot determine the account home from the passwd database: $($_.Exception.Message)"
    }
    if (-not [IO.Path]::IsPathRooted($passwdHome)) {
        Fail 'The passwd database home must be absolute.'
    }
    $homeRealPath = Canonical-Path $passwdHome -MustExist
    $wslDistro = $null
    if (-not [string]::IsNullOrEmpty($env:WSL_DISTRO_NAME)) {
        $wslDistro = [string]$env:WSL_DISTRO_NAME
    }
    return [pscustomobject][ordered]@{
        platform = 'posix'
        homeRealPath = $homeRealPath
        wslDistro = $wslDistro
    }
}

function Read-EnvironmentObject($EnvironmentObject, [string]$Label) {
    Assert-JsonObject $EnvironmentObject $Label
    $platform = Get-StringProperty $EnvironmentObject 'platform'
    if ($platform -cnotin @('windows', 'posix')) {
        Fail "$Label.platform must be windows or posix."
    }
    $homeRealPath = Get-StringProperty $EnvironmentObject 'homeRealPath'
    if (-not (Test-EnvironmentPathRooted $homeRealPath $platform)) {
        Fail "$Label.homeRealPath must be absolute."
    }
    if (-not (Has-ExactProperty $EnvironmentObject 'wslDistro')) {
        Fail "$Label.wslDistro is required."
    }
    $wslDistro = Get-PropertyValue $EnvironmentObject 'wslDistro'
    if ($null -ne $wslDistro -and $wslDistro -isnot [string]) {
        Fail "$Label.wslDistro must be a string or null."
    }
    if ($platform -ceq 'windows' -and $null -ne $wslDistro) {
        Fail "$Label.wslDistro must be null on Windows."
    }
    return [pscustomobject][ordered]@{
        platform = $platform
        homeRealPath = $homeRealPath
        wslDistro = $wslDistro
    }
}

function Invoke-WithoutPluginRoot([scriptblock]$ScriptBlock) {
    $hadVariable = Test-Path Env:COPILOT_PLUGIN_ROOT
    $savedValue = $env:COPILOT_PLUGIN_ROOT
    try {
        Remove-Item Env:COPILOT_PLUGIN_ROOT -ErrorAction SilentlyContinue
        return & $ScriptBlock
    }
    finally {
        if ($hadVariable) {
            $env:COPILOT_PLUGIN_ROOT = $savedValue
        }
        else {
            Remove-Item Env:COPILOT_PLUGIN_ROOT -ErrorAction SilentlyContinue
        }
    }
}

function Read-LegacyProbe {
    if ($LegacyProbeJson -and $LegacyProbeFile) {
        Fail 'Specify only one of -LegacyProbeJson and -LegacyProbeFile.'
    }

    if ($LegacyProbeJson) {
        $probe = Read-JsonText $LegacyProbeJson '-LegacyProbeJson'
    }
    elseif ($LegacyProbeFile) {
        $probe = Read-Json $LegacyProbeFile
    }
    else {
        $probe = [pscustomobject][ordered]@{
            declared = $false
            result = 'unknown'
            checkedAt = $null
        }
    }

    Assert-JsonObject $probe 'legacy probe'
    if (-not (Has-ExactProperty $probe 'declared')) {
        Fail 'legacy probe.declared is required.'
    }
    $declared = Get-PropertyValue $probe 'declared'
    if ($declared -isnot [bool]) {
        Fail 'legacy probe.declared must be a boolean.'
    }
    if (-not (Has-ExactProperty $probe 'result')) {
        Fail 'legacy probe.result is required.'
    }
    $result = Get-StringProperty $probe 'result'
    if ($result -cnotin @('absent', 'present', 'unknown')) {
        Fail 'legacy probe.result must be absent, present, or unknown.'
    }
    if (-not (Has-ExactProperty $probe 'checkedAt')) {
        Fail 'legacy probe.checkedAt is required.'
    }
    $checkedAt = Get-PropertyValue $probe 'checkedAt'
    if ($null -ne $checkedAt) {
        $checkedAt = Read-ExactUtcTimestampValue $checkedAt 'legacy probe.checkedAt'
    }
    if (-not $declared -and $result -cne 'unknown') {
        Fail 'legacy probe.result must be unknown when declared is false.'
    }
    return [pscustomobject][ordered]@{
        declared = [bool]$declared
        result = $result
        checkedAt = $checkedAt
    }
}

function Test-ProvenanceBlockedMessage([string]$Message) {
    return (
        $Message -like 'No user or explicit project extraKnownMarketplaces declaration found*' -or
        $Message -like 'Conflicting declarations for marketplace key*' -or
        $Message -like 'Cannot establish marketplace provenance*' -or
        $Message -like "Source '*' already owns cell/locator*"
    )
}

function Get-InstalledPayloadPluginId(
    [string]$ResolvedPayload,
    [string]$ResolvedCopilotHome
) {
    $installed = Canonical-Path (Join-Path $ResolvedCopilotHome 'installed-plugins')
    $prefix = $installed + [IO.Path]::DirectorySeparatorChar
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    if (-not $ResolvedPayload.StartsWith($prefix, $comparison)) { return '' }
    $relative = $ResolvedPayload.Substring($prefix.Length)
    $parts = @($relative -split '[\\/]')
    if ($parts.Count -ne 2) { return '' }
    return $parts[1]
}

function Resolve-StatusTarget(
    [string]$ResolvedCopilotHome,
    [string]$ResolvedProjectRoot,
    [string]$ResolvedDurableHome
) {
    $target = [ordered]@{
        provenanceBlocked = $false
        provenanceReason = ''
        invalidContext = $false
        invalidReason = ''
        marketplaceId = $null
        pluginId = $null
        payloadRoot = $null
        cellRoot = $null
        pluginRoot = $null
        namespaceReceipt = $null
        installReceipt = $null
        source = $null
        sourceFingerprint = $null
        validatedContext = $null
    }

    $pointer = $Context
    if (-not $pointer) { $pointer = $env:COPILOT_EXTENSIONS_CONTEXT }
    if ($pointer) {
        if (-not [IO.Path]::IsPathRooted($pointer)) {
            Fail 'The installation-context receipt pointer must be absolute.'
        }
        $pointerPath = Canonical-Path $pointer
        $payloadExpectation = $ExpectedPayloadRoot
        if (-not $payloadExpectation -and $PayloadRoot) { $payloadExpectation = $PayloadRoot }
        if (-not $payloadExpectation -and $env:COPILOT_PLUGIN_ROOT) {
            $payloadExpectation = $env:COPILOT_PLUGIN_ROOT
        }
        $pluginExpectation = $PluginId
        if (-not $pluginExpectation) { $pluginExpectation = $ExpectedPluginId }
        try {
            $pointerPath = Canonical-Path $pointer -MustExist
            $validated = Validate-ContextReceipt $pointerPath $ResolvedDurableHome $ExpectedMarketplaceId $pluginExpectation $payloadExpectation $ExpectedCellRoot
            $target.marketplaceId = [string]$validated.marketplaceId
            $target.pluginId = [string]$validated.pluginId
            $target.payloadRoot = [string]$validated.payloadRoot
            $target.cellRoot = [string]$validated.cellRoot
            $target.pluginRoot = [string]$validated.pluginRoot
            $target.namespaceReceipt = [string]$validated.namespaceReceipt
            $target.installReceipt = [string]$validated.installReceipt
            $target.source = $validated.source
            $target.sourceFingerprint = [string]$validated.sourceFingerprint
            $target.validatedContext = $validated
            return [pscustomobject]$target
        }
        catch {
            $target.invalidContext = $true
            $target.invalidReason = $_.Exception.Message
            if ((Split-Path -Leaf $pointerPath) -ceq 'install.json') {
                $pluginRoot = Split-Path -Parent $pointerPath
                $pluginsRoot = Split-Path -Parent $pluginRoot
                $cellRoot = Split-Path -Parent $pluginsRoot
                if ((Split-Path -Leaf $pluginsRoot) -ceq 'plugins') {
                    $target.pluginId = Split-Path -Leaf $pluginRoot
                    $target.marketplaceId = Split-Path -Leaf $cellRoot
                    $target.cellRoot = $cellRoot
                    $target.pluginRoot = $pluginRoot
                    $target.namespaceReceipt = Canonical-Path (Join-Path $cellRoot 'namespace.json')
                    $target.installReceipt = $pointerPath
                }
            }
            return [pscustomobject]$target
        }
    }

    if (-not $PayloadRoot) { $PayloadRoot = $env:COPILOT_PLUGIN_ROOT }
    if (-not $PayloadRoot) { Fail "$Action requires -PayloadRoot or COPILOT_PLUGIN_ROOT." }
    if (-not [IO.Path]::IsPathRooted($PayloadRoot)) {
        Fail 'The payload root must be absolute.'
    }

    $resolvedPayload = Canonical-Path $PayloadRoot -MustExist
    if (-not (Test-Path -LiteralPath $resolvedPayload -PathType Container)) {
        Fail "The payload root must be an existing directory: $resolvedPayload"
    }
    if ($env:COPILOT_PLUGIN_ROOT) {
        if (-not [IO.Path]::IsPathRooted($env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT must be absolute.'
        }
        if (-not (Paths-Equal $resolvedPayload $env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT conflicts with -PayloadRoot.'
        }
    }
    $target.payloadRoot = $resolvedPayload

    $descriptor = Read-SourceDescriptor
    $evidence = $null
    if ($null -ne $descriptor) {
        if (-not $PluginId) { Fail 'Explicit source resolution requires -PluginId.' }
        $evidence = [pscustomobject]@{
            source = Normalize-Source $descriptor ''
            pluginId = $PluginId
            readableName = $(if ($MarketplaceKey) { $MarketplaceKey } else { 'marketplace' })
            locator = $null
        }
    }
    else {
        try {
            $evidence = Resolve-InstalledEvidence $resolvedPayload $ResolvedCopilotHome $ResolvedProjectRoot
        }
        catch {
            if (Test-ProvenanceBlockedMessage $_.Exception.Message) {
                $target.provenanceBlocked = $true
                $target.provenanceReason = $_.Exception.Message
                $target.pluginId = $(if ($PluginId) { $PluginId } else { Get-InstalledPayloadPluginId $resolvedPayload $ResolvedCopilotHome })
                return [pscustomobject]$target
            }
            throw
        }
        if ($null -eq $evidence) {
            try {
                $evidence = Resolve-DirectoryEvidence $resolvedPayload $PluginId
            }
            catch {
                if (Test-ProvenanceBlockedMessage $_.Exception.Message) {
                    $target.provenanceBlocked = $true
                    $target.provenanceReason = $_.Exception.Message
                    $target.pluginId = $PluginId
                    return [pscustomobject]$target
                }
                throw
            }
        }
        if ($null -eq $evidence) {
            $target.provenanceBlocked = $true
            $target.provenanceReason = "Cannot establish marketplace provenance for payload '$resolvedPayload'. Supply an explicit source descriptor for management/development mode."
            $target.pluginId = $PluginId
            return [pscustomobject]$target
        }
        if ($PluginId -and $PluginId -cne $evidence.pluginId) {
            Fail "Expected plugin '$PluginId', payload evidence identifies '$($evidence.pluginId)'."
        }
    }

    Assert-PluginId $evidence.pluginId
    $identity = Source-Identity $evidence.source $evidence.readableName
    $existing = @(Find-ExistingSource $ResolvedDurableHome $identity.fingerprint $identity.marketplaceId $evidence.locator)
    $rebind = @($existing | Where-Object { -not $_.sameId -or -not $_.locatorMatch })
    if ($rebind.Count -gt 0) {
        $target.provenanceBlocked = $true
        $target.provenanceReason = "Source '$($identity.fingerprint)' already owns cell/locator '$((@($rebind | ForEach-Object { $_.marketplaceId }) -join ', '))'; explicit rebind or new-cell intent is required."
    }
    $target.marketplaceId = [string]$identity.marketplaceId
    $target.pluginId = [string]$evidence.pluginId
    $target.source = [pscustomobject][ordered]@{
        kind = $identity.kind
        canonical = $identity.canonical
        ref = $identity.ref
    }
    $target.sourceFingerprint = [string]$identity.fingerprint
    $target.cellRoot = Canonical-Path (Join-Path (Join-Path $ResolvedDurableHome 'marketplaces') $identity.marketplaceId)
    $target.pluginRoot = Canonical-Path (Join-Path (Join-Path $target.cellRoot 'plugins') $evidence.pluginId)
    $target.namespaceReceipt = Canonical-Path (Join-Path $target.cellRoot 'namespace.json')
    $target.installReceipt = Canonical-Path (Join-Path $target.pluginRoot 'install.json')
    if ($target.provenanceBlocked) {
        return [pscustomobject]$target
    }
    if (Test-Path -LiteralPath $target.installReceipt -PathType Leaf) {
        try {
            $validated = Validate-ContextReceipt $target.installReceipt $ResolvedDurableHome $identity.marketplaceId $evidence.pluginId $resolvedPayload $target.cellRoot
            $target.validatedContext = $validated
        }
        catch {
            $target.invalidContext = $true
            $target.invalidReason = $_.Exception.Message
        }
    }
    return [pscustomobject]$target
}

function Read-PolicyDecision(
    $CurrentEnvironment,
    [string]$MarketplaceId,
    [string]$PluginId
) {
    $pathEntry = Join-Path (Join-Path $CurrentEnvironment.homeRealPath '.copilot-extensions') 'installation-mode.json'
    $authoritative = $true
    if ($PolicyPath) {
        if (-not [IO.Path]::IsPathRooted($PolicyPath)) {
            Fail '-PolicyPath must be absolute.'
        }
        $pathEntry = $PolicyPath
        $authoritative = $false
    }
    $path = Canonical-Path $pathEntry

    $policy = [ordered]@{
        path = $path
        authoritative = $authoritative
        state = 'missing'
        scope = 'default'
        enabled = $false
        reason = 'policy-default-false'
    }

    $pathItem = Get-Item -LiteralPath $pathEntry -Force -ErrorAction SilentlyContinue
    if ($null -eq $pathItem) {
        if (-not $authoritative) {
            $policy.reason = 'policy-injected-non-authoritative'
        }
        return [pscustomobject]$policy
    }
    $linkType = Get-PropertyValue $pathItem 'LinkType'
    if ($pathItem.PSIsContainer -or $linkType) {
        $policy.state = 'invalid'
        $policy.enabled = $null
        $policy.reason = 'policy-invalid'
        return [pscustomobject]$policy
    }

    try {
        $document = Read-Json $path
        Assert-JsonObject $document 'installation-mode.json'
        if ((Get-StringProperty $document 'schema') -cne 'copilot-extensions.installation-mode') {
            Fail 'installation-mode.json has an unsupported schema.'
        }
        $version = Get-PropertyValue $document 'version'
        Assert-PositiveInteger $version 'installation-mode.json version'
        if ($version -gt 1) {
            $policy.state = 'unsupported'
            $policy.enabled = $null
            $policy.reason = 'policy-version-unsupported'
            return [pscustomobject]$policy
        }
        if ($version -ne 1) {
            Fail 'installation-mode.json has an unsupported schema.'
        }

        if (Has-ExactProperty $document 'installationMode') {
            $installationMode = Get-PropertyValue $document 'installationMode'
            Assert-JsonObject $installationMode 'installation-mode.json installationMode'
        }
        else {
            $installationMode = [pscustomobject]@{}
        }

        $winningScope = 'default'
        $winningEnabled = $false
        $winningReason = 'policy-default-false'

        $globalEnabled = Get-OptionalBooleanField $installationMode 'enabled' 'installationMode.enabled'
        if ($globalEnabled.present) {
            $winningScope = 'global'
            $winningEnabled = $globalEnabled.value
            $winningReason = $(if ($globalEnabled.value) { 'policy-global-true' } else { 'policy-global-false' })
        }

        $marketplaces = $null
        if (Has-ExactProperty $installationMode 'marketplaces') {
            $marketplaces = Get-PropertyValue $installationMode 'marketplaces'
            Assert-JsonObject $marketplaces 'installationMode.marketplaces'
            foreach ($marketplaceProperty in $marketplaces.PSObject.Properties) {
                Assert-MarketplaceId $marketplaceProperty.Name
                Assert-JsonObject $marketplaceProperty.Value "installationMode.marketplaces.$($marketplaceProperty.Name)"
                $marketplaceEnabled = Get-OptionalBooleanField $marketplaceProperty.Value 'enabled' "installationMode.marketplaces.$($marketplaceProperty.Name).enabled"
                if ($MarketplaceId -and $marketplaceProperty.Name -ceq $MarketplaceId -and $marketplaceEnabled.present) {
                    $winningScope = 'marketplace'
                    $winningEnabled = $marketplaceEnabled.value
                    $winningReason = $(if ($marketplaceEnabled.value) { 'policy-marketplace-true' } else { 'policy-marketplace-false' })
                }

                if (Has-ExactProperty $marketplaceProperty.Value 'plugins') {
                    $plugins = Get-PropertyValue $marketplaceProperty.Value 'plugins'
                    Assert-JsonObject $plugins "installationMode.marketplaces.$($marketplaceProperty.Name).plugins"
                    foreach ($pluginProperty in $plugins.PSObject.Properties) {
                        Assert-PluginId $pluginProperty.Name
                        Assert-JsonObject $pluginProperty.Value "installationMode.marketplaces.$($marketplaceProperty.Name).plugins.$($pluginProperty.Name)"
                        $pluginEnabled = Get-OptionalBooleanField $pluginProperty.Value 'enabled' "installationMode.marketplaces.$($marketplaceProperty.Name).plugins.$($pluginProperty.Name).enabled"
                        if ($MarketplaceId -and $PluginId -and
                            $marketplaceProperty.Name -ceq $MarketplaceId -and
                            $pluginProperty.Name -ceq $PluginId -and
                            $pluginEnabled.present) {
                            $winningScope = 'plugin'
                            $winningEnabled = $pluginEnabled.value
                            $winningReason = $(if ($pluginEnabled.value) { 'policy-plugin-true' } else { 'policy-plugin-false' })
                        }
                    }
                }
            }
        }

        $policy.state = 'valid'
        $policy.scope = $winningScope
        $policy.enabled = $winningEnabled
        $policy.reason = $winningReason
        if (-not $authoritative) {
            $policy.reason = 'policy-injected-non-authoritative'
        }
        return [pscustomobject]$policy
    }
    catch {
        $policy.state = 'invalid'
        $policy.enabled = $null
        $policy.reason = 'policy-invalid'
        return [pscustomobject]$policy
    }
}

function Read-MaintenanceState([string]$MarkerPath, [string]$SidecarPath, [string]$Scope) {
    $maintenance = [ordered]@{
        state = 'inactive'
        scope = 'none'
        marker = $null
        sidecar = $null
        reason = ''
    }
    $markerItem = Get-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
    if ($null -eq $markerItem) {
        return [pscustomobject]$maintenance
    }

    $maintenance.scope = $Scope
    $maintenance.marker = [IO.Path]::GetFullPath($MarkerPath)
    $maintenance.sidecar = [IO.Path]::GetFullPath($SidecarPath)
    $markerLinkType = Get-PropertyValue $markerItem 'LinkType'
    $sidecarItem = Get-Item -LiteralPath $SidecarPath -Force -ErrorAction SilentlyContinue
    $sidecarLinkType = Get-PropertyValue $sidecarItem 'LinkType'
    if ($markerLinkType -or $null -eq $sidecarItem -or
        $sidecarItem.PSIsContainer -or $sidecarLinkType) {
        $maintenance.state = 'stale'
        $maintenance.reason = 'maintenance-stale'
        return [pscustomobject]$maintenance
    }
    $maintenance.sidecar = Canonical-Path $SidecarPath -MustExist

    try {
        $sidecar = Read-Json $maintenance.sidecar
        Assert-JsonObject $sidecar 'maintenance.json'
        $owner = Get-StringProperty $sidecar 'owner'
        $host = Get-StringProperty $sidecar 'host'
        $pid = Get-PropertyValue $sidecar 'pid'
        $reason = Get-StringProperty $sidecar 'reason'
        $enteredAt = Read-ExactUtcTimestampValue (Get-PropertyValue $sidecar 'enteredAt') 'maintenance.json enteredAt'
        $expectedUntil = Read-ExactUtcTimestampValue (Get-PropertyValue $sidecar 'expectedUntil') 'maintenance.json expectedUntil'
        if (-not $owner) { Fail 'maintenance.json owner must be non-empty.' }
        if (-not $reason) { Fail 'maintenance.json reason must be non-empty.' }
        Assert-PositiveInteger $pid 'maintenance.json pid'
        $now = [DateTime]::UtcNow
        $currentHost = Normalize-ShortHost ([Environment]::MachineName)
        $enteredTime = [DateTime]::ParseExact(
            $enteredAt,
            'yyyy-MM-ddTHH:mm:ssZ',
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
        )
        $expectedTime = [DateTime]::ParseExact(
            $expectedUntil,
            'yyyy-MM-ddTHH:mm:ssZ',
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
        )
        if ((Normalize-ShortHost $host) -ceq $currentHost -and
            $enteredTime -le $now -and
            $now -le $expectedTime -and
            $null -ne (Get-Process -Id ([int]$pid) -ErrorAction SilentlyContinue)) {
            $maintenance.state = 'active'
            $maintenance.reason = 'maintenance-active'
            return [pscustomobject]$maintenance
        }
        $maintenance.state = 'stale'
        $maintenance.reason = 'maintenance-stale'
        return [pscustomobject]$maintenance
    }
    catch {
        $maintenance.state = 'stale'
        $maintenance.reason = 'maintenance-stale'
        return [pscustomobject]$maintenance
    }
}

function Read-EffectiveMaintenance($CurrentEnvironment, [string]$PluginRoot) {
    $userBase = Join-Path $CurrentEnvironment.homeRealPath '.copilot-extensions'
    $user = Read-MaintenanceState (Join-Path $userBase 'maintenance') (Join-Path $userBase 'maintenance.json') 'user'
    if ($user.scope -cne 'none') { return $user }
    if (-not $PluginRoot) { return $user }
    return Read-MaintenanceState (Join-Path $PluginRoot 'maintenance') (Join-Path $PluginRoot 'maintenance.json') 'plugin'
}

function Validate-ActivationReceipt(
    [string]$ActivationPath,
    [string]$ResolvedDurableHome,
    [string]$ExpectedMarketplaceId,
    [string]$ExpectedPluginId,
    [string]$ResolvedLegacyRoot,
    $CurrentEnvironment
) {
    $result = [ordered]@{
        path = $null
        present = $false
        classification = 'absent'
        marketplaceId = $ExpectedMarketplaceId
        pluginId = $ExpectedPluginId
        mode = $null
        state = $null
        context = $null
        runtimeRoot = $null
        activationGeneration = $null
        namespaceGeneration = $null
        installGeneration = $null
    }
    if ([string]::IsNullOrWhiteSpace($ActivationPath)) {
        return [pscustomobject]$result
    }
    $activationItem = Get-Item -LiteralPath $ActivationPath -Force -ErrorAction SilentlyContinue
    if ($null -eq $activationItem) {
        return [pscustomobject]$result
    }

    $result.present = $true
    $result.path = Canonical-Path $ActivationPath
    $activationLinkType = Get-PropertyValue $activationItem 'LinkType'
    if ($activationItem.PSIsContainer -or $activationLinkType) {
        $result.classification = 'invalid'
        return [pscustomobject]$result
    }
    $result.path = Canonical-Path $ActivationPath -MustExist
    try {
        $activation = Read-Json $result.path
        Assert-JsonObject $activation 'installation-activation.json'
        $version = Get-PropertyValue $activation 'version'
        Assert-PositiveInteger $version 'installation-activation.json version'
        if ((Get-StringProperty $activation 'schema') -cne 'copilot-extensions.installation-activation' -or
            $version -ne 1) {
            Fail 'installation-activation.json has an unsupported schema or version.'
        }
        $marketplaceId = Get-StringProperty $activation 'marketplaceId'
        $pluginId = Get-StringProperty $activation 'pluginId'
        if ($marketplaceId -cne $ExpectedMarketplaceId -or $pluginId -cne $ExpectedPluginId) {
            Fail 'installation-activation.json does not match its canonical cell/plugin location.'
        }
        $mode = Get-StringProperty $activation 'mode'
        $state = Get-StringProperty $activation 'state'
        if ($mode -ceq 'namespaced' -and $state -ceq 'active') {
        }
        elseif ($mode -ceq 'legacy' -and $state -ceq 'deactivated') {
        }
        else {
            Fail 'installation-activation.json mode/state pair is invalid.'
        }
        $result.mode = $mode
        $result.state = $state
        $environment = Read-EnvironmentObject (Get-PropertyValue $activation 'environment') 'installation-activation.json environment'
        if (-not (Test-SameEnvironment $environment $CurrentEnvironment)) {
            $result.classification = 'foreign-environment'
            return [pscustomobject]$result
        }
        $context = Get-StringProperty $activation 'context'
        if (-not [IO.Path]::IsPathRooted($context)) {
            Fail 'installation-activation.json context must be absolute.'
        }
        $cellRoot = Canonical-Path (Join-Path (Join-Path $ResolvedDurableHome 'marketplaces') $ExpectedMarketplaceId)
        $validatedContext = Invoke-WithoutPluginRoot {
            Validate-ContextReceipt $context $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' $cellRoot
        }
        $namespaceGeneration = Get-PropertyValue $activation 'namespaceGeneration'
        $installGeneration = Get-PropertyValue $activation 'installGeneration'
        $generation = Get-PropertyValue $activation 'generation'
        Assert-ReceiptGeneration $namespaceGeneration 'installation-activation.json namespaceGeneration'
        Assert-ReceiptGeneration $installGeneration 'installation-activation.json installGeneration'
        Assert-ReceiptGeneration $generation 'installation-activation.json generation'
        $result.activationGeneration = [long]$generation
        $result.namespaceGeneration = [long]$namespaceGeneration
        $result.installGeneration = [long]$installGeneration
        $legacyEvidence = Get-PropertyValue $activation 'legacy'
        Assert-JsonObject $legacyEvidence 'installation-activation.json legacy'
        $legacyDisposition = Get-StringProperty $legacyEvidence 'disposition'
        if ($legacyDisposition -cnotin @('absent', 'quiesced', 'retained-inert', 'restored')) {
            Fail 'installation-activation.json legacy.disposition is invalid.'
        }
        $recordedProbe = Get-PropertyValue $legacyEvidence 'probe'
        Assert-JsonObject $recordedProbe 'installation-activation.json legacy.probe'
        $recordedDeclared = Get-PropertyValue $recordedProbe 'declared'
        if ($recordedDeclared -isnot [bool]) {
            Fail 'installation-activation.json legacy.probe.declared must be a boolean.'
        }
        $recordedResult = Get-StringProperty $recordedProbe 'result'
        if ($recordedResult -cnotin @('absent', 'present', 'unknown')) {
            Fail 'installation-activation.json legacy.probe.result is invalid.'
        }
        if (-not $recordedDeclared -and $recordedResult -cne 'unknown') {
            Fail 'installation-activation.json undeclared legacy probe must be unknown.'
        }
        if (-not (Has-ExactProperty $recordedProbe 'checkedAt')) {
            Fail 'installation-activation.json legacy.probe.checkedAt is required.'
        }
        $recordedCheckedAt = Get-PropertyValue $recordedProbe 'checkedAt'
        if ($null -ne $recordedCheckedAt) {
            [void](Read-ExactUtcTimestampValue $recordedCheckedAt 'installation-activation.json legacy.probe.checkedAt')
        }
        $createdAt = Read-ExactUtcTimestampValue (Get-PropertyValue $activation 'createdAt') 'installation-activation.json createdAt'
        $updatedAt = Read-ExactUtcTimestampValue (Get-PropertyValue $activation 'updatedAt') 'installation-activation.json updatedAt'
        if ([string]::CompareOrdinal($updatedAt, $createdAt) -lt 0) {
            Fail 'installation-activation.json updatedAt precedes createdAt.'
        }
        $result.context = [string]$validatedContext.installReceipt
        $result.installGeneration = [long]$validatedContext.generation
        if ($mode -ceq 'namespaced') {
            $result.runtimeRoot = [string]$validatedContext.pluginRoot
        }
        else {
            $result.runtimeRoot = $ResolvedLegacyRoot
        }
        if ([long]$validatedContext.namespaceGeneration -ne [long]$namespaceGeneration -or
            [long]$validatedContext.generation -ne [long]$installGeneration) {
            $result.classification = 'revalidation-required'
            return [pscustomobject]$result
        }
        $result.classification = 'valid'
        return [pscustomobject]$result
    }
    catch {
        $result.classification = 'invalid'
        return [pscustomobject]$result
    }
}

function Read-LegacyOwnership(
    [string]$ResolvedLegacyRoot,
    [string]$ResolvedDurableHome,
    [string]$CurrentMarketplaceId,
    [string]$CurrentPluginId,
    $CurrentEnvironment
) {
    $legacy = [ordered]@{
        root = $ResolvedLegacyRoot
        probe = $null
        tombstone = $null
        disposition = 'active'
        ownerMarketplaceId = $null
        classification = 'none'
    }

    $tombstoneEntry = Join-Path $ResolvedLegacyRoot '.installation-ownership.json'
    $tombstonePath = Canonical-Path $tombstoneEntry
    $tombstoneItem = Get-Item -LiteralPath $tombstoneEntry -Force -ErrorAction SilentlyContinue
    if ($null -eq $tombstoneItem) {
        return [pscustomobject]$legacy
    }
    $legacy.tombstone = $tombstonePath
    $tombstoneLinkType = Get-PropertyValue $tombstoneItem 'LinkType'
    if ($tombstoneItem.PSIsContainer -or $tombstoneLinkType) {
        $legacy.disposition = 'orphaned-transfer'
        $legacy.classification = 'orphaned-transfer'
        return [pscustomobject]$legacy
    }

    try {
        $tombstone = Read-Json $tombstonePath
        Assert-JsonObject $tombstone '.installation-ownership.json'
        $version = Get-PropertyValue $tombstone 'version'
        Assert-PositiveInteger $version '.installation-ownership.json version'
        if ((Get-StringProperty $tombstone 'schema') -cne 'copilot-extensions.legacy-installation-ownership' -or
            $version -ne 1) {
            Fail '.installation-ownership.json has an unsupported schema or version.'
        }
        $ownerMarketplaceId = Get-StringProperty $tombstone 'marketplaceId'
        Assert-MarketplaceId $ownerMarketplaceId
        $legacy.ownerMarketplaceId = $ownerMarketplaceId
        $ownerPluginId = Get-StringProperty $tombstone 'pluginId'
        Assert-PluginId $ownerPluginId
        if ($CurrentPluginId -and $ownerPluginId -cne $CurrentPluginId) {
            Fail '.installation-ownership.json pluginId does not match the current plugin.'
        }
        if (-not $CurrentPluginId) { $CurrentPluginId = $ownerPluginId }
        $environment = Read-EnvironmentObject (Get-PropertyValue $tombstone 'environment') '.installation-ownership.json environment'
        if (-not (Test-SameEnvironment $environment $CurrentEnvironment)) {
            $legacy.disposition = 'orphaned-transfer'
            $legacy.classification = 'foreign-environment'
            return [pscustomobject]$legacy
        }
        $activation = Get-PropertyValue $tombstone 'activation'
        Assert-JsonObject $activation '.installation-ownership.json activation'
        $activationPath = Get-StringProperty $activation 'path'
        if (-not [IO.Path]::IsPathRooted($activationPath)) {
            Fail '.installation-ownership.json activation.path must be absolute.'
        }
        $activationGeneration = Get-PropertyValue $activation 'generation'
        Assert-ReceiptGeneration $activationGeneration '.installation-ownership.json activation.generation'
        [void](Read-ExactUtcTimestampValue (Get-PropertyValue $tombstone 'transferredAt') '.installation-ownership.json transferredAt')
        $expectedActivationPath = Canonical-Path (
            Join-Path (
                Join-Path (
                    Join-Path (
                        Join-Path $ResolvedDurableHome 'marketplaces'
                    ) $ownerMarketplaceId
                ) 'plugins'
            ) (Join-Path $CurrentPluginId 'installation-activation.json')
        )
        if (-not (Paths-Equal $activationPath $expectedActivationPath)) {
            Fail '.installation-ownership.json activation.path is not canonical.'
        }
        $validatedActivation = Validate-ActivationReceipt $activationPath $ResolvedDurableHome $ownerMarketplaceId $CurrentPluginId $ResolvedLegacyRoot $CurrentEnvironment
        if ($validatedActivation.classification -ceq 'foreign-environment') {
            $legacy.disposition = 'orphaned-transfer'
            $legacy.classification = 'orphaned-transfer'
            return [pscustomobject]$legacy
        }
        if ($validatedActivation.classification -cne 'valid' -or
            $validatedActivation.mode -cne 'namespaced' -or
            [long]$validatedActivation.activationGeneration -ne [long]$activationGeneration) {
            Fail '.installation-ownership.json points to a stale or invalid activation.'
        }
        if ($CurrentMarketplaceId -and $ownerMarketplaceId -ceq $CurrentMarketplaceId) {
            $legacy.disposition = 'owned-by-current-cell'
        }
        else {
            $legacy.disposition = 'owned-by-other-cell'
        }
        $legacy.classification = 'valid'
        return [pscustomobject]$legacy
    }
    catch {
        $legacy.disposition = 'orphaned-transfer'
        if ($legacy.classification -cne 'foreign-environment') {
            $legacy.classification = 'orphaned-transfer'
        }
        return [pscustomobject]$legacy
    }
}

function Resolve-InstallationStatus(
    [string]$ResolvedCopilotHome,
    [string]$ResolvedProjectRoot,
    [string]$ResolvedDurableHome
) {
    if (-not $LegacyRoot) {
        Fail "$Action requires -LegacyRoot."
    }
    if (-not [IO.Path]::IsPathRooted($LegacyRoot)) {
        Fail '-LegacyRoot must be absolute.'
    }

    $currentEnvironment = Get-CurrentEnvironment
    $resolvedLegacyRoot = Canonical-Path $LegacyRoot
    $legacyProbe = Read-LegacyProbe
    $target = Resolve-StatusTarget $ResolvedCopilotHome $ResolvedProjectRoot $ResolvedDurableHome
    $policy = Read-PolicyDecision $currentEnvironment $target.marketplaceId $target.pluginId

    $legacy = Read-LegacyOwnership $resolvedLegacyRoot $ResolvedDurableHome $target.marketplaceId $target.pluginId $currentEnvironment
    $legacy.probe = $legacyProbe

    $maintenance = Read-EffectiveMaintenance $currentEnvironment $target.pluginRoot
    $activationPath = $null
    if ($target.pluginRoot) {
        $activationPath = Canonical-Path (Join-Path $target.pluginRoot 'installation-activation.json')
    }
    $activation = Validate-ActivationReceipt $activationPath $ResolvedDurableHome $target.marketplaceId $target.pluginId $resolvedLegacyRoot $currentEnvironment

    $desiredMode = $null
    if (-not $policy.authoritative -and
        @('valid', 'revalidation-required') -ccontains $activation.classification -and
        $activation.mode -ceq 'namespaced') {
        $desiredMode = 'namespaced'
    }
    elseif (($policy.state -ceq 'valid' -or $policy.state -ceq 'missing') -and
            $policy.enabled -and $policy.authoritative) {
        $desiredMode = 'namespaced'
    }
    elseif ($policy.state -ceq 'valid' -or $policy.state -ceq 'missing') {
        $desiredMode = 'legacy'
    }

    $actualMode = 'legacy'
    $runtimeRoot = $resolvedLegacyRoot
    $context = $null
    $activationPointer = $null
    $activationGeneration = $null
    $installGeneration = $null
    if ($activation.present) {
        $activationPointer = $activation.path
        $activationGeneration = $activation.activationGeneration
    }
    if ($activation.classification -ceq 'valid') {
        $actualMode = $activation.mode
        $runtimeRoot = $activation.runtimeRoot
        $context = $activation.context
        $installGeneration = $activation.installGeneration
    }
    elseif ($activation.classification -ceq 'revalidation-required') {
        $actualMode = $activation.mode
        $runtimeRoot = $activation.runtimeRoot
        $context = $activation.context
        $installGeneration = $activation.installGeneration
    }
    elseif ($activation.classification -ceq 'foreign-environment' -or
            $activation.classification -ceq 'invalid') {
        $actualMode = $null
        $runtimeRoot = $null
        $context = $null
        $installGeneration = $null
    }
    if ($target.provenanceBlocked -or $target.invalidContext) {
        $desiredMode = $null
        $actualMode = $null
        $runtimeRoot = $null
        $context = $null
        $activationPointer = $null
        $activationGeneration = $null
        $installGeneration = $null
    }

    $status = 'ready'
    $reason = ''
    if ($policy.state -ceq 'invalid' -or $policy.state -ceq 'unsupported' -or $target.invalidContext -or $activation.classification -ceq 'invalid') {
        $status = 'invalid'
        if ($policy.state -ceq 'invalid' -or $policy.state -ceq 'unsupported') {
            $reason = $policy.reason
        }
        elseif ($target.invalidContext) {
            $reason = 'context-invalid'
        }
        elseif ($activation.classification -ceq 'invalid') {
            $reason = 'activation-invalid'
        }
        else {
            $reason = 'invalid'
        }
    }
    elseif ($maintenance.scope -cne 'none') {
        $status = 'maintenance-blocked'
        $reason = $maintenance.reason
    }
    elseif ($activation.classification -ceq 'foreign-environment' -or $legacy.classification -ceq 'foreign-environment') {
        $status = 'foreign-environment'
        $reason = 'foreign-environment'
    }
    elseif ($legacy.disposition -ceq 'orphaned-transfer') {
        $status = 'orphaned-transfer'
        $reason = 'orphaned-transfer'
    }
    elseif ($activation.classification -ceq 'revalidation-required') {
        $status = 'revalidation-required'
        $reason = 'revalidation-required'
    }
    elseif ($activation.classification -ceq 'valid' -and $activation.mode -ceq 'namespaced' -and $desiredMode -ceq 'legacy') {
        $status = 'deactivation-required'
        $reason = 'deactivation-required'
    }
    elseif ($target.provenanceBlocked) {
        $status = 'provenance-blocked'
        $reason = 'provenance-blocked'
    }
    elseif ($desiredMode -ceq 'namespaced' -and -not ($activation.classification -ceq 'valid' -and $activation.mode -ceq 'namespaced')) {
        if ($activation.classification -ceq 'absent' -and
            $legacyProbe.declared -and $legacyProbe.result -ceq 'absent') {
            $status = 'ready'
            $reason = 'activation-required'
        }
        else {
            $status = 'migration-required'
            $reason = 'migration-required'
        }
    }
    elseif ($activation.classification -ceq 'valid' -and $activation.mode -ceq 'namespaced') {
        $status = 'ready'
        $reason = 'namespaced-active'
    }
    else {
        $status = 'ready'
        $reason = $policy.reason
    }

    $result = [ordered]@{
        schema = 'copilot-extensions.installation-resolution'
        version = 1
        marketplaceId = $target.marketplaceId
        pluginId = $target.pluginId
        environment = [pscustomobject][ordered]@{
            platform = $currentEnvironment.platform
            homeRealPath = $currentEnvironment.homeRealPath
            wslDistro = $currentEnvironment.wslDistro
        }
        desiredMode = $desiredMode
        actualMode = $actualMode
        status = $status
        maintenance = [pscustomobject][ordered]@{
            state = $maintenance.state
            scope = $maintenance.scope
            marker = $maintenance.marker
            sidecar = $maintenance.sidecar
        }
        runtimeRoot = $runtimeRoot
        context = $context
        activation = $activationPointer
        activationGeneration = $activationGeneration
        installGeneration = $installGeneration
        reason = $reason
        policy = [pscustomobject][ordered]@{
            path = $policy.path
            authoritative = $policy.authoritative
            state = $policy.state
            scope = $policy.scope
            enabled = $policy.enabled
            reason = $policy.reason
        }
        legacy = [pscustomobject][ordered]@{
            root = $legacy.root
            probe = $legacy.probe
            tombstone = $legacy.tombstone
            disposition = $legacy.disposition
            ownerMarketplaceId = $legacy.ownerMarketplaceId
        }
    }

    if ($Action -ceq 'probe-legacy') {
        $allowMutation = $false
        $probeReason = $reason
        if ($status -ceq 'ready' -and $actualMode -ceq 'namespaced') {
            $probeReason = 'namespaced-active'
        }
        elseif ($legacy.disposition -ceq 'owned-by-current-cell' -or
                $legacy.disposition -ceq 'owned-by-other-cell') {
            $probeReason = 'legacy-owned-by-other-cell'
        }
        elseif ($status -ceq 'ready' -and $reason -ceq 'activation-required') {
            $probeReason = 'namespaced-requested'
        }
        elseif ($status -ceq 'migration-required') {
            $allowMutation = $true
            $probeReason = 'migration-required'
        }
        elseif ($status -ceq 'ready' -and $actualMode -ceq 'legacy') {
            $allowMutation = $true
            $probeReason = 'legacy-active'
        }
        $result.allowMutation = $allowMutation
        $result.probeReason = $probeReason
    }

    return [pscustomobject]$result
}

function Assert-ExpectedGeneration(
    [long]$Actual,
    [long]$Expected,
    [string]$ReceiptName
) {
    if ($Expected -lt 0) {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    if ($Actual -ne $Expected) {
        Fail "$ReceiptName generation changed: expected $Expected, found $Actual; restart installation-context resolution."
    }
}

function ConvertTo-ExpectedGeneration($Value, [string]$ReceiptName) {
    if ($null -eq $Value) {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    $text = [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
    if ($text -notmatch '\A[0-9]+\z') {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    [long]$parsed = 0
    if (-not [long]::TryParse(
        $text,
        [Globalization.NumberStyles]::None,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed
    )) {
        Fail "Expected $ReceiptName generation exceeds the portable signed 64-bit maximum."
    }
    return $parsed
}

function Invoke-ActivationCas([string]$ResolvedDurableHome) {
    if (-not $Context) { Fail 'activation-cas requires -Context.' }
    if (-not $ExpectedMarketplaceId) {
        Fail 'activation-cas requires -ExpectedMarketplaceId.'
    }
    if (-not $ExpectedPluginId) {
        Fail 'activation-cas requires -ExpectedPluginId.'
    }
    Assert-MarketplaceId $ExpectedMarketplaceId
    Assert-PluginId $ExpectedPluginId
    if ($ExpectedNamespaceGeneration -lt 0) {
        Fail 'activation-cas requires -ExpectedNamespaceGeneration.'
    }
    if ($ExpectedInstallGeneration -lt 0) {
        Fail 'activation-cas requires -ExpectedInstallGeneration.'
    }
    if ($ExpectedActivationGeneration -lt 0) {
        Fail 'activation-cas requires -ExpectedActivationGeneration.'
    }
    if (($ActivationMode -ceq 'namespaced' -and $ActivationState -ceq 'active') -or
        ($ActivationMode -ceq 'legacy' -and $ActivationState -ceq 'deactivated')) {
    }
    else {
        Fail 'Activation mode/state pair is invalid.'
    }
    if ($LegacyDisposition -cnotin @('absent', 'quiesced', 'retained-inert', 'restored')) {
        Fail 'Activation legacy disposition is invalid.'
    }
    if (-not $LegacyProbeJson -and -not $LegacyProbeFile) {
        Fail 'activation-cas requires -LegacyProbeJson or -LegacyProbeFile.'
    }
    $recordedProbe = Read-LegacyProbe
    $currentEnvironment = Get-CurrentEnvironment
    if (-not (Test-EnvironmentPathRooted $Context $currentEnvironment.platform)) {
        Fail 'Activation context must be absolute.'
    }
    $contextPath = Canonical-Path $Context -MustExist
    $cellRoot = Canonical-Path (Join-Path (Join-Path $ResolvedDurableHome 'marketplaces') $ExpectedMarketplaceId)
    $pluginRoot = Canonical-Path (Join-Path (Join-Path $cellRoot 'plugins') $ExpectedPluginId)
    $installPath = Canonical-Path (Join-Path $pluginRoot 'install.json')
    if (-not (Paths-Equal $contextPath $installPath)) {
        Fail 'Activation context is not the canonical install receipt.'
    }
    $resolvedLegacyRoot = $LegacyRoot
    if (-not $resolvedLegacyRoot) {
        $resolvedLegacyRoot = Join-Path $currentEnvironment.homeRealPath ('.' + $ExpectedPluginId)
    }
    if (-not (Test-EnvironmentPathRooted $resolvedLegacyRoot $currentEnvironment.platform)) {
        Fail '-LegacyRoot must be absolute.'
    }
    $resolvedLegacyRoot = Canonical-Path $resolvedLegacyRoot
    $activationPath = Join-Path $pluginRoot 'installation-activation.json'
    $genesisLock = Join-Path (Join-Path $ResolvedDurableHome 'marketplaces/.locks') ($ExpectedMarketplaceId + '.genesis')
    $installLock = Join-Path (Join-Path $cellRoot '.locks') ($ExpectedPluginId + '.install.lock')
    $startingLockCount = $script:HeldLocks.Count
    $operationFailed = $false

    try {
        Acquire-Lock $genesisLock 'genesis' $ExpectedMarketplaceId
        Acquire-Lock $installLock 'install' $ExpectedMarketplaceId $ExpectedPluginId
        $validated = Invoke-WithoutPluginRoot {
            Validate-ContextReceipt $contextPath $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' $cellRoot
        }
        $actualNamespaceGeneration = [long]$validated.namespaceGeneration
        $actualInstallGeneration = [long]$validated.generation
        $activation = Validate-ActivationReceipt `
            $activationPath `
            $ResolvedDurableHome `
            $ExpectedMarketplaceId `
            $ExpectedPluginId `
            $resolvedLegacyRoot `
            $currentEnvironment
        $actualActivationGeneration = 0
        if ($activation.classification -ceq 'foreign-environment') {
            Fail 'Existing activation receipt belongs to a foreign environment.'
        }
        if ($activation.classification -ceq 'invalid') {
            Fail 'Existing activation receipt is invalid.'
        }
        if ($activation.classification -cin @('valid', 'revalidation-required')) {
            $actualActivationGeneration = [long]$activation.activationGeneration
        }

        if ($actualNamespaceGeneration -ne $ExpectedNamespaceGeneration -or
            $actualInstallGeneration -ne $ExpectedInstallGeneration -or
            $actualActivationGeneration -ne $ExpectedActivationGeneration) {
            return [pscustomobject][ordered]@{
                action = 'activation-cas'
                status = 'revalidation-required'
                reason = 'generation-changed'
                activation = $(if ($activation.present) { $activation.path } else { $null })
                activationChanged = $false
                activationGeneration = $actualActivationGeneration
                namespaceGeneration = $actualNamespaceGeneration
                installGeneration = $actualInstallGeneration
                expectedActivationGeneration = $ExpectedActivationGeneration
                expectedNamespaceGeneration = $ExpectedNamespaceGeneration
                expectedInstallGeneration = $ExpectedInstallGeneration
                operative = $false
            }
        }
        $namespaceReceipt = Read-Json $validated.namespaceReceipt
        if ((Get-StringProperty $namespaceReceipt 'state') -cne 'active' -or
            (Get-StringProperty $validated 'state') -cne 'active') {
            Fail 'Activation requires active namespace and install receipts.'
        }
        if ($actualActivationGeneration -eq [int64]::MaxValue) {
            Fail 'installation-activation.json generation cannot be incremented; explicit repair is required.'
        }

        $now = Get-UtcTimestamp
        $createdAt = $now
        if ($activation.present) {
            $existing = Read-Json $activation.path
            $createdAt = Get-ReceiptTimestamp $existing 'createdAt' $now
        }
        $nextGeneration = $actualActivationGeneration + 1
        $receipt = [ordered]@{
            schema = 'copilot-extensions.installation-activation'
            version = 1
            marketplaceId = $ExpectedMarketplaceId
            pluginId = $ExpectedPluginId
            mode = $ActivationMode
            state = $ActivationState
            environment = $currentEnvironment
            context = $contextPath
            namespaceGeneration = $actualNamespaceGeneration
            installGeneration = $actualInstallGeneration
            generation = $nextGeneration
            legacy = [ordered]@{
                disposition = $LegacyDisposition
                probe = $recordedProbe
            }
            createdAt = $createdAt
            updatedAt = $now
        }
        Assert-AllLocksOwned
        Write-AtomicJson $activationPath $receipt
        $published = Validate-ActivationReceipt `
            $activationPath `
            $ResolvedDurableHome `
            $ExpectedMarketplaceId `
            $ExpectedPluginId `
            $resolvedLegacyRoot `
            $currentEnvironment
        if ($published.classification -cne 'valid') {
            Fail 'Published activation receipt did not validate as current.'
        }
        return [pscustomobject][ordered]@{
            action = 'activation-cas'
            status = 'ready'
            reason = 'activation-published'
            activation = $published.path
            activationChanged = $true
            activationGeneration = [long]$published.activationGeneration
            namespaceGeneration = $actualNamespaceGeneration
            installGeneration = $actualInstallGeneration
            environment = $currentEnvironment
            mode = $ActivationMode
            state = $ActivationState
            context = $contextPath
            operative = $false
        }
    }
    catch {
        $operationFailed = $true
        throw
    }
    finally {
        $releaseError = $null
        while ($script:HeldLocks.Count -gt $startingLockCount) {
            try {
                Release-Lock
            }
            catch {
                Pop-HeldLock
                if ($null -eq $releaseError) {
                    $releaseError = $_.Exception
                }
                if ($operationFailed) {
                    [Console]::Error.WriteLine(
                        "installation-context: $($_.Exception.Message) while preserving the original activation failure."
                    )
                }
            }
        }
        if (-not $operationFailed -and $null -ne $releaseError) {
            throw $releaseError
        }
    }
}

function Get-PayloadIdentity([string]$InstallPath) {
    $install = Read-Json $InstallPath
    $payload = Get-PropertyValue $install 'payload'
    $root = Get-StringProperty $payload 'root'
    if (-not [IO.Path]::IsPathRooted($root)) { Fail 'payload.root must be absolute.' }
    $root = Canonical-Path $root
    $version = Get-StringProperty $payload 'version'
    if ([string]::IsNullOrWhiteSpace($version)) {
        Fail 'payload.version must be a non-empty string.'
    }
    $origin = Get-StringProperty $payload 'origin'
    if ($origin -cnotin @('installed', 'directory', 'staged', 'explicit')) {
        Fail 'payload.origin must be installed, directory, staged, or explicit.'
    }
    $originReceipt = Get-PropertyValue $payload 'originReceipt'
    if ($null -ne $originReceipt) {
        if ($originReceipt -isnot [string]) {
            Fail 'payload.originReceipt must be a string.'
        }
        if (-not [IO.Path]::IsPathRooted($originReceipt)) {
            Fail 'payload.originReceipt must be absolute.'
        }
        $originReceipt = Canonical-Path $originReceipt
    }
    return [pscustomobject][ordered]@{
        root = $root
        version = $version
        origin = $origin
        originReceipt = $originReceipt
    }
}

function Assert-NotReparsePoint([string]$Path, [string]$Label) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -ne $item -and
        (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        Fail "$Label may not be a symbolic link or reparse point."
    }
}

function Resolve-SnapshotPaths($Validated, [string]$RequestedSnapshotId) {
    Assert-SnapshotId $RequestedSnapshotId
    $snapshotsRoot = Canonical-Path ([string]$Validated.snapshotsRoot)
    $lexicalRoot = Join-Path $snapshotsRoot $RequestedSnapshotId
    Assert-NotReparsePoint $lexicalRoot 'Snapshot root'
    if (-not (Test-Path -LiteralPath $lexicalRoot -PathType Container)) {
        Fail 'Snapshot root must be an existing materialized directory.'
    }
    $snapshotRoot = Canonical-Path $lexicalRoot -MustExist
    if (-not (Paths-Equal (Split-Path -Parent $snapshotRoot) $snapshotsRoot)) {
        Fail 'Snapshot root must be one direct child of snapshotsRoot.'
    }
    $nameComparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') {
        $nameComparison = [StringComparison]::OrdinalIgnoreCase
    }
    if (-not [string]::Equals(
        (Split-Path -Leaf $snapshotRoot),
        $RequestedSnapshotId,
        $nameComparison
    )) {
        Fail 'Snapshot root does not retain the requested snapshot id.'
    }
    $materialized = @(
        Get-ChildItem -LiteralPath $snapshotRoot -Force |
            Where-Object { $_.Name -cne 'snapshot-provenance.json' } |
            Select-Object -First 1
    )
    if ($materialized.Count -eq 0) {
        Fail 'Snapshot root must contain materialized payload content.'
    }
    $lexicalProvenance = Join-Path $snapshotRoot 'snapshot-provenance.json'
    Assert-NotReparsePoint $lexicalProvenance 'Snapshot provenance'
    $provenance = Canonical-Path $lexicalProvenance
    if (-not (Path-IsWithin $provenance $snapshotsRoot)) {
        Fail 'Snapshot provenance path escapes snapshotsRoot.'
    }
    return [pscustomobject][ordered]@{
        snapshotRoot = $snapshotRoot
        provenance = $provenance
    }
}

function Validate-SnapshotProvenance(
    [string]$ContextPath,
    [string]$ResolvedDurableHome,
    [string]$MarketplaceExpectation,
    [string]$PluginExpectation,
    [string]$RequestedSnapshotId,
    [bool]$RequireCurrentReceipts = $true,
    [string]$PayloadRootExpectation = '',
    [string]$PayloadVersionExpectation = ''
) {
    if (-not $ContextPath) { Fail 'snapshot-validate requires -Context.' }
    if (-not $MarketplaceExpectation) {
        Fail 'snapshot-validate requires -ExpectedMarketplaceId.'
    }
    if (-not $PluginExpectation) {
        Fail 'snapshot-validate requires -ExpectedPluginId.'
    }
    if (-not $RequestedSnapshotId) { Fail 'snapshot-validate requires -SnapshotId.' }
    Assert-MarketplaceId $MarketplaceExpectation
    Assert-PluginId $PluginExpectation
    Assert-SnapshotId $RequestedSnapshotId
    if ($PayloadRootExpectation) {
        if (-not (Test-FullyQualifiedPath $PayloadRootExpectation)) {
            Fail 'Expected snapshot payload root must be absolute.'
        }
        $PayloadRootExpectation = Canonical-Path $PayloadRootExpectation
    }
    if ($PayloadVersionExpectation -and
        [string]::IsNullOrWhiteSpace($PayloadVersionExpectation)) {
        Fail 'Expected snapshot payload version must be a non-empty string.'
    }
    $validated = Invoke-WithoutPluginRoot {
        Validate-ContextReceipt $ContextPath $ResolvedDurableHome $MarketplaceExpectation $PluginExpectation '' ''
    }
    $paths = Resolve-SnapshotPaths $validated $RequestedSnapshotId
    $actualProvenance = Canonical-Path $paths.provenance -MustExist
    if (-not (Paths-Equal $actualProvenance $paths.provenance)) {
        Fail "Snapshot provenance is not at its exact canonical location '$($paths.provenance)'."
    }
    $provenance = Read-Json $actualProvenance
    if ($null -eq $provenance -or $provenance -isnot [pscustomobject]) {
        Fail 'Snapshot provenance must be a JSON object.'
    }
    $provenanceVersion = Get-PropertyValue $provenance 'version'
    Assert-PositiveInteger $provenanceVersion 'snapshot provenance version'
    if ((Get-StringProperty $provenance 'schema') -cne
            'copilot-extensions.snapshot-provenance' -or
        $provenanceVersion -ne 1) {
        Fail 'Snapshot provenance has an unsupported schema or version.'
    }
    $marketplaceId = Get-StringProperty $provenance 'marketplaceId'
    $receiptPluginId = Get-StringProperty $provenance 'pluginId'
    if ($marketplaceId -cne $MarketplaceExpectation) {
        Fail "Expected marketplace '$MarketplaceExpectation', snapshot provenance names '$marketplaceId'."
    }
    if ($receiptPluginId -cne $PluginExpectation) {
        Fail "Expected plugin '$PluginExpectation', snapshot provenance names '$receiptPluginId'."
    }

    $source = Get-PropertyValue $provenance 'source'
    if ($null -eq $source -or $source -isnot [pscustomobject]) {
        Fail 'Snapshot provenance source is missing.'
    }
    $normalized = Normalize-Source ([pscustomobject][ordered]@{
        kind = Get-StringProperty $source 'kind'
        canonical = Get-StringProperty $source 'canonical'
        ref = Get-StringProperty $source 'ref'
    }) '' -FromReceipt
    $readableName = $marketplaceId.Substring(0, $marketplaceId.LastIndexOf('--'))
    $identity = Source-Identity $normalized $readableName
    $fingerprint = Get-StringProperty $source 'fingerprint'
    if ($identity.marketplaceId -cne $marketplaceId) {
        Fail 'Snapshot provenance marketplaceId does not match its normalized source.'
    }
    if ($identity.fingerprint -cne $fingerprint) {
        Fail 'Snapshot provenance fingerprint does not match its normalized source.'
    }
    if ($fingerprint -cne $validated.sourceFingerprint -or
        $normalized.kind -cne $validated.source.kind -or
        $normalized.canonical -cne $validated.source.canonical -or
        $normalized.ref -cne $validated.source.ref) {
        Fail 'Snapshot provenance source does not match the canonical namespace receipt.'
    }

    $snapshot = Get-PropertyValue $provenance 'snapshot'
    if ($null -eq $snapshot -or $snapshot -isnot [pscustomobject]) {
        Fail 'Snapshot provenance snapshot identity is missing.'
    }
    if ((Get-StringProperty $snapshot 'id') -cne $RequestedSnapshotId) {
        Fail 'Snapshot provenance id does not match its canonical snapshot directory.'
    }
    $recordedSnapshotRoot = Get-StringProperty $snapshot 'root'
    if (-not [IO.Path]::IsPathRooted($recordedSnapshotRoot)) {
        Fail 'Snapshot provenance snapshot.root must be absolute.'
    }
    if (-not (Paths-Equal $recordedSnapshotRoot $paths.snapshotRoot)) {
        Fail 'Snapshot provenance snapshot.root is not its exact canonical location.'
    }

    $namespaceReference = Get-PropertyValue $provenance 'namespaceReceipt'
    $installReference = Get-PropertyValue $provenance 'installReceipt'
    if ($null -eq $namespaceReference -or
        $namespaceReference -isnot [pscustomobject] -or
        $null -eq $installReference -or
        $installReference -isnot [pscustomobject]) {
        Fail 'Snapshot provenance receipt references are missing.'
    }
    $namespacePath = Get-StringProperty $namespaceReference 'path'
    $installPath = Get-StringProperty $installReference 'path'
    if (-not [IO.Path]::IsPathRooted($namespacePath) -or
        -not [IO.Path]::IsPathRooted($installPath)) {
        Fail 'Snapshot provenance receipt paths must be absolute.'
    }
    if (-not (Paths-Equal $namespacePath $validated.namespaceReceipt)) {
        Fail 'Snapshot provenance namespace receipt does not match the current context.'
    }
    if (-not (Paths-Equal $installPath $validated.installReceipt)) {
        Fail 'Snapshot provenance install receipt does not match the current context.'
    }
    $namespaceGeneration = Get-PropertyValue $namespaceReference 'generation'
    $installGeneration = Get-PropertyValue $installReference 'generation'
    Assert-ReceiptGeneration $namespaceGeneration 'snapshot provenance namespace generation'
    Assert-ReceiptGeneration $installGeneration 'snapshot provenance install generation'
    if ($RequireCurrentReceipts) {
        if ([long]$namespaceGeneration -ne [long]$validated.namespaceGeneration) {
            Fail 'Snapshot provenance namespace generation is stale; restart snapshot production.'
        }
        if ([long]$installGeneration -ne [long]$validated.generation) {
            Fail 'Snapshot provenance install generation is stale; restart snapshot production.'
        }
    }
    elseif ([long]$validated.namespaceGeneration -lt [long]$namespaceGeneration -or
        [long]$validated.generation -lt [long]$installGeneration) {
        Fail 'Current receipt generation predates the owned runtime slot.'
    }
    $namespace = Read-Json $validated.namespaceReceipt
    if ($RequireCurrentReceipts -and
        ((Get-StringProperty $namespace 'state') -cne 'active' -or
         (Get-StringProperty $validated 'state') -cne 'active')) {
        Fail 'Snapshot provenance requires active namespace and install receipts.'
    }

    $payload = Get-PropertyValue $provenance 'payload'
    if ($null -eq $payload -or $payload -isnot [pscustomobject]) {
        Fail 'Snapshot provenance payload identity is missing.'
    }
    if (-not (Has-ExactProperty $payload 'originReceipt')) {
        Fail 'Snapshot provenance payload.originReceipt must be present.'
    }
    $payloadRoot = Get-StringProperty $payload 'root'
    if (-not [IO.Path]::IsPathRooted($payloadRoot)) {
        Fail 'Snapshot provenance payload.root must be absolute.'
    }
    $payloadRoot = Canonical-Path $payloadRoot
    $payloadVersion = Get-StringProperty $payload 'version'
    if ([string]::IsNullOrWhiteSpace($payloadVersion)) {
        Fail 'Snapshot provenance payload.version must be a non-empty string.'
    }
    $payloadOrigin = Get-StringProperty $payload 'origin'
    if ($payloadOrigin -cnotin @('installed', 'directory', 'staged', 'explicit')) {
        Fail 'Snapshot provenance payload.origin is invalid.'
    }
    $payloadOriginReceipt = Get-PropertyValue $payload 'originReceipt'
    if ($null -ne $payloadOriginReceipt) {
        if ($payloadOriginReceipt -isnot [string]) {
            Fail 'Snapshot provenance payload.originReceipt must be a string or null.'
        }
        if (-not [IO.Path]::IsPathRooted($payloadOriginReceipt)) {
            Fail 'Snapshot provenance payload.originReceipt must be absolute.'
        }
        $payloadOriginReceipt = Canonical-Path $payloadOriginReceipt
    }
    $recordedPayload = [pscustomobject][ordered]@{
        root = $payloadRoot
        version = $payloadVersion
        origin = $payloadOrigin
        originReceipt = $payloadOriginReceipt
    }
    if ($PayloadRootExpectation -and
        -not (Paths-Equal $payloadRoot $PayloadRootExpectation)) {
        Fail "Expected snapshot payload root '$PayloadRootExpectation', provenance names '$payloadRoot'."
    }
    if ($PayloadVersionExpectation -and
        $payloadVersion -cne $PayloadVersionExpectation) {
        Fail "Expected snapshot payload version '$PayloadVersionExpectation', provenance names '$payloadVersion'."
    }
    $resultPayload = $recordedPayload
    if ($RequireCurrentReceipts) {
        $currentPayload = Get-PayloadIdentity $validated.installReceipt
        if (-not (Paths-Equal $payloadRoot $currentPayload.root) -or
            $payloadVersion -cne $currentPayload.version -or
            $payloadOrigin -cne $currentPayload.origin -or
            $payloadOriginReceipt -cne $currentPayload.originReceipt) {
            Fail 'Snapshot provenance payload does not match the pinned install receipt.'
        }
        $resultPayload = $currentPayload
    }
    [void](Read-ExactUtcTimestampValue (
        Get-PropertyValue $provenance 'createdAt'
    ) 'snapshot provenance createdAt')

    return [pscustomobject][ordered]@{
        action = 'snapshot-validate'
        status = 'ready'
        reason = 'snapshot-provenance-valid'
        provenance = $actualProvenance
        snapshotRoot = $paths.snapshotRoot
        snapshotId = $RequestedSnapshotId
        marketplaceId = $marketplaceId
        pluginId = $receiptPluginId
        sourceFingerprint = $fingerprint
        namespaceReceipt = Canonical-Path $namespacePath
        installReceipt = Canonical-Path $installPath
        namespaceGeneration = [long]$namespaceGeneration
        installGeneration = [long]$installGeneration
        payload = $resultPayload
        operative = $false
    }
}

function Invoke-SnapshotStamp([string]$ResolvedDurableHome) {
    if (-not $Context) { Fail 'snapshot-stamp requires -Context.' }
    if (-not $ExpectedMarketplaceId) {
        Fail 'snapshot-stamp requires -ExpectedMarketplaceId.'
    }
    if (-not $ExpectedPluginId) {
        Fail 'snapshot-stamp requires -ExpectedPluginId.'
    }
    if (-not $SnapshotId) { Fail 'snapshot-stamp requires -SnapshotId.' }
    Assert-MarketplaceId $ExpectedMarketplaceId
    Assert-PluginId $ExpectedPluginId
    Assert-SnapshotId $SnapshotId
    if ($ExpectedNamespaceGeneration -lt 0) {
        Fail 'snapshot-stamp requires -ExpectedNamespaceGeneration.'
    }
    if ($ExpectedInstallGeneration -lt 0) {
        Fail 'snapshot-stamp requires -ExpectedInstallGeneration.'
    }
    $validated = Invoke-WithoutPluginRoot {
        Validate-ContextReceipt $Context $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' ''
    }
    $cellRoot = [string]$validated.cellRoot
    $genesisLock = Join-Path (Join-Path $ResolvedDurableHome 'marketplaces/.locks') ($ExpectedMarketplaceId + '.genesis')
    $installLock = Join-Path (Join-Path $cellRoot '.locks') ($ExpectedPluginId + '.install.lock')
    $startingLockCount = $script:HeldLocks.Count
    $operationFailed = $false
    try {
        Acquire-Lock $genesisLock 'genesis' $ExpectedMarketplaceId
        Acquire-Lock $installLock 'install' $ExpectedMarketplaceId $ExpectedPluginId
        $validated = Invoke-WithoutPluginRoot {
            Validate-ContextReceipt $validated.installReceipt $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' $cellRoot
        }
        Assert-ExpectedGeneration ([long]$validated.namespaceGeneration) $ExpectedNamespaceGeneration 'namespace.json'
        Assert-ExpectedGeneration ([long]$validated.generation) $ExpectedInstallGeneration 'install.json'
        $namespace = Read-Json $validated.namespaceReceipt
        if ((Get-StringProperty $namespace 'state') -cne 'active' -or
            (Get-StringProperty $validated 'state') -cne 'active') {
            Fail 'Snapshot provenance requires active namespace and install receipts.'
        }
        $paths = Resolve-SnapshotPaths $validated $SnapshotId
        $snapshotChanged = $false
        $existingEntry = Get-Item -LiteralPath $paths.provenance -Force -ErrorAction SilentlyContinue
        if ($null -ne $existingEntry) {
            $published = Validate-SnapshotProvenance `
                $validated.installReceipt `
                $ResolvedDurableHome `
                $ExpectedMarketplaceId `
                $ExpectedPluginId `
                $SnapshotId
        }
        else {
            $payload = Get-PayloadIdentity $validated.installReceipt
            $receipt = [ordered]@{
                schema = 'copilot-extensions.snapshot-provenance'
                version = 1
                marketplaceId = $ExpectedMarketplaceId
                pluginId = $ExpectedPluginId
                source = [ordered]@{
                    kind = [string]$validated.source.kind
                    canonical = [string]$validated.source.canonical
                    ref = [string]$validated.source.ref
                    fingerprint = [string]$validated.sourceFingerprint
                }
                snapshot = [ordered]@{
                    id = $SnapshotId
                    root = [string]$paths.snapshotRoot
                }
                payload = $payload
                namespaceReceipt = [ordered]@{
                    path = [string]$validated.namespaceReceipt
                    generation = [long]$validated.namespaceGeneration
                }
                installReceipt = [ordered]@{
                    path = [string]$validated.installReceipt
                    generation = [long]$validated.generation
                }
                createdAt = Get-UtcTimestamp
            }
            Assert-AllLocksOwned
            Write-AtomicJson $paths.provenance $receipt
            $snapshotChanged = $true
            $published = Validate-SnapshotProvenance `
                $validated.installReceipt `
                $ResolvedDurableHome `
                $ExpectedMarketplaceId `
                $ExpectedPluginId `
                $SnapshotId
        }
        $published.action = 'snapshot-stamp'
        $published.reason = $(if ($snapshotChanged) {
            'snapshot-provenance-published'
        } else {
            'snapshot-provenance-current'
        })
        $published | Add-Member -NotePropertyName snapshotChanged -NotePropertyValue $snapshotChanged
        $published | Add-Member -NotePropertyName pluginRoot -NotePropertyValue ([string]$validated.pluginRoot)
        return $published
    }
    catch {
        $operationFailed = $true
        throw
    }
    finally {
        $releaseError = $null
        while ($script:HeldLocks.Count -gt $startingLockCount) {
            try {
                Release-Lock
            }
            catch {
                Pop-HeldLock
                if ($null -eq $releaseError) {
                    $releaseError = $_.Exception
                }
                if ($operationFailed) {
                    [Console]::Error.WriteLine(
                        "installation-context: $($_.Exception.Message) while preserving the original snapshot failure."
                    )
                }
            }
        }
        if (-not $operationFailed -and $null -ne $releaseError) {
            throw $releaseError
        }
    }
}

function Assert-ExactPropertyCount($Object, [int]$Expected, [string]$Label) {
    if ($null -eq $Object -or $Object -isnot [pscustomobject]) {
        Fail "$Label must be a JSON object."
    }
    if (@($Object.PSObject.Properties).Count -ne $Expected) {
        Fail "$Label contains unknown or missing fields."
    }
}

function Resolve-RuntimeSlotPaths(
    $Validated,
    [string]$RequestedRuntimeVersion,
    [bool]$RequireExisting
) {
    Assert-RuntimeVersion $RequestedRuntimeVersion
    $pluginRoot = Canonical-Path ([string]$Validated.pluginRoot)
    $install = Read-Json ([string]$Validated.installReceipt)
    $roots = Get-PropertyValue $install 'roots'
    $versionsRelative = Get-StringProperty $roots 'versions'
    $separatorPattern = $(if ($env:OS -eq 'Windows_NT') { '[\\/]' } else { '/' })
    $cursor = $pluginRoot
    foreach ($part in @($versionsRelative -split $separatorPattern)) {
        if (-not $part) { continue }
        $cursor = Join-Path $cursor $part
        Assert-NotReparsePoint $cursor 'Versions root'
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -ne $entry -and -not $entry.PSIsContainer) {
            Fail 'Versions root path components must be ordinary directories.'
        }
    }
    if ($RequireExisting -and
        -not (Test-Path -LiteralPath $cursor -PathType Container)) {
        Fail 'Versions root must be an existing directory.'
    }
    $versionsRoot = Canonical-Path $cursor
    if (-not (Paths-Equal $versionsRoot ([string]$Validated.versionsRoot))) {
        Fail 'Versions root does not match the validated install receipt.'
    }
    if ((Paths-Equal $versionsRoot $pluginRoot) -or
        -not (Path-IsWithin $versionsRoot $pluginRoot)) {
        Fail 'Versions root must remain beneath the canonical plugin root.'
    }

    $lexicalSlotRoot = Join-Path $versionsRoot $RequestedRuntimeVersion
    Assert-NotReparsePoint $lexicalSlotRoot 'Runtime slot'
    $slotEntry = Get-Item -LiteralPath $lexicalSlotRoot -Force -ErrorAction SilentlyContinue
    if ($null -ne $slotEntry -and -not $slotEntry.PSIsContainer) {
        Fail 'Runtime slot must be an ordinary directory.'
    }
    if ($RequireExisting -and $null -eq $slotEntry) {
        Fail 'Runtime slot must be an existing directory.'
    }
    $slotRoot = Canonical-Path $lexicalSlotRoot
    if (-not (Paths-Equal (Split-Path -Parent $slotRoot) $versionsRoot)) {
        Fail 'Runtime slot must be one direct child of versionsRoot.'
    }
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') {
        $comparison = [StringComparison]::OrdinalIgnoreCase
    }
    if (-not [string]::Equals(
        (Split-Path -Leaf $slotRoot),
        $RequestedRuntimeVersion,
        $comparison
    )) {
        Fail 'Runtime slot does not retain the requested runtime version.'
    }
    $ownership = Join-Path $slotRoot '.runtime-slot-ownership.json'
    Assert-NotReparsePoint $ownership 'Runtime slot ownership'
    return [pscustomobject][ordered]@{
        versionsRelative = $versionsRelative
        versionsRoot = $versionsRoot
        slotRoot = $slotRoot
        ownership = $ownership
        slotExists = ($null -ne $slotEntry)
    }
}

function Resolve-RuntimeSlotCompletionPaths(
    $Validated,
    [string]$RequestedRuntimeVersion
) {
    $paths = Resolve-RuntimeSlotPaths $Validated $RequestedRuntimeVersion $true
    $buildReceipt = Join-Path $paths.slotRoot '.install-complete.json'
    $completion = Join-Path $paths.slotRoot '.runtime-slot-completion.json'
    Assert-NotReparsePoint $completion 'Runtime slot completion'
    $paths | Add-Member -NotePropertyName buildReceipt -NotePropertyValue $buildReceipt
    $paths | Add-Member -NotePropertyName completion -NotePropertyValue $completion
    return $paths
}

function Ensure-VersionsRootChain($Validated, [string]$VersionsRelative) {
    $separatorPattern = $(if ($env:OS -eq 'Windows_NT') { '[\\/]' } else { '/' })
    $cursor = Canonical-Path ([string]$Validated.pluginRoot)
    foreach ($part in @($VersionsRelative -split $separatorPattern)) {
        if (-not $part) { continue }
        $cursor = Join-Path $cursor $part
        Assert-NotReparsePoint $cursor 'Versions root'
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -eq $entry) {
            try {
                New-Item -ItemType Directory -Path $cursor -ErrorAction Stop | Out-Null
            }
            catch {
                Assert-NotReparsePoint $cursor 'Versions root'
                $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
                if ($null -eq $entry -or -not $entry.PSIsContainer) {
                    Fail "Cannot create versions root component '$cursor': $($_.Exception.Message)"
                }
            }
        }
        Assert-NotReparsePoint $cursor 'Versions root'
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -eq $entry -or -not $entry.PSIsContainer) {
            Fail 'Versions root path components must be ordinary directories.'
        }
    }
}

function New-RuntimeSlotOwnership(
    $Validated,
    $Snapshot,
    [string]$RequestedRuntimeVersion,
    [string]$SlotRoot,
    [string]$CreatedAt
) {
    return [ordered]@{
        schema = 'copilot-extensions.runtime-slot-ownership'
        version = 1
        marketplaceId = [string]$Validated.marketplaceId
        pluginId = [string]$Validated.pluginId
        sourceFingerprint = [string]$Validated.sourceFingerprint
        runtime = [ordered]@{
            version = $RequestedRuntimeVersion
            root = $SlotRoot
        }
        snapshot = [ordered]@{
            id = [string]$Snapshot.snapshotId
            root = [string]$Snapshot.snapshotRoot
            provenance = [string]$Snapshot.provenance
            provenanceSha256 = Get-FileSha256 ([string]$Snapshot.provenance)
        }
        namespaceReceipt = [ordered]@{
            path = [string]$Snapshot.namespaceReceipt
            generation = [long]$Snapshot.namespaceGeneration
        }
        installReceipt = [ordered]@{
            path = [string]$Snapshot.installReceipt
            generation = [long]$Snapshot.installGeneration
        }
        createdAt = $CreatedAt
    }
}

function Validate-RuntimeSlotOwnershipCore(
    $Validated,
    $Snapshot,
    [string]$RequestedRuntimeVersion
) {
    $paths = Resolve-RuntimeSlotPaths $Validated $RequestedRuntimeVersion $true
    if (-not (Test-Path -LiteralPath $paths.ownership)) {
        Fail 'Runtime slot ownership must exist.'
    }
    $actualOwnership = Canonical-Path $paths.ownership -MustExist
    if (-not (Paths-Equal $actualOwnership $paths.ownership)) {
        Fail "Runtime slot ownership is not at its exact canonical location '$($paths.ownership)'."
    }
    $ownershipEntry = Get-Item -LiteralPath $actualOwnership -Force
    if ($ownershipEntry.PSIsContainer -or
        (($ownershipEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        Fail 'Runtime slot ownership must be an ordinary file.'
    }
    $ownership = Read-Json $actualOwnership
    Assert-ExactPropertyCount $ownership 10 'Runtime slot ownership'
    $runtime = Get-PropertyValue $ownership 'runtime'
    $recordedSnapshot = Get-PropertyValue $ownership 'snapshot'
    $namespaceReference = Get-PropertyValue $ownership 'namespaceReceipt'
    $installReference = Get-PropertyValue $ownership 'installReceipt'
    Assert-ExactPropertyCount $runtime 2 'Runtime slot ownership runtime identity'
    Assert-ExactPropertyCount $recordedSnapshot 4 'Runtime slot ownership snapshot identity'
    Assert-ExactPropertyCount $namespaceReference 2 'Runtime slot ownership namespace receipt'
    Assert-ExactPropertyCount $installReference 2 'Runtime slot ownership install receipt'

    $ownershipVersion = Get-PropertyValue $ownership 'version'
    Assert-PositiveInteger $ownershipVersion 'runtime slot ownership version'
    if ((Get-StringProperty $ownership 'schema') -cne
            'copilot-extensions.runtime-slot-ownership' -or
        $ownershipVersion -ne 1) {
        Fail 'Runtime slot ownership has an unsupported schema or version.'
    }
    [void](Read-ExactUtcTimestampValue (
        Get-PropertyValue $ownership 'createdAt'
    ) 'runtime slot ownership createdAt')
    $namespaceGeneration = Get-PropertyValue $namespaceReference 'generation'
    $installGeneration = Get-PropertyValue $installReference 'generation'
    Assert-ReceiptGeneration $namespaceGeneration 'runtime slot ownership namespace generation'
    Assert-ReceiptGeneration $installGeneration 'runtime slot ownership install generation'

    if ((Get-StringProperty $ownership 'marketplaceId') -cne
            [string]$Snapshot.marketplaceId -or
        (Get-StringProperty $ownership 'pluginId') -cne
            [string]$Snapshot.pluginId -or
        (Get-StringProperty $ownership 'sourceFingerprint') -cne
            [string]$Snapshot.sourceFingerprint -or
        (Get-StringProperty $runtime 'version') -cne $RequestedRuntimeVersion -or
        (Get-StringProperty $recordedSnapshot 'id') -cne
            [string]$Snapshot.snapshotId -or
        (Get-StringProperty $recordedSnapshot 'provenanceSha256') -cne
            (Get-FileSha256 ([string]$Snapshot.provenance)) -or
        [long]$namespaceGeneration -ne [long]$Snapshot.namespaceGeneration -or
        [long]$installGeneration -ne [long]$Snapshot.installGeneration) {
        Fail 'Runtime slot ownership does not match the validated snapshot and installation receipts.'
    }

    $pathFields = @(
        @($runtime, 'root', [string]$paths.slotRoot),
        @($recordedSnapshot, 'root', [string]$Snapshot.snapshotRoot),
        @($recordedSnapshot, 'provenance', [string]$Snapshot.provenance),
        @($namespaceReference, 'path', [string]$Snapshot.namespaceReceipt),
        @($installReference, 'path', [string]$Snapshot.installReceipt)
    )
    foreach ($field in $pathFields) {
        $recordedPath = Get-StringProperty $field[0] ([string]$field[1])
        if (-not (Test-FullyQualifiedPath $recordedPath)) {
            Fail "Runtime slot ownership $($field[1]) must be absolute."
        }
        if (-not (Paths-Equal $recordedPath ([string]$field[2]))) {
            Fail 'Runtime slot ownership does not match the validated snapshot and installation receipts.'
        }
    }

    $namespace = Read-Json ([string]$Validated.namespaceReceipt)
    $slotContent = @(
        Get-ChildItem -LiteralPath $paths.slotRoot -Force |
            Where-Object { $_.Name -cne '.runtime-slot-ownership.json' } |
            Select-Object -First 1
    )
    return [pscustomobject][ordered]@{
        action = 'slot-validate'
        status = 'ready'
        reason = 'runtime-slot-ownership-valid'
        slotRoot = [string]$paths.slotRoot
        runtimeVersion = $RequestedRuntimeVersion
        ownership = $actualOwnership
        snapshotId = [string]$Snapshot.snapshotId
        snapshotProvenance = [string]$Snapshot.provenance
        marketplaceId = [string]$Validated.marketplaceId
        pluginId = [string]$Validated.pluginId
        sourceFingerprint = [string]$Validated.sourceFingerprint
        namespaceReceipt = [string]$Snapshot.namespaceReceipt
        installReceipt = [string]$Snapshot.installReceipt
        namespaceGeneration = [long]$Snapshot.namespaceGeneration
        installGeneration = [long]$Snapshot.installGeneration
        namespaceState = Get-StringProperty $namespace 'state'
        installState = Get-StringProperty $Validated 'state'
        slotEmpty = ($slotContent.Count -eq 0)
        activated = $false
        operative = $false
    }
}

function Invoke-SlotValidate([string]$ResolvedDurableHome) {
    if (-not $Context) { Fail 'slot-validate requires -Context.' }
    if (-not $ExpectedMarketplaceId) {
        Fail 'slot-validate requires -ExpectedMarketplaceId.'
    }
    if (-not $ExpectedPluginId) {
        Fail 'slot-validate requires -ExpectedPluginId.'
    }
    if (-not $SnapshotId) { Fail 'slot-validate requires -SnapshotId.' }
    if (-not $RuntimeVersion) { Fail 'slot-validate requires -RuntimeVersion.' }
    Assert-MarketplaceId $ExpectedMarketplaceId
    Assert-PluginId $ExpectedPluginId
    Assert-SnapshotId $SnapshotId
    Assert-RuntimeVersion $RuntimeVersion
    $validated = Invoke-WithoutPluginRoot {
        Validate-ContextReceipt $Context $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' ''
    }
    $snapshot = Validate-SnapshotProvenance `
        $Context `
        $ResolvedDurableHome `
        $ExpectedMarketplaceId `
        $ExpectedPluginId `
        $SnapshotId `
        $false `
        $ExpectedPayloadRoot `
        $ExpectedPayloadVersion
    return Validate-RuntimeSlotOwnershipCore $validated $snapshot $RuntimeVersion
}

function Invoke-SlotProvision([string]$ResolvedDurableHome) {
    if (-not $Context) { Fail 'slot-provision requires -Context.' }
    if (-not $ExpectedMarketplaceId) {
        Fail 'slot-provision requires -ExpectedMarketplaceId.'
    }
    if (-not $ExpectedPluginId) {
        Fail 'slot-provision requires -ExpectedPluginId.'
    }
    if (-not $SnapshotId) { Fail 'slot-provision requires -SnapshotId.' }
    if (-not $RuntimeVersion) { Fail 'slot-provision requires -RuntimeVersion.' }
    Assert-MarketplaceId $ExpectedMarketplaceId
    Assert-PluginId $ExpectedPluginId
    Assert-SnapshotId $SnapshotId
    Assert-RuntimeVersion $RuntimeVersion

    $validated = Invoke-WithoutPluginRoot {
        Validate-ContextReceipt $Context $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' ''
    }
    $cellRoot = [string]$validated.cellRoot
    $genesisLock = Join-Path (Join-Path $ResolvedDurableHome 'marketplaces/.locks') ($ExpectedMarketplaceId + '.genesis')
    $installLock = Join-Path (Join-Path $cellRoot '.locks') ($ExpectedPluginId + '.install.lock')
    $startingLockCount = $script:HeldLocks.Count
    $operationFailed = $false
    $temporarySlot = ''
    $temporaryOwnership = ''
    try {
        Acquire-Lock $genesisLock 'genesis' $ExpectedMarketplaceId '' `
            $script:RuntimeSlotLockTimeoutSeconds
        Acquire-Lock $installLock 'install' $ExpectedMarketplaceId $ExpectedPluginId `
            $script:RuntimeSlotLockTimeoutSeconds
        $validated = Invoke-WithoutPluginRoot {
            Validate-ContextReceipt $validated.installReceipt $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' $cellRoot
        }
        $paths = Resolve-RuntimeSlotPaths $validated $RuntimeVersion $false
        if ($paths.slotExists) {
            $snapshot = Validate-SnapshotProvenance `
                $validated.installReceipt `
                $ResolvedDurableHome `
                $ExpectedMarketplaceId `
                $ExpectedPluginId `
                $SnapshotId `
                $false `
                $ExpectedPayloadRoot `
                $ExpectedPayloadVersion
            $result = Validate-RuntimeSlotOwnershipCore $validated $snapshot $RuntimeVersion
            $result.action = 'slot-provision'
            $result.reason = 'runtime-slot-ownership-current'
            $result | Add-Member -NotePropertyName slotChanged -NotePropertyValue $false
            return $result
        }

        $snapshot = Validate-SnapshotProvenance `
            $validated.installReceipt `
            $ResolvedDurableHome `
            $ExpectedMarketplaceId `
            $ExpectedPluginId `
            $SnapshotId `
            $true `
            $ExpectedPayloadRoot `
            $ExpectedPayloadVersion
        Ensure-VersionsRootChain $validated $paths.versionsRelative
        $paths = Resolve-RuntimeSlotPaths $validated $RuntimeVersion $false

        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $slotDigest = ([BitConverter]::ToString(
                $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$paths.slotRoot))
            )).Replace('-', '').ToLowerInvariant().Substring(0, 16)
        }
        finally {
            $sha.Dispose()
        }
        $temporarySlot = Join-Path (
            Split-Path -Parent $paths.versionsRoot
        ) ('.runtime-slot-' + $slotDigest + '-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $temporarySlot -ErrorAction Stop | Out-Null
        $temporaryOwnership = Join-Path $temporarySlot '.runtime-slot-ownership.json'
        $ownership = New-RuntimeSlotOwnership `
            $validated `
            $snapshot `
            $RuntimeVersion `
            ([string]$paths.slotRoot) `
            (Get-UtcTimestamp)
        Write-AtomicJson $temporaryOwnership $ownership
        Assert-AllLocksOwned
        try {
            if ($env:OS -eq 'Windows_NT') {
                $moveResult = [CeAtomicDirectory]::MoveWindows(
                    $temporarySlot,
                    [string]$paths.slotRoot
                )
            }
            elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                    [Runtime.InteropServices.OSPlatform]::Linux
                )) {
                $moveResult = [CeAtomicDirectory]::MoveLinux(
                    $temporarySlot,
                    [string]$paths.slotRoot
                )
            }
            elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                    [Runtime.InteropServices.OSPlatform]::OSX
                )) {
                $moveResult = [CeAtomicDirectory]::MoveDarwin(
                    $temporarySlot,
                    [string]$paths.slotRoot
                )
            }
            else {
                Fail 'Atomic no-replace directory publication is unavailable.'
            }
        }
        catch {
            if (Test-Path -LiteralPath $paths.slotRoot) {
                Fail 'Runtime slot appeared during publication; refusing replacement.'
            }
            Fail "Cannot publish runtime slot '$($paths.slotRoot)': $($_.Exception.Message)"
        }
        if ($moveResult -eq 0) {
            Fail 'Runtime slot appeared during publication; refusing replacement.'
        }
        $temporarySlot = ''
        $temporaryOwnership = ''
        $result = Validate-RuntimeSlotOwnershipCore $validated $snapshot $RuntimeVersion
        $result.action = 'slot-provision'
        $result.reason = 'runtime-slot-ownership-published'
        $result | Add-Member -NotePropertyName slotChanged -NotePropertyValue $true
        return $result
    }
    catch {
        $operationFailed = $true
        throw
    }
    finally {
        $cleanupError = $null
        try {
            if ($temporaryOwnership -and
                (Test-Path -LiteralPath $temporaryOwnership -PathType Leaf)) {
                [IO.File]::Delete($temporaryOwnership)
            }
            if ($temporarySlot -and
                (Test-Path -LiteralPath $temporarySlot -PathType Container)) {
                [IO.Directory]::Delete($temporarySlot, $false)
            }
        }
        catch {
            $cleanupError = $_.Exception
            if ($operationFailed) {
                [Console]::Error.WriteLine(
                    "installation-context: $($_.Exception.Message) while preserving the original slot provisioning failure."
                )
            }
        }
        $releaseError = $null
        while ($script:HeldLocks.Count -gt $startingLockCount) {
            try {
                Release-Lock
            }
            catch {
                Pop-HeldLock
                if ($null -eq $releaseError) {
                    $releaseError = $_.Exception
                }
                if ($operationFailed) {
                    [Console]::Error.WriteLine(
                        "installation-context: $($_.Exception.Message) while preserving the original slot provisioning failure."
                    )
                }
            }
        }
        if (-not $operationFailed -and $null -ne $releaseError) {
            throw $releaseError
        }
        if (-not $operationFailed -and $null -ne $cleanupError) {
            throw $cleanupError
        }
    }
}

function Read-BuildCompletion(
    [string]$Path,
    [string]$RequestedRuntimeVersion,
    [string]$SnapshotContentSha256
) {
    $entry = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $entry) { Fail 'Build completion evidence must exist.' }
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail 'Build completion evidence may not be a symbolic link or reparse point.'
    }
    if ($entry.PSIsContainer) {
        Fail 'Build completion evidence must be an ordinary file.'
    }
    $actual = Canonical-Path $Path -MustExist
    if (-not (Paths-Equal $actual $Path)) {
        Fail "Build completion evidence is not at its exact canonical location '$Path'."
    }
    try {
        $validated = Read-RegularFileBytes $actual 'Build completion evidence'
        $bytes = [byte[]]$validated.bytes
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
            $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            Fail "UTF-8 BOM is not allowed in '$actual'."
        }
        $receipt = Read-JsonText (
            $strictUtf8.GetString($bytes)
        ) 'build completion evidence'
    }
    catch {
        Fail "Invalid JSON in '$actual': $($_.Exception.Message)"
    }
    Assert-ExactPropertyCount $receipt 4 'Build completion evidence'
    $version = Get-PropertyValue $receipt 'version'
    if ($version -isnot [string] -or $version -cne $RequestedRuntimeVersion) {
        Fail 'Build completion evidence version must match the runtime version.'
    }
    $completedAt = Read-ExactUtcTimestampValue (
        Get-PropertyValue $receipt 'completed_at'
    ) 'build completion completed_at'
    $pidValue = Get-PropertyValue $receipt 'pid'
    $pidIsInteger = (
        $pidValue -is [byte] -or
        $pidValue -is [int16] -or
        $pidValue -is [int32] -or
        $pidValue -is [int64]
    )
    if ($pidValue -is [bool] -or
        -not $pidIsInteger -or
        [long]$pidValue -lt 0 -or
        [long]$pidValue -gt 9223372036854775807) {
        Fail 'Build completion evidence pid must be an integer from 0 through 9223372036854775807.'
    }
    $payloadHash = Get-PropertyValue $receipt 'payload_hash'
    if ($payloadHash -isnot [string] -or
        $payloadHash -cnotmatch '^[0-9a-f]{64}$') {
        Fail 'Build completion evidence payload_hash must be lowercase 64-hex.'
    }
    if ($payloadHash -cne $SnapshotContentSha256) {
        Fail 'Build completion evidence payload_hash does not match the snapshot content digest.'
    }
    return [pscustomobject][ordered]@{
        path = $actual
        receiptSha256 = Get-BytesSha256 $bytes
        payloadSha256 = $payloadHash
        pid = [long]$pidValue
        completedAt = $completedAt
        value = $receipt
    }
}

function New-RuntimeSlotCompletion(
    $Validated,
    $Snapshot,
    $Ownership,
    $Build,
    [string]$RequestedRuntimeVersion,
    [string]$SlotRoot,
    [string]$SnapshotContentSha256
) {
    return [ordered]@{
        schema = 'copilot.extensions/runtime-slot-completion/v1'
        marketplaceId = [string]$Validated.marketplaceId
        pluginId = [string]$Validated.pluginId
        sourceFingerprint = [string]$Validated.sourceFingerprint
        runtime = [ordered]@{
            version = $RequestedRuntimeVersion
            root = $SlotRoot
        }
        snapshot = [ordered]@{
            id = [string]$Snapshot.snapshotId
            provenance = [string]$Snapshot.provenance
            provenanceSha256 = Get-FileSha256 ([string]$Snapshot.provenance)
            contentSha256 = $SnapshotContentSha256
        }
        ownership = [ordered]@{
            path = [string]$Ownership.ownership
            sha256 = Get-FileSha256 ([string]$Ownership.ownership)
        }
        build = [ordered]@{
            receipt = [string]$Build.path
            receiptSha256 = [string]$Build.receiptSha256
            payloadSha256 = [string]$Build.payloadSha256
            pid = [long]$Build.pid
        }
        namespaceReceipt = [ordered]@{
            path = [string]$Ownership.namespaceReceipt
            generation = [long]$Ownership.namespaceGeneration
        }
        installReceipt = [ordered]@{
            path = [string]$Ownership.installReceipt
            generation = [long]$Ownership.installGeneration
        }
        completedAt = [string]$Build.completedAt
    }
}

function Validate-RuntimeSlotCompletionCore(
    $Validated,
    $Snapshot,
    $Ownership,
    [string]$RequestedRuntimeVersion
) {
    $paths = Resolve-RuntimeSlotCompletionPaths $Validated $RequestedRuntimeVersion
    $snapshotContentSha256 = Get-SnapshotContentSha256 ([string]$Snapshot.snapshotRoot)
    $entry = Get-Item -LiteralPath $paths.completion -Force -ErrorAction SilentlyContinue
    if ($null -eq $entry) { Fail 'Runtime slot completion must exist.' }
    if ($entry.PSIsContainer -or
        (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        Fail 'Runtime slot completion must be an ordinary file.'
    }
    $actual = Canonical-Path $paths.completion -MustExist
    if (-not (Paths-Equal $actual $paths.completion)) {
        Fail "Runtime slot completion is not at its exact canonical location '$($paths.completion)'."
    }
    $receipt = Read-Json $actual
    Assert-ExactPropertyCount $receipt 11 'Runtime slot completion'
    $runtime = Get-PropertyValue $receipt 'runtime'
    $recordedSnapshot = Get-PropertyValue $receipt 'snapshot'
    $recordedOwnership = Get-PropertyValue $receipt 'ownership'
    $recordedBuild = Get-PropertyValue $receipt 'build'
    $namespaceReference = Get-PropertyValue $receipt 'namespaceReceipt'
    $installReference = Get-PropertyValue $receipt 'installReceipt'
    Assert-ExactPropertyCount $runtime 2 'Runtime slot completion runtime'
    Assert-ExactPropertyCount $recordedSnapshot 4 'Runtime slot completion snapshot'
    Assert-ExactPropertyCount $recordedOwnership 2 'Runtime slot completion ownership'
    Assert-ExactPropertyCount $recordedBuild 4 'Runtime slot completion build'
    Assert-ExactPropertyCount $namespaceReference 2 'Runtime slot completion namespaceReceipt'
    Assert-ExactPropertyCount $installReference 2 'Runtime slot completion installReceipt'
    if ((Get-StringProperty $receipt 'schema') -cne
            'copilot.extensions/runtime-slot-completion/v1') {
        Fail 'Runtime slot completion has an unsupported schema.'
    }
    [void](Read-ExactUtcTimestampValue (
        Get-PropertyValue $receipt 'completedAt'
    ) 'runtime slot completion completedAt')
    $namespaceGeneration = Get-PropertyValue $namespaceReference 'generation'
    $installGeneration = Get-PropertyValue $installReference 'generation'
    Assert-ReceiptGeneration $namespaceGeneration 'runtime slot completion namespaceReceipt generation'
    Assert-ReceiptGeneration $installGeneration 'runtime slot completion installReceipt generation'
    $snapshotProvenanceSha256 = Get-StringProperty $recordedSnapshot 'provenanceSha256'
    $recordedContentSha256 = Get-StringProperty $recordedSnapshot 'contentSha256'
    $ownershipSha256 = Get-StringProperty $recordedOwnership 'sha256'
    $buildReceiptSha256 = Get-StringProperty $recordedBuild 'receiptSha256'
    $payloadSha256 = Get-StringProperty $recordedBuild 'payloadSha256'
    foreach ($digest in @(
        $snapshotProvenanceSha256,
        $recordedContentSha256,
        $ownershipSha256,
        $buildReceiptSha256,
        $payloadSha256
    )) {
        if ($digest -cnotmatch '^[0-9a-f]{64}$') {
            Fail 'Runtime slot completion digests must be lowercase 64-hex.'
        }
    }
    $buildPid = Get-PropertyValue $recordedBuild 'pid'
    $pidIsInteger = (
        $buildPid -is [byte] -or
        $buildPid -is [int16] -or
        $buildPid -is [int32] -or
        $buildPid -is [int64]
    )
    if ($buildPid -is [bool] -or
        -not $pidIsInteger -or
        [long]$buildPid -lt 0 -or
        [long]$buildPid -gt 9223372036854775807) {
        Fail 'Runtime slot completion build.pid must be an integer from 0 through 9223372036854775807.'
    }
    $pathFields = @(
        @($runtime, 'root', [string]$paths.slotRoot),
        @($recordedSnapshot, 'provenance', [string]$Snapshot.provenance),
        @($recordedOwnership, 'path', [string]$paths.ownership),
        @($recordedBuild, 'receipt', [string]$paths.buildReceipt),
        @($namespaceReference, 'path', [string]$Ownership.namespaceReceipt),
        @($installReference, 'path', [string]$Ownership.installReceipt)
    )
    foreach ($field in $pathFields) {
        $recordedPath = Get-StringProperty $field[0] ([string]$field[1])
        if (-not (Test-FullyQualifiedPath $recordedPath)) {
            Fail "Runtime slot completion $($field[1]) must be absolute."
        }
        if ($recordedPath -cne [string]$field[2]) {
            Fail 'Runtime slot completion does not match the validated snapshot, ownership, and installation receipts.'
        }
    }
    if ((Get-StringProperty $receipt 'marketplaceId') -cne
            [string]$Validated.marketplaceId -or
        (Get-StringProperty $receipt 'pluginId') -cne
            [string]$Validated.pluginId -or
        (Get-StringProperty $receipt 'sourceFingerprint') -cne
            [string]$Validated.sourceFingerprint -or
        (Get-StringProperty $runtime 'version') -cne
            $RequestedRuntimeVersion -or
        (Get-StringProperty $recordedSnapshot 'id') -cne
            [string]$Snapshot.snapshotId -or
        $snapshotProvenanceSha256 -cne
            (Get-FileSha256 ([string]$Snapshot.provenance)) -or
        $recordedContentSha256 -cne $snapshotContentSha256 -or
        $ownershipSha256 -cne (Get-FileSha256 ([string]$paths.ownership)) -or
        $payloadSha256 -cne $snapshotContentSha256 -or
        [long]$namespaceGeneration -ne [long]$Ownership.namespaceGeneration -or
        [long]$installGeneration -ne [long]$Ownership.installGeneration) {
        Fail 'Runtime slot completion does not match the validated snapshot, ownership, and installation receipts.'
    }
    return [pscustomobject][ordered]@{
        action = 'slot-completion-validate'
        status = 'ready'
        reason = 'runtime-slot-completion-valid'
        slotRoot = [string]$paths.slotRoot
        runtimeVersion = $RequestedRuntimeVersion
        ownership = [string]$paths.ownership
        completion = $actual
        buildReceipt = [string]$paths.buildReceipt
        receipt = $receipt
        snapshotId = [string]$Snapshot.snapshotId
        snapshotProvenance = [string]$Snapshot.provenance
        marketplaceId = [string]$Validated.marketplaceId
        pluginId = [string]$Validated.pluginId
        sourceFingerprint = [string]$Validated.sourceFingerprint
        namespaceReceipt = [string]$Ownership.namespaceReceipt
        installReceipt = [string]$Ownership.installReceipt
        namespaceGeneration = [long]$Ownership.namespaceGeneration
        installGeneration = [long]$Ownership.installGeneration
        completedAt = Get-StringProperty $receipt 'completedAt'
        payloadSha256 = $payloadSha256
        activated = $false
        operative = $false
    }
}

function Invoke-SlotCompletionValidate([string]$ResolvedDurableHome) {
    if (-not $Context) { Fail 'slot-completion-validate requires -Context.' }
    if (-not $ExpectedMarketplaceId) {
        Fail 'slot-completion-validate requires -ExpectedMarketplaceId.'
    }
    if (-not $ExpectedPluginId) {
        Fail 'slot-completion-validate requires -ExpectedPluginId.'
    }
    if (-not $ExpectedPayloadRoot) {
        Fail 'slot-completion-validate requires -ExpectedPayloadRoot.'
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedPayloadVersion)) {
        Fail 'slot-completion-validate requires -ExpectedPayloadVersion.'
    }
    if (-not $SnapshotId) {
        Fail 'slot-completion-validate requires -SnapshotId.'
    }
    if (-not $RuntimeVersion) {
        Fail 'slot-completion-validate requires -RuntimeVersion.'
    }
    $validated = Invoke-WithoutPluginRoot {
        Validate-ContextReceipt $Context $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' ''
    }
    $snapshot = Validate-SnapshotProvenance `
        $Context `
        $ResolvedDurableHome `
        $ExpectedMarketplaceId `
        $ExpectedPluginId `
        $SnapshotId `
        $false `
        $ExpectedPayloadRoot `
        $ExpectedPayloadVersion
    $ownership = Validate-RuntimeSlotOwnershipCore $validated $snapshot $RuntimeVersion
    return Validate-RuntimeSlotCompletionCore `
        $validated `
        $snapshot `
        $ownership `
        $RuntimeVersion
}

function Publish-RuntimeSlotCompletion(
    [string]$Path,
    $Receipt
) {
    $temporary = Join-Path (
        Split-Path -Parent $Path
    ) ('.runtime-slot-completion.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Write-AtomicJson $temporary $Receipt
        Assert-AllLocksOwned
        try {
            if ($env:OS -eq 'Windows_NT') {
                $moveResult = [CeAtomicDirectory]::MoveWindows($temporary, $Path)
            }
            elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                    [Runtime.InteropServices.OSPlatform]::Linux
                )) {
                $moveResult = [CeAtomicDirectory]::MoveLinux($temporary, $Path)
            }
            elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                    [Runtime.InteropServices.OSPlatform]::OSX
                )) {
                $moveResult = [CeAtomicDirectory]::MoveDarwin($temporary, $Path)
            }
            else {
                Fail 'Atomic no-replace completion publication is unavailable.'
            }
            if ($moveResult -eq 0) {
                return $false
            }
            $temporary = ''
            return $true
        }
        catch {
            if ($null -ne (
                Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
            )) {
                return $false
            }
            Fail "Cannot publish runtime slot completion '$Path' without replacement: $($_.Exception.Message)"
        }
    }
    finally {
        if ($temporary -and (Test-Path -LiteralPath $temporary -PathType Leaf)) {
            [IO.File]::Delete($temporary)
        }
    }
}

function Invoke-SlotComplete([string]$ResolvedDurableHome) {
    if (-not $Context) { Fail 'slot-complete requires -Context.' }
    if (-not $ExpectedMarketplaceId) {
        Fail 'slot-complete requires -ExpectedMarketplaceId.'
    }
    if (-not $ExpectedPluginId) {
        Fail 'slot-complete requires -ExpectedPluginId.'
    }
    if (-not $ExpectedPayloadRoot) {
        Fail 'slot-complete requires -ExpectedPayloadRoot.'
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedPayloadVersion)) {
        Fail 'slot-complete requires -ExpectedPayloadVersion.'
    }
    if (-not $SnapshotId) { Fail 'slot-complete requires -SnapshotId.' }
    if (-not $RuntimeVersion) { Fail 'slot-complete requires -RuntimeVersion.' }

    $validated = Invoke-WithoutPluginRoot {
        Validate-ContextReceipt $Context $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' ''
    }
    $cellRoot = [string]$validated.cellRoot
    $genesisLock = Join-Path (Join-Path $ResolvedDurableHome 'marketplaces/.locks') ($ExpectedMarketplaceId + '.genesis')
    $installLock = Join-Path (Join-Path $cellRoot '.locks') ($ExpectedPluginId + '.install.lock')
    $startingLockCount = $script:HeldLocks.Count
    $operationFailed = $false
    try {
        Acquire-Lock $genesisLock 'genesis' $ExpectedMarketplaceId '' `
            $script:RuntimeSlotCompletionLockTimeoutSeconds
        Acquire-Lock $installLock 'install' $ExpectedMarketplaceId $ExpectedPluginId `
            $script:RuntimeSlotCompletionLockTimeoutSeconds
        $validated = Invoke-WithoutPluginRoot {
            Validate-ContextReceipt $validated.installReceipt $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' $cellRoot
        }
        $paths = Resolve-RuntimeSlotCompletionPaths $validated $RuntimeVersion
        $created = $false
        $completionEntry = Get-Item `
            -LiteralPath $paths.completion `
            -Force `
            -ErrorAction SilentlyContinue
        if ($null -ne $completionEntry) {
            $snapshot = Validate-SnapshotProvenance `
                $validated.installReceipt `
                $ResolvedDurableHome `
                $ExpectedMarketplaceId `
                $ExpectedPluginId `
                $SnapshotId `
                $false `
                $ExpectedPayloadRoot `
                $ExpectedPayloadVersion
            $ownership = Validate-RuntimeSlotOwnershipCore `
                $validated `
                $snapshot `
                $RuntimeVersion
        }
        else {
            $snapshot = Validate-SnapshotProvenance `
                $validated.installReceipt `
                $ResolvedDurableHome `
                $ExpectedMarketplaceId `
                $ExpectedPluginId `
                $SnapshotId `
                $true `
                $ExpectedPayloadRoot `
                $ExpectedPayloadVersion
            $ownership = Validate-RuntimeSlotOwnershipCore `
                $validated `
                $snapshot `
                $RuntimeVersion
            $snapshotContentSha256 = Get-SnapshotContentSha256 (
                [string]$snapshot.snapshotRoot
            )
            $build = Read-BuildCompletion `
                $paths.buildReceipt `
                $RuntimeVersion `
                $snapshotContentSha256
            $receipt = New-RuntimeSlotCompletion `
                $validated `
                $snapshot `
                $ownership `
                $build `
                $RuntimeVersion `
                ([string]$paths.slotRoot) `
                $snapshotContentSha256
            $confirmedSnapshotContentSha256 = Get-SnapshotContentSha256 (
                [string]$snapshot.snapshotRoot
            )
            if ($confirmedSnapshotContentSha256 -cne $snapshotContentSha256) {
                Fail 'Snapshot content changed before completion publication.'
            }
            $created = Publish-RuntimeSlotCompletion $paths.completion $receipt
        }
        $result = Validate-RuntimeSlotCompletionCore `
            $validated `
            $snapshot `
            $ownership `
            $RuntimeVersion
        $result.action = 'slot-complete'
        $result.reason = $(if ($created) {
            'runtime-slot-completion-published'
        } else {
            'runtime-slot-completion-current'
        })
        $result | Add-Member -NotePropertyName created -NotePropertyValue $created
        return $result
    }
    catch {
        $operationFailed = $true
        throw
    }
    finally {
        $releaseError = $null
        while ($script:HeldLocks.Count -gt $startingLockCount) {
            try {
                Release-Lock
            }
            catch {
                Pop-HeldLock
                if ($null -eq $releaseError) {
                    $releaseError = $_.Exception
                }
                if ($operationFailed) {
                    [Console]::Error.WriteLine(
                        "installation-context: $($_.Exception.Message) while preserving the original slot completion failure."
                    )
                }
            }
        }
        if (-not $operationFailed -and $null -ne $releaseError) {
            throw $releaseError
        }
    }
}

function Read-RuntimeMarker([string]$Path, [string]$Label) {
    $entry = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $entry) { return $null }
    if ($entry.PSIsContainer -or
        (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        Fail "$Label may not be a symbolic link or reparse point."
    }
    $actual = Canonical-Path $Path -MustExist
    if (-not (Paths-Equal $actual $Path)) {
        Fail "$Label is not at its exact canonical location '$Path'."
    }
    try {
        $capture = Read-RegularFileBytes `
            $actual `
            $Label `
            -RequireSameIdentity
        $value = $strictUtf8.GetString([byte[]]$capture.bytes)
    }
    catch {
        Fail "Cannot read $($Label.ToLowerInvariant()) '$actual': $($_.Exception.Message)"
    }
    if ($value.EndsWith("`r`n", [StringComparison]::Ordinal)) {
        $value = $value.Substring(0, $value.Length - 2)
    }
    elseif ($value.EndsWith("`n", [StringComparison]::Ordinal)) {
        $value = $value.Substring(0, $value.Length - 1)
    }
    if ([string]::IsNullOrEmpty($value) -or
        $value.Contains("`n") -or
        $value.Contains("`r") -or
        $value -cne $value.Trim()) {
        Fail "$Label must contain exactly one runtime version."
    }
    Assert-RuntimeVersion $value
    return $value
}

function Invoke-SlotCutover([string]$ResolvedDurableHome) {
    if (-not $Context) { Fail 'slot-cutover requires -Context.' }
    if (-not $ExpectedMarketplaceId) {
        Fail 'slot-cutover requires -ExpectedMarketplaceId.'
    }
    if (-not $ExpectedPluginId) {
        Fail 'slot-cutover requires -ExpectedPluginId.'
    }
    if (-not $ExpectedPayloadRoot) {
        Fail 'slot-cutover requires -ExpectedPayloadRoot.'
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedPayloadVersion)) {
        Fail 'slot-cutover requires -ExpectedPayloadVersion.'
    }
    if (-not $SnapshotId) { Fail 'slot-cutover requires -SnapshotId.' }
    if (-not $RuntimeVersion) { Fail 'slot-cutover requires -RuntimeVersion.' }
    if ($script:ExpectedCurrentVersionSupplied -eq [bool]$ExpectCurrentAbsent) {
        Fail 'Specify exactly one of -ExpectedCurrentVersion and -ExpectCurrentAbsent.'
    }
    if ($script:ExpectedCurrentVersionSupplied) {
        Assert-RuntimeVersion $ExpectedCurrentVersion
    }

    $validated = Invoke-WithoutPluginRoot {
        Validate-ContextReceipt $Context $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' ''
    }
    $cellRoot = [string]$validated.cellRoot
    $installPath = [string]$validated.installReceipt
    $genesisLock = Join-Path (
        Join-Path $ResolvedDurableHome 'marketplaces/.locks'
    ) ($ExpectedMarketplaceId + '.genesis')
    $installLock = Join-Path (
        Join-Path $cellRoot '.locks'
    ) ($ExpectedPluginId + '.install.lock')
    $startingLockCount = $script:HeldLocks.Count
    $operationFailed = $false
    try {
        Acquire-Lock $genesisLock 'genesis' $ExpectedMarketplaceId '' `
            $script:RuntimeSlotCompletionLockTimeoutSeconds
        Acquire-Lock $installLock 'install' $ExpectedMarketplaceId $ExpectedPluginId `
            $script:RuntimeSlotCompletionLockTimeoutSeconds
        $validated = Invoke-WithoutPluginRoot {
            Validate-ContextReceipt $installPath $ResolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId '' $cellRoot
        }
        $runtimeRoot = Split-Path -Parent ([string]$validated.versionsRoot)
        $currentPath = Join-Path $runtimeRoot 'current-version'
        $lastKnownGoodPath = Join-Path $runtimeRoot 'last-known-good'
        $actualNamespaceGeneration = [long]$validated.namespaceGeneration
        $actualInstallGeneration = [long]$validated.generation
        $actualCurrent = Read-RuntimeMarker $currentPath 'Current version marker'
        $actualLastKnownGood = Read-RuntimeMarker `
            $lastKnownGoodPath `
            'Last-known-good marker'
        if ($actualNamespaceGeneration -ne [long]$ExpectedNamespaceGeneration -or
            $actualInstallGeneration -ne [long]$ExpectedInstallGeneration) {
            return [pscustomobject][ordered]@{
                action = 'slot-cutover'
                status = 'revalidation-required'
                reason = 'generation-changed'
                cutoverChanged = $false
                runtimeVersion = $RuntimeVersion
                currentVersion = $actualCurrent
                lastKnownGoodVersion = $actualLastKnownGood
                namespaceGeneration = $actualNamespaceGeneration
                installGeneration = $actualInstallGeneration
                expectedNamespaceGeneration = [long]$ExpectedNamespaceGeneration
                expectedInstallGeneration = [long]$ExpectedInstallGeneration
                activated = $false
                operative = $false
            }
        }
        $namespaceReceipt = Read-Json ([string]$validated.namespaceReceipt)
        if ((Get-StringProperty $namespaceReceipt 'state') -cne 'active' -or
            (Get-StringProperty $validated 'state') -cne 'active') {
            Fail 'Runtime slot cutover requires active namespace and install receipts.'
        }
        $currentMatches = if ($ExpectCurrentAbsent) {
            $null -eq $actualCurrent
        } else {
            $actualCurrent -ceq $ExpectedCurrentVersion
        }
        if (-not $currentMatches) {
            return [pscustomobject][ordered]@{
                action = 'slot-cutover'
                status = 'revalidation-required'
                reason = 'current-version-changed'
                cutoverChanged = $false
                runtimeVersion = $RuntimeVersion
                currentVersion = $actualCurrent
                lastKnownGoodVersion = $actualLastKnownGood
                expectedCurrentVersion = $(if (
                    $script:ExpectedCurrentVersionSupplied
                ) { $ExpectedCurrentVersion } else { $null })
                expectedCurrentAbsent = [bool]$ExpectCurrentAbsent
                namespaceGeneration = $actualNamespaceGeneration
                installGeneration = $actualInstallGeneration
                activated = $false
                operative = $false
            }
        }

        $snapshot = Validate-SnapshotProvenance `
            $installPath `
            $ResolvedDurableHome `
            $ExpectedMarketplaceId `
            $ExpectedPluginId `
            $SnapshotId `
            $false `
            $ExpectedPayloadRoot `
            $ExpectedPayloadVersion
        $ownership = Validate-RuntimeSlotOwnershipCore `
            $validated `
            $snapshot `
            $RuntimeVersion
        $completion = Validate-RuntimeSlotCompletionCore `
            $validated `
            $snapshot `
            $ownership `
            $RuntimeVersion
        $confirmedCurrent = Read-RuntimeMarker `
            $currentPath `
            'Current version marker'
        $confirmedLastKnownGood = Read-RuntimeMarker `
            $lastKnownGoodPath `
            'Last-known-good marker'
        if ($confirmedCurrent -cne $actualCurrent -or
            $confirmedLastKnownGood -cne $actualLastKnownGood) {
            return [pscustomobject][ordered]@{
                action = 'slot-cutover'
                status = 'revalidation-required'
                reason = 'runtime-marker-changed'
                cutoverChanged = $false
                runtimeVersion = $RuntimeVersion
                currentVersion = $confirmedCurrent
                lastKnownGoodVersion = $confirmedLastKnownGood
                namespaceGeneration = $actualNamespaceGeneration
                installGeneration = $actualInstallGeneration
                activated = $false
                operative = $false
            }
        }

        $desiredLastKnownGood = $RuntimeVersion
        $changed = (
            $actualCurrent -cne $RuntimeVersion -or
            $actualLastKnownGood -cne $desiredLastKnownGood
        )
        if ($actualCurrent -cne $RuntimeVersion) {
            Write-AtomicText $currentPath $RuntimeVersion
        }
        if ($actualLastKnownGood -cne $desiredLastKnownGood) {
            Write-AtomicText $lastKnownGoodPath $desiredLastKnownGood
        }
        $publishedCurrent = Read-RuntimeMarker `
            $currentPath `
            'Current version marker'
        $publishedLastKnownGood = Read-RuntimeMarker `
            $lastKnownGoodPath `
            'Last-known-good marker'
        if ($publishedCurrent -cne $RuntimeVersion -or
            $publishedLastKnownGood -cne $desiredLastKnownGood) {
            Fail (
                'Published runtime cutover markers did not validate as current: ' +
                "current='$publishedCurrent', " +
                "last-known-good='$publishedLastKnownGood', " +
                "expected='$RuntimeVersion'."
            )
        }
        return [pscustomobject][ordered]@{
            action = 'slot-cutover'
            status = 'ready'
            reason = $(if ($changed) {
                'runtime-slot-cutover-published'
            } else {
                'runtime-slot-cutover-current'
            })
            cutoverChanged = $changed
            runtimeVersion = $RuntimeVersion
            previousVersion = $actualCurrent
            currentVersion = $publishedCurrent
            lastKnownGoodVersion = $publishedLastKnownGood
            currentMarker = $currentPath
            lastKnownGoodMarker = $lastKnownGoodPath
            completion = [string]$completion.completion
            namespaceGeneration = $actualNamespaceGeneration
            installGeneration = $actualInstallGeneration
            activated = $false
            operative = $false
        }
    }
    catch {
        $operationFailed = $true
        throw
    }
    finally {
        $releaseError = $null
        while ($script:HeldLocks.Count -gt $startingLockCount) {
            try {
                Release-Lock
            }
            catch {
                Pop-HeldLock
                if ($null -eq $releaseError) {
                    $releaseError = $_.Exception
                }
                if ($operationFailed) {
                    [Console]::Error.WriteLine(
                        "installation-context: $($_.Exception.Message) while preserving the original slot cutover failure."
                    )
                }
            }
        }
        if (-not $operationFailed -and $null -ne $releaseError) {
            throw $releaseError
        }
    }
}

function Stamp-Context($Resolved, [string]$ResolvedDurableHome) {
    if ([string]::IsNullOrWhiteSpace($PayloadVersion)) {
        Fail 'stamp requires -PayloadVersion.'
    }
    if ($PayloadOrigin -cnotin @('installed', 'directory', 'staged', 'explicit')) {
        Fail 'payload origin must be installed, directory, staged, or explicit.'
    }
    if ($ExpectedNamespaceGeneration -lt 0) {
        Fail 'stamp requires -ExpectedNamespaceGeneration.'
    }
    if ($ExpectedInstallGeneration -lt 0) {
        Fail 'stamp requires -ExpectedInstallGeneration.'
    }

    $originReceiptPath = ''
    if ($PayloadOriginReceipt) {
        if (-not [IO.Path]::IsPathRooted($PayloadOriginReceipt)) {
            Fail 'payload origin receipt must be absolute.'
        }
        $originReceiptPath = Canonical-Path $PayloadOriginReceipt -MustExist
    }

    $namespacePath = [string]$Resolved.namespaceReceipt
    $installPath = [string]$Resolved.installReceipt
    $cellRoot = [string]$Resolved.cellRoot
    $pluginRoot = [string]$Resolved.pluginRoot
    $marketplaceId = [string]$Resolved.marketplaceId
    $receiptPluginId = [string]$Resolved.pluginId
    $namespaceChanged = $false
    $installChanged = $false

    $genesisLock = Join-Path (Join-Path $ResolvedDurableHome 'marketplaces/.locks') ($marketplaceId + '.genesis')
    Acquire-Lock $genesisLock 'genesis' $marketplaceId
    $genesisFailed = $false
    try {
        $existingNamespace = $null
        $namespaceGeneration = 0
        $existingNamespaceState = ''
        $locators = @()
        if (Test-Path -LiteralPath $namespacePath -PathType Leaf) {
            $validatedNamespace = Validate-NamespaceReceipt $namespacePath $ResolvedDurableHome
            $existingNamespace = $validatedNamespace.receipt
            $namespaceGeneration = [long](Get-PropertyValue $existingNamespace 'generation')
            $existingNamespaceState = Get-StringProperty $existingNamespace 'state'
            $priorLocators = Get-PropertyValue $existingNamespace 'locators' @()
            $locators = @($priorLocators)
        }
        Assert-ExpectedGeneration $namespaceGeneration $ExpectedNamespaceGeneration 'namespace.json'
        $appendLocator = $false
        if ($null -ne $Resolved.locator) {
            $appendLocator = $true
            foreach ($known in $locators) {
                if (Locator-Matches $Resolved.locator $known) {
                    $appendLocator = $false
                    break
                }
            }
        }
        if ($appendLocator) { $locators += $Resolved.locator }
        if ($locators.Count -gt 16) {
            $locators = @($locators | Select-Object -Last 16)
        }
        if ($null -eq $existingNamespace -or
            $existingNamespaceState -ne $NamespaceState -or
            $appendLocator) {
            $now = Get-UtcTimestamp
            $createdAt = $now
            if ($null -ne $existingNamespace) {
                $createdAt = Get-ReceiptTimestamp $existingNamespace 'createdAt' $now
            }
            if ($namespaceGeneration -eq [int64]::MaxValue) {
                Fail 'namespace.json generation cannot be incremented; explicit repair is required.'
            }
            $namespaceGeneration++
            $namespace = [ordered]@{
                schema = 'copilot-extensions.marketplace-namespace'
                version = 1
                marketplaceId = $marketplaceId
                source = [ordered]@{
                    kind = [string]$Resolved.source.kind
                    canonical = [string]$Resolved.source.canonical
                    ref = [string]$Resolved.source.ref
                    fingerprint = [string]$Resolved.sourceFingerprint
                }
                locators = @($locators)
                generation = $namespaceGeneration
                state = $NamespaceState
                createdAt = $createdAt
                updatedAt = $now
            }
            Write-AtomicJson $namespacePath $namespace
            $namespaceChanged = $true
        }
    }
    catch {
        $genesisFailed = $true
        throw
    }
    finally {
        if ($script:HeldLocks.Count -gt 0) {
            try { Release-Lock }
            catch {
                if (-not $genesisFailed) { throw }
                [Console]::Error.WriteLine(
                    "installation-context: $($_.Exception.Message) while preserving the original mutation failure."
                )
            }
        }
    }

    $installLock = Join-Path (Join-Path $cellRoot '.locks') ($receiptPluginId + '.install.lock')
    Acquire-Lock $installLock 'install' $marketplaceId $receiptPluginId
    $installFailed = $false
    try {
        $existingInstall = $null
        $installGeneration = 0
        $existingInstallState = ''
        $currentPayloadRoot = ''
        $currentPayloadVersion = ''
        $currentPayloadOrigin = ''
        $currentOriginReceipt = ''
        if (Test-Path -LiteralPath $installPath -PathType Leaf) {
            $savedPluginRoot = $env:COPILOT_PLUGIN_ROOT
            try {
                Remove-Item Env:COPILOT_PLUGIN_ROOT -ErrorAction SilentlyContinue
                [void](Validate-ContextReceipt $installPath $ResolvedDurableHome $marketplaceId $receiptPluginId '' $cellRoot)
            }
            finally {
                if ($null -ne $savedPluginRoot) {
                    $env:COPILOT_PLUGIN_ROOT = $savedPluginRoot
                }
            }
            $existingInstall = Read-Json $installPath
            $installGeneration = [long](Get-PropertyValue $existingInstall 'generation')
            $existingInstallState = Get-StringProperty $existingInstall 'state'
            $existingPayload = Get-PropertyValue $existingInstall 'payload'
            $currentPayloadRoot = Canonical-Path (Get-StringProperty $existingPayload 'root')
            $currentPayloadVersion = Get-StringProperty $existingPayload 'version'
            $currentPayloadOrigin = Get-StringProperty $existingPayload 'origin'
            $currentOriginReceipt = Get-StringProperty $existingPayload 'originReceipt'
        }
        Assert-ExpectedGeneration $installGeneration $ExpectedInstallGeneration 'install.json'
        $payloadChanged = $null -eq $existingInstall
        if ($null -ne $existingInstall) {
            $payloadChanged = (-not (Paths-Equal $currentPayloadRoot $Resolved.payloadRoot) -or
                $currentPayloadVersion -ne $PayloadVersion -or
                $currentPayloadOrigin -ne $PayloadOrigin -or
                $currentOriginReceipt -ne $originReceiptPath -or
                $existingInstallState -ne $InstallState)
        }
        if ($payloadChanged) {
            $now = Get-UtcTimestamp
            $createdAt = $now
            if ($null -ne $existingInstall) {
                $createdAt = Get-ReceiptTimestamp $existingInstall 'createdAt' $now
                $existingRoots = Get-PropertyValue $existingInstall 'roots'
                $roots = [ordered]@{}
                foreach ($name in @('versions', 'snapshots', 'state', 'run', 'logs', 'cache', 'launchers')) {
                    $roots[$name] = Get-StringProperty $existingRoots $name
                }
            }
            else {
                $roots = [ordered]@{
                    versions = 'versions'
                    snapshots = 'snapshots'
                    state = 'state'
                    run = 'run'
                    logs = 'logs'
                    cache = 'cache'
                    launchers = 'launchers'
                }
            }
            $payload = [ordered]@{
                root = [string]$Resolved.payloadRoot
                version = $PayloadVersion
                origin = $PayloadOrigin
            }
            if ($originReceiptPath) { $payload['originReceipt'] = $originReceiptPath }
            if ($installGeneration -eq [int64]::MaxValue) {
                Fail 'install.json generation cannot be incremented; explicit repair is required.'
            }
            $installGeneration++
            $install = [ordered]@{
                schema = 'copilot-extensions.plugin-installation'
                version = 1
                marketplaceId = $marketplaceId
                pluginId = $receiptPluginId
                pluginRoot = $pluginRoot
                namespaceReceipt = $namespacePath
                payload = $payload
                roots = $roots
                generation = $installGeneration
                state = $InstallState
                createdAt = $createdAt
                updatedAt = $now
            }
            Write-AtomicJson $installPath $install
            $installChanged = $true
        }
    }
    catch {
        $installFailed = $true
        throw
    }
    finally {
        if ($script:HeldLocks.Count -gt 0) {
            try { Release-Lock }
            catch {
                if (-not $installFailed) { throw }
                [Console]::Error.WriteLine(
                    "installation-context: $($_.Exception.Message) while preserving the original mutation failure."
                )
            }
        }
    }

    $validated = Validate-ContextReceipt $installPath $ResolvedDurableHome $marketplaceId $receiptPluginId $Resolved.payloadRoot $cellRoot
    $validated.action = 'stamp'
    $validated | Add-Member -NotePropertyName namespaceChanged -NotePropertyValue $namespaceChanged
    $validated | Add-Member -NotePropertyName installChanged -NotePropertyValue $installChanged
    $validated | Add-Member -NotePropertyName operative -NotePropertyValue $false
    return $validated
}

try {
    Assert-ExactChoice $Action @(
        'source-id',
        'resolve',
        'validate',
        'stamp',
        'activation-cas',
        'snapshot-stamp',
        'snapshot-validate',
        'slot-provision',
        'slot-validate',
        'slot-complete',
        'slot-completion-validate',
        'slot-cutover',
        'status',
        'probe-legacy'
    ) 'Action'
    $expectedPayloadRootSupplied = $PSBoundParameters.ContainsKey('ExpectedPayloadRoot')
    $expectedPayloadVersionSupplied = $PSBoundParameters.ContainsKey('ExpectedPayloadVersion')
    $script:ExpectedCurrentVersionSupplied = $PSBoundParameters.ContainsKey(
        'ExpectedCurrentVersion'
    )
    if ($Action -cne 'slot-cutover' -and (
        $script:ExpectedCurrentVersionSupplied -or
        $PSBoundParameters.ContainsKey('ExpectCurrentAbsent')
    )) {
        Fail '-ExpectedCurrentVersion and -ExpectCurrentAbsent are valid only for slot-cutover.'
    }
    if ($Action -in @(
        'slot-provision',
        'slot-validate',
        'slot-complete',
        'slot-completion-validate',
        'slot-cutover'
    )) {
        if ($expectedPayloadRootSupplied -and
            [string]::IsNullOrWhiteSpace($ExpectedPayloadRoot)) {
            Fail 'Expected snapshot payload root must be absolute.'
        }
        if ($expectedPayloadVersionSupplied -and
            [string]::IsNullOrWhiteSpace($ExpectedPayloadVersion)) {
            Fail 'Expected snapshot payload version must be a non-empty string.'
        }
    }
    elseif ($expectedPayloadVersionSupplied) {
        Fail '-ExpectedPayloadVersion is valid only for runtime slot actions.'
    }
    if ($Action -in @(
        'slot-complete',
        'slot-completion-validate',
        'slot-cutover'
    )) {
        if (-not $expectedPayloadRootSupplied) {
            Fail "$Action requires -ExpectedPayloadRoot."
        }
        if (-not $expectedPayloadVersionSupplied) {
            Fail "$Action requires -ExpectedPayloadVersion."
        }
    }
    if ($expectedPayloadRootSupplied -and
        $Action -notin @(
            'resolve',
            'validate',
            'slot-provision',
            'slot-validate',
            'slot-complete',
            'slot-completion-validate',
            'slot-cutover',
            'status',
            'probe-legacy'
        )) {
        Fail "-ExpectedPayloadRoot is not valid for $Action."
    }
    if ($Action -cin @(
        'stamp',
        'activation-cas',
        'snapshot-stamp',
        'slot-cutover'
    )) {
        $ExpectedNamespaceGeneration = ConvertTo-ExpectedGeneration `
            $ExpectedNamespaceGeneration `
            'namespace.json'
        $ExpectedInstallGeneration = ConvertTo-ExpectedGeneration `
            $ExpectedInstallGeneration `
            'install.json'
    }
    if ($Action -ceq 'activation-cas') {
        $ExpectedActivationGeneration = ConvertTo-ExpectedGeneration `
            $ExpectedActivationGeneration `
            'activation'
    }
    if ($PayloadOrigin -cnotin @('', 'installed', 'directory', 'staged', 'explicit')) {
        Fail 'payload origin must be installed, directory, staged, or explicit.'
    }
    Assert-ReceiptState $NamespaceState 'NamespaceState'
    Assert-ReceiptState $InstallState 'InstallState'

    if (-not $CopilotHome) { $CopilotHome = Get-DefaultHome '.copilot' }
    if (-not $DurableHome) { $DurableHome = Get-DefaultHome '.copilot-extensions' }
    if (-not [IO.Path]::IsPathRooted($CopilotHome) -or
        -not [IO.Path]::IsPathRooted($DurableHome)) {
        Fail '-CopilotHome and -DurableHome must be absolute.'
    }
    $resolvedCopilotHome = Canonical-Path $CopilotHome
    $resolvedDurableHome = Canonical-Path $DurableHome
    $resolvedProjectRoot = ''
    if ($ProjectRoot) { $resolvedProjectRoot = Canonical-Path $ProjectRoot -MustExist }

    if ($Action -eq 'source-id') {
        $descriptor = Read-SourceDescriptor
        if ($null -eq $descriptor) {
            Fail 'source-id requires -SourceJson or -SourceFile.'
        }
        $normalized = Normalize-Source $descriptor ''
        $name = $MarketplaceKey
        if (-not $name) { $name = 'marketplace' }
        $result = Source-Identity $normalized $name
    }
    elseif ($Action -eq 'status' -or $Action -eq 'probe-legacy') {
        $result = Resolve-InstallationStatus $resolvedCopilotHome $resolvedProjectRoot $resolvedDurableHome
    }
    elseif ($Action -eq 'activation-cas') {
        $result = Invoke-ActivationCas $resolvedDurableHome
    }
    elseif ($Action -eq 'snapshot-stamp') {
        $result = Invoke-SnapshotStamp $resolvedDurableHome
    }
    elseif ($Action -eq 'snapshot-validate') {
        $result = Validate-SnapshotProvenance `
            $Context `
            $resolvedDurableHome `
            $ExpectedMarketplaceId `
            $ExpectedPluginId `
            $SnapshotId
    }
    elseif ($Action -eq 'slot-provision') {
        $result = Invoke-SlotProvision $resolvedDurableHome
    }
    elseif ($Action -eq 'slot-validate') {
        $result = Invoke-SlotValidate $resolvedDurableHome
    }
    elseif ($Action -eq 'slot-complete') {
        $result = Invoke-SlotComplete $resolvedDurableHome
    }
    elseif ($Action -eq 'slot-completion-validate') {
        $result = Invoke-SlotCompletionValidate $resolvedDurableHome
    }
    elseif ($Action -eq 'slot-cutover') {
        $result = Invoke-SlotCutover $resolvedDurableHome
    }
    elseif ($Action -eq 'validate') {
        $pointer = $Context
        if (-not $pointer) { $pointer = $env:COPILOT_EXTENSIONS_CONTEXT }
        if (-not $pointer) { Fail 'validate requires -Context or COPILOT_EXTENSIONS_CONTEXT.' }
        $payloadExpectation = $ExpectedPayloadRoot
        if (-not $payloadExpectation -and $PayloadRoot) { $payloadExpectation = $PayloadRoot }
        $result = Validate-ContextReceipt $pointer $resolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId $payloadExpectation $ExpectedCellRoot
    }
    else {
        $pointer = ''
        if ($Action -eq 'resolve') {
            $pointer = $Context
            if (-not $pointer) { $pointer = $env:COPILOT_EXTENSIONS_CONTEXT }
        }
        if ($pointer) {
            $payloadExpectation = $ExpectedPayloadRoot
            if (-not $payloadExpectation) { $payloadExpectation = $PayloadRoot }
            if (-not $payloadExpectation) { $payloadExpectation = $env:COPILOT_PLUGIN_ROOT }
            $pluginExpectation = $PluginId
            if (-not $pluginExpectation) { $pluginExpectation = $ExpectedPluginId }
            if (-not $pluginExpectation) {
                Fail 'resolve with an explicit context requires an expected plugin id.'
            }
            if (-not $payloadExpectation -and -not $ExpectedMarketplaceId -and
                -not $ExpectedCellRoot) {
                Fail 'resolve with an explicit context requires an expected payload, marketplace, or cell identity.'
            }
            $result = Validate-ContextReceipt $pointer $resolvedDurableHome $ExpectedMarketplaceId $pluginExpectation $payloadExpectation $ExpectedCellRoot
            $result.action = 'resolve'
        }
        else {
            if (-not $PayloadRoot) { $PayloadRoot = $env:COPILOT_PLUGIN_ROOT }
            if (-not $PayloadRoot) { Fail "$Action requires -PayloadRoot or COPILOT_PLUGIN_ROOT." }
            if (-not [IO.Path]::IsPathRooted($PayloadRoot)) {
                Fail 'The payload root must be absolute.'
            }
            $resolvedPayload = Canonical-Path $PayloadRoot -MustExist
            if (-not (Test-Path -LiteralPath $resolvedPayload -PathType Container)) {
                Fail "The payload root must be an existing directory: $resolvedPayload"
            }
            if ($env:COPILOT_PLUGIN_ROOT) {
                if (-not [IO.Path]::IsPathRooted($env:COPILOT_PLUGIN_ROOT)) {
                    Fail 'COPILOT_PLUGIN_ROOT must be absolute.'
                }
                if (-not (Paths-Equal $resolvedPayload $env:COPILOT_PLUGIN_ROOT)) {
                    Fail 'COPILOT_PLUGIN_ROOT conflicts with -PayloadRoot.'
                }
            }
            $descriptor = Read-SourceDescriptor
            $evidence = $null
            if ($null -ne $descriptor) {
                if (-not $PluginId) { Fail 'Explicit source resolution requires -PluginId.' }
                $evidence = [pscustomobject]@{
                    source = Normalize-Source $descriptor ''
                    pluginId = $PluginId
                    readableName = $(if ($MarketplaceKey) { $MarketplaceKey } else { 'marketplace' })
                    locator = $null
                }
            }
            else {
                $evidence = Resolve-InstalledEvidence $resolvedPayload $resolvedCopilotHome $resolvedProjectRoot
                if ($null -eq $evidence) {
                    $evidence = Resolve-DirectoryEvidence $resolvedPayload $PluginId
                }
                if ($null -eq $evidence) {
                    Fail "Cannot establish marketplace provenance for payload '$resolvedPayload'. Supply an explicit source descriptor for management/development mode."
                }
                if ($PluginId -and $PluginId -cne $evidence.pluginId) {
                    Fail "Expected plugin '$PluginId', payload evidence identifies '$($evidence.pluginId)'."
                }
            }
            $identity = Source-Identity $evidence.source $evidence.readableName
            Assert-PluginId $evidence.pluginId
            $existing = @(Find-ExistingSource $resolvedDurableHome $identity.fingerprint $identity.marketplaceId $evidence.locator)
            $rebind = @($existing | Where-Object { -not $_.sameId -or -not $_.locatorMatch })
            if ($rebind.Count -gt 0) {
                $owners = ($rebind | ForEach-Object { $_.marketplaceId }) -join ', '
                Fail "Source '$($identity.fingerprint)' already owns cell/locator '$owners'; explicit rebind or new-cell intent is required."
            }
            $cellRoot = Canonical-Path (Join-Path (Join-Path $resolvedDurableHome 'marketplaces') $identity.marketplaceId)
            $pluginRootPath = Canonical-Path (Join-Path (Join-Path $cellRoot 'plugins') $evidence.pluginId)
            $result = [pscustomobject][ordered]@{
                action = 'resolve'
                source = [pscustomobject][ordered]@{
                    kind = $identity.kind
                    canonical = $identity.canonical
                    ref = $identity.ref
                    record = $identity.record
                }
                sourceFingerprint = $identity.fingerprint
                marketplaceId = $identity.marketplaceId
                marketplaceSlot = $identity.marketplaceId
                pluginId = $evidence.pluginId
                payloadRoot = $resolvedPayload
                cellRoot = $cellRoot
                pluginRoot = $pluginRootPath
                versionsRoot = Canonical-Path (Join-Path $pluginRootPath 'versions')
                snapshotsRoot = Canonical-Path (Join-Path $pluginRootPath 'snapshots')
                stateRoot = Canonical-Path (Join-Path $pluginRootPath 'state')
                runRoot = Canonical-Path (Join-Path $pluginRootPath 'run')
                logsRoot = Canonical-Path (Join-Path $pluginRootPath 'logs')
                cacheRoot = Canonical-Path (Join-Path $pluginRootPath 'cache')
                launchersRoot = Canonical-Path (Join-Path $pluginRootPath 'launchers')
                reposRoot = Canonical-Path (Join-Path $cellRoot 'repos')
                namespaceReceipt = Canonical-Path (Join-Path $cellRoot 'namespace.json')
                installReceipt = Canonical-Path (Join-Path $pluginRootPath 'install.json')
                locator = $evidence.locator
                existingCells = $existing
                rebindRequired = $false
                operative = $false
            }
            if ($Action -eq 'stamp') {
                $result = Stamp-Context $result $resolvedDurableHome
            }
        }
    }
    $result | ConvertTo-Json -Depth 12 -Compress
    if ($Action -eq 'probe-legacy' -and -not $result.allowMutation) {
        exit 3
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine("installation-context: $($_.Exception.Message)")
    exit 1
}
