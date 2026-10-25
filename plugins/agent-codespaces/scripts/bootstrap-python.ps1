function Get-EvaluatorBootstrapRegistrySnapshot {
    $locations = @(
        @{ Hive = [Microsoft.Win32.RegistryHive]::CurrentUser; View = [Microsoft.Win32.RegistryView]::Default; Rank = 0 },
        @{ Hive = [Microsoft.Win32.RegistryHive]::LocalMachine; View = [Microsoft.Win32.RegistryView]::Registry64; Rank = 1 },
        @{ Hive = [Microsoft.Win32.RegistryHive]::LocalMachine; View = [Microsoft.Win32.RegistryView]::Registry32; Rank = 2 }
    )
    foreach ($location in $locations) {
        if (-not [Environment]::Is64BitOperatingSystem -and
            $location.View -eq [Microsoft.Win32.RegistryView]::Registry64) { continue }
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($location.Hive, $location.View)
        try {
            $company = $base.OpenSubKey('Software\Python\PythonCore', $false)
            if ($null -eq $company) { continue }
            try {
                $tags = $company.GetSubKeyNames()
                if ($tags.Count -gt 64) { throw 'CPython registration inventory exceeds its bounded limit.' }
                foreach ($tag in $tags) {
                    $install = $company.OpenSubKey("$tag\InstallPath", $false)
                    try {
                        $root = $null
                        $python = $null
                        $arguments = $null
                        $options = [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
                        if ($null -ne $install) {
                            $root = $install.GetValue('', $null, $options)
                            $python = $install.GetValue('ExecutablePath', $null, $options)
                            $arguments = $install.GetValue('ExecutableArguments', $null, $options)
                        }
                        [pscustomobject]@{
                            Root = $root
                            Python = $python
                            Arguments = $arguments
                            Tag = $tag
                            Rank = [int]$location.Rank
                        }
                    } finally {
                        if ($null -ne $install) { $install.Dispose() }
                    }
                }
            } finally {
                $company.Dispose()
            }
        } finally {
            $base.Dispose()
        }
    }
}

function Get-EvaluatorBootstrapRegistrations {
    $snapshot = @(Get-EvaluatorBootstrapRegistrySnapshot)
    $userTags = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($row in $snapshot) {
        if ($row.Rank -eq 0) { [void]$userTags.Add($row.Tag) }
    }
    foreach ($row in $snapshot) {
        if ($row.Rank -ne 0 -and $userTags.Contains($row.Tag)) { continue }
        if ($row.Root -isnot [string] -or -not $row.Root -or $row.Arguments) { continue }
        if ($null -ne $row.Arguments -and $row.Arguments -isnot [string]) { continue }
        $python = $row.Python
        if ($null -eq $python) {
            try {
                $python = [IO.Path]::Combine($row.Root, 'python.exe')
            } catch [ArgumentException] {
                continue
            }
        }
        if ($python -isnot [string] -or -not $python) { continue }
        [pscustomobject]@{ Root = $row.Root; Python = $python; Tag = $row.Tag; Rank = $row.Rank }
    }
}

function Test-EvaluatorBootstrapUnredirectedPath {
    param([string]$Path, [switch]$AllowMissing)
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':')) { return $false }
    $current = [IO.Path]::GetPathRoot($Path)
    if ([IO.DriveInfo]::new($current).DriveType -ne [IO.DriveType]::Fixed) { return $false }
    $segments = @('') + @($Path.Substring($current.Length).Split('\') | Where-Object { $_ })
    foreach ($segment in $segments) {
        if ($segment) { $current = [IO.Path]::Combine($current, $segment) }
        try {
            $attributes = [IO.File]::GetAttributes($current)
        } catch [IO.FileNotFoundException] {
            if ($AllowMissing) { return $true }
            throw
        } catch [IO.DirectoryNotFoundException] {
            if ($AllowMissing) { return $true }
            throw
        }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
    }
    return $true
}

function Test-EvaluatorBootstrapPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Root,
        [string[]]$ExcludedRoots = @()
    )
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Root -notmatch '^[A-Za-z]:\\' -or
        $Path.Substring(2).Contains(':') -or $Root.Substring(2).Contains(':')) { return $false }
    try {
        $full = [IO.Path]::GetFullPath($Path)
        $base = [IO.Path]::GetFullPath($Root)
    } catch [ArgumentException] {
        return $false
    } catch [NotSupportedException] {
        return $false
    }
    if (-not [string]::Equals([IO.Path]::GetFileName($full), 'python.exe', [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([IO.Path]::GetDirectoryName($full).TrimEnd('\'), $base.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    if (-not (Test-EvaluatorBootstrapUnredirectedPath -Path $full)) { return $false }
    foreach ($excluded in $ExcludedRoots) {
        if (-not $excluded) { continue }
        if (-not [IO.Path]::IsPathRooted($excluded)) {
            throw 'Excluded runtime roots must be absolute.'
        }
        $excludedFull = [IO.Path]::GetFullPath($excluded).TrimEnd('\')
        # An aliased addressed root cannot establish independent evaluator admission.
        if (-not (Test-EvaluatorBootstrapUnredirectedPath -Path $excludedFull -AllowMissing)) { return $false }
        if ([string]::Equals($full, $excludedFull, [StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith($excludedFull + '\', [StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    $parent = [IO.Path]::GetDirectoryName($base.TrimEnd('\'))
    foreach ($directory in @($base, $parent)) {
        if ($directory) {
            $marker = [IO.Path]::Combine($directory, 'pyvenv.cfg')
            if ([IO.File]::Exists($marker) -or [IO.Directory]::Exists($marker)) { return $false }
        }
    }
    if ([IO.Directory]::GetFiles($base, '*._pth', [IO.SearchOption]::TopDirectoryOnly).Count -gt 0) { return $false }
    return $true
}

function Initialize-EvaluatorBootstrapTrust {
    if ('CopilotExtensions.EvaluatorBootstrapTrustV1' -as [type]) { return }
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;
using Microsoft.Win32.SafeHandles;

namespace CopilotExtensions {
    public sealed class BootstrapTrustResult {
        public uint Status;
        public string Publisher;
    }

    public static class EvaluatorBootstrapTrustV1 {
        [StructLayout(LayoutKind.Sequential)]
        private struct TrustFile {
            public uint Size;
            public IntPtr Path;
            public IntPtr File;
            public IntPtr KnownSubject;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct TrustData {
            public uint Size;
            public IntPtr Policy;
            public IntPtr Sip;
            public uint Ui;
            public uint Revocation;
            public uint Choice;
            public IntPtr File;
            public uint Action;
            public IntPtr State;
            public IntPtr Url;
            public uint Flags;
            public uint UiContext;
            public IntPtr SignatureSettings;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProviderSigner {
            public uint Size;
            public System.Runtime.InteropServices.ComTypes.FILETIME VerifyAt;
            public uint CertificateCount;
            public IntPtr Certificates;
            public uint SignerType;
            public IntPtr Signer;
            public uint Error;
            public uint CounterSignerCount;
            public IntPtr CounterSigners;
            public IntPtr Chain;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProviderCertificate {
            public uint Size;
            public IntPtr Certificate;
        }

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("wintrust.dll", ExactSpelling = true)]
        private static extern uint WinVerifyTrust(IntPtr window, ref Guid action, ref TrustData data);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("wintrust.dll", ExactSpelling = true)]
        private static extern IntPtr WTHelperProvDataFromStateData(IntPtr state);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("wintrust.dll", ExactSpelling = true)]
        private static extern IntPtr WTHelperGetProvSignerFromChain(
            IntPtr provider, uint index, [MarshalAs(UnmanagedType.Bool)] bool counterSigner, uint counterIndex);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("kernel32.dll", ExactSpelling = true)]
        private static extern void GetNativeSystemInfo(IntPtr info);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(
            SafeFileHandle file, System.Text.StringBuilder path, uint capacity, uint flags);

        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle OpenDirectory(
            string path, uint access, uint sharing, IntPtr security, uint disposition, uint flags, IntPtr template);

        public static bool IsCanonicalDrive(string path) {
            string root = Path.GetPathRoot(path);
            using (SafeFileHandle directory = OpenDirectory(
                root, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
                if (directory.IsInvalid)
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                return String.Equals(FinalPath(directory).TrimEnd('\\'), root.TrimEnd('\\'),
                    StringComparison.OrdinalIgnoreCase);
            }
        }

        public static string FinalPath(SafeFileHandle file) {
            System.Text.StringBuilder path = new System.Text.StringBuilder(1024);
            uint size = GetFinalPathNameByHandle(file, path, (uint)path.Capacity, 0);
            if (size == 0) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            if (size >= path.Capacity) {
                if (size > 32768) throw new InvalidDataException("Bootstrap final path exceeds its bounded limit.");
                path = new System.Text.StringBuilder((int)size + 1);
                size = GetFinalPathNameByHandle(file, path, (uint)path.Capacity, 0);
                if (size == 0) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                if (size >= path.Capacity) throw new IOException("Bootstrap final path changed during admission.");
            }
            string result = path.ToString();
            return result.StartsWith(@"\\?\", StringComparison.Ordinal) ? result.Substring(4) : result;
        }

        public static ushort NativeMachine() {
            IntPtr info = Marshal.AllocHGlobal(64);
            try {
                GetNativeSystemInfo(info);
                ushort architecture = (ushort)Marshal.ReadInt16(info);
                if (architecture == 0) return 0x014c;
                if (architecture == 9) return 0x8664;
                if (architecture == 12) return 0xaa64;
                return 0;
            } finally {
                Marshal.FreeHGlobal(info);
            }
        }

        public static ushort ReadMachine(FileStream stream) {
            long position = stream.Position;
            try {
                using (BinaryReader reader = new BinaryReader(stream, System.Text.Encoding.UTF8, true)) {
                    if (stream.Length < 64 || reader.ReadUInt16() != 0x5a4d) return 0;
                    stream.Position = 0x3c;
                    uint offset = reader.ReadUInt32();
                    if (offset < 64 || offset > stream.Length - 6) return 0;
                    stream.Position = offset;
                    if (reader.ReadUInt32() != 0x00004550) return 0;
                    return reader.ReadUInt16();
                }
            } finally {
                stream.Position = position;
            }
        }

        public static BootstrapTrustResult Verify(string path, SafeFileHandle handle) {
            Guid action = new Guid("00AAC56B-CD44-11D0-8CC2-00C04FC295EE");
            TrustFile file = new TrustFile();
            file.Size = (uint)Marshal.SizeOf(typeof(TrustFile));
            file.Path = Marshal.StringToHGlobalUni(path);
            file.File = handle.DangerousGetHandle();
            IntPtr pointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(TrustFile)));
            TrustData data = new TrustData();
            data.Size = (uint)Marshal.SizeOf(typeof(TrustData));
            data.Ui = 2;
            data.Choice = 1;
            data.File = pointer;
            data.Action = 1;
            data.Flags = 0x1000;
            try {
                Marshal.StructureToPtr(file, pointer, false);
                uint result = WinVerifyTrust(new IntPtr(-1), ref action, ref data);
                BootstrapTrustResult outcome = new BootstrapTrustResult();
                outcome.Status = result;
                if (result != 0) return outcome;
                IntPtr provider = WTHelperProvDataFromStateData(data.State);
                if (provider == IntPtr.Zero) throw new InvalidOperationException("Verified signer state is unavailable.");
                IntPtr signerPointer = WTHelperGetProvSignerFromChain(provider, 0, false, 0);
                if (signerPointer == IntPtr.Zero) throw new InvalidOperationException("Verified signer is unavailable.");
                ProviderSigner signer = (ProviderSigner)Marshal.PtrToStructure(signerPointer, typeof(ProviderSigner));
                if (signer.Error != 0 || signer.CertificateCount == 0 || signer.Certificates == IntPtr.Zero)
                    throw new InvalidOperationException("Verified signer certificate is unavailable.");
                ProviderCertificate certificate = (ProviderCertificate)Marshal.PtrToStructure(
                    signer.Certificates, typeof(ProviderCertificate));
                if (certificate.Certificate == IntPtr.Zero)
                    throw new InvalidOperationException("Verified signer certificate is empty.");
                using (X509Certificate2 leaf = new X509Certificate2(certificate.Certificate)) {
                    outcome.Publisher = leaf.GetNameInfo(X509NameType.SimpleName, false);
                }
                return outcome;
            } finally {
                // Close provider state before freeing its input or releasing the caller's file guard.
                if (data.State != IntPtr.Zero) {
                    data.Action = 2;
                    WinVerifyTrust(new IntPtr(-1), ref action, ref data);
                }
                Marshal.FreeHGlobal(pointer);
                Marshal.FreeHGlobal(file.Path);
            }
        }
    }
}
'@
}

function Get-EvaluatorBootstrapVersion {
    param([Parameter(Mandatory)][string]$Path)
    $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    if ($version.FileMajorPart -ne 3 -or $version.FileMinorPart -lt 10 -or
        $version.IsPreRelease -or $version.ProductName -cne 'Python' -or
        -not [string]::Equals($version.OriginalFilename, 'python.exe', [StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }
    return [version]::new($version.FileMajorPart, $version.FileMinorPart, $version.FileBuildPart, $version.FilePrivatePart)
}

function Get-EvaluatorBootstrapCandidates {
    param([string[]]$ExcludedRoots = @())
    foreach ($registration in @(Get-EvaluatorBootstrapRegistrations)) {
        try {
            if (-not (Test-EvaluatorBootstrapPath -Path $registration.Python -Root $registration.Root -ExcludedRoots $ExcludedRoots)) {
                continue
            }
            $version = Get-EvaluatorBootstrapVersion -Path $registration.Python
            if ($null -eq $version) { continue }
            [pscustomobject]@{
                Python = [IO.Path]::GetFullPath($registration.Python)
                Root = [IO.Path]::GetFullPath($registration.Root)
                Version = $version
                Rank = $registration.Rank
            }
        } catch [IO.FileNotFoundException] {
            continue
        } catch [IO.DirectoryNotFoundException] {
            continue
        }
    }
}

function Resolve-EvaluatorBootstrapPython {
    param([string[]]$ExcludedRoots = @())
    $ErrorActionPreference = 'Stop'
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        return [pscustomobject]@{ Status = 'unavailable'; Reason = 'unsupported-platform'; Python = $null; Guard = $null }
    }
    [object[]]$candidates = @(Get-EvaluatorBootstrapCandidates -ExcludedRoots $ExcludedRoots)
    if ($candidates.Count -eq 0) {
        return [pscustomobject]@{ Status = 'unavailable'; Reason = 'no-independent-base'; Python = $null; Guard = $null }
    }
    $comparer = [Collections.Generic.Comparer[object]]::Create([Comparison[object]]{
        param($left, $right)
        $order = $right.Version.CompareTo($left.Version)
        if ($order -ne 0) { return $order }
        $order = [int]$left.Rank - [int]$right.Rank
        if ($order -ne 0) { return $order }
        return [StringComparer]::OrdinalIgnoreCase.Compare($left.Python, $right.Python)
    })
    [Array]::Sort($candidates, [Collections.IComparer]$comparer)
    Initialize-EvaluatorBootstrapTrust
    $machine = [CopilotExtensions.EvaluatorBootstrapTrustV1]::NativeMachine()
    if ($machine -eq 0) {
        return [pscustomobject]@{ Status = 'unavailable'; Reason = 'unsupported-architecture'; Python = $null; Guard = $null }
    }
    foreach ($excluded in $ExcludedRoots) {
        if ($excluded -and -not [CopilotExtensions.EvaluatorBootstrapTrustV1]::IsCanonicalDrive($excluded)) {
            return [pscustomobject]@{ Status = 'unavailable'; Reason = 'aliased-exclusion-drive'; Python = $null; Guard = $null }
        }
    }
    foreach ($candidate in $candidates) {
        $guard = $null
        try {
            if (-not [CopilotExtensions.EvaluatorBootstrapTrustV1]::IsCanonicalDrive($candidate.Python)) { continue }
            $guard = [IO.File]::Open($candidate.Python, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
            if (-not (Test-EvaluatorBootstrapPath -Path $candidate.Python -Root $candidate.Root -ExcludedRoots $ExcludedRoots)) {
                continue
            }
            $finalPath = [CopilotExtensions.EvaluatorBootstrapTrustV1]::FinalPath($guard.SafeFileHandle)
            if (-not [string]::Equals($finalPath, $candidate.Python, [StringComparison]::OrdinalIgnoreCase)) {
                continue
            }
            if ((Get-EvaluatorBootstrapVersion -Path $candidate.Python) -ne $candidate.Version) {
                throw 'Registered bootstrap executable changed during admission.'
            }
            if ([CopilotExtensions.EvaluatorBootstrapTrustV1]::ReadMachine($guard) -ne $machine) { continue }
            $trust = [CopilotExtensions.EvaluatorBootstrapTrustV1]::Verify($candidate.Python, $guard.SafeFileHandle)
            if ($trust.Status -ne 0 -or $trust.Publisher -cne 'Python Software Foundation') { continue }
            $selection = [pscustomobject]@{
                Status = 'selected'
                Reason = 'registered-signed-base'
                Python = $candidate.Python
                Guard = $guard
            }
            $guard = $null
            return $selection
        } catch [IO.FileNotFoundException] {
            continue
        } catch [IO.DirectoryNotFoundException] {
            continue
        } finally {
            if ($null -ne $guard) { $guard.Dispose() }
        }
    }
    return [pscustomobject]@{ Status = 'unavailable'; Reason = 'no-admitted-base'; Python = $null; Guard = $null }
}
