# Native counterpart of _directory.py: create the control root, never rewrite an existing ACL.
if (-not ('CopilotExtensions.NativeExecutionDirectory' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;

namespace CopilotExtensions {
    public static class NativeExecutionDirectory {
        [StructLayout(LayoutKind.Sequential)]
        private struct Attributes {
            public uint Length;
            public IntPtr Descriptor;
            [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateDirectory(string path, ref Attributes security);

        public static void Create(string path) {
            string sid;
            using (var identity = WindowsIdentity.GetCurrent()) sid = identity.User.Value;
            var security = new DirectorySecurity();
            security.SetSecurityDescriptorSddlForm(
                "O:" + sid + "D:P(A;OICI;FA;;;" + sid + ")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)");
            byte[] descriptor = security.GetSecurityDescriptorBinaryForm();
            var pinned = GCHandle.Alloc(descriptor, GCHandleType.Pinned);
            try {
                var attributes = new Attributes();
                attributes.Length = (uint)Marshal.SizeOf(typeof(Attributes));
                attributes.Descriptor = pinned.AddrOfPinnedObject();
                if (!CreateDirectory(path, ref attributes)) {
                    int error = Marshal.GetLastWin32Error();
                    if (error != 183) throw new Win32Exception(error);
                }
            } finally { pinned.Free(); }
        }
    }
}
'@
}

function New-NativeExecutionDirectory {
    param([Parameter(Mandatory)][string]$Root)
    if ($env:OS -ne 'Windows_NT') { throw 'Native execution directory creation requires Windows.' }
    if ([CopilotExtensions.NativeFoundationState]::CheckPath($Root, $true, $true)) { return }
    $anchor = [IO.Path]::GetPathRoot($Root)
    while ($Root.Length -gt $anchor.Length -and $Root.EndsWith('\')) {
        $Root = $Root.Substring(0, $Root.Length - 1)
    }
    $parent = [IO.Path]::GetDirectoryName($Root)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($parent, $true, $false)
    [CopilotExtensions.NativeExecutionDirectory]::Create($Root)
    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($Root, $true, $false)
}
