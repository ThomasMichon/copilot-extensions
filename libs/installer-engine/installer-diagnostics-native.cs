using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class InstallerDiagnosticsPipeV1 {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool PeekNamedPipe(SafeFileHandle handle, IntPtr buffer,
        uint size, IntPtr read, out uint available, IntPtr remaining);
    [DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int number);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern uint GetFileType(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetConsoleMode(IntPtr handle, out uint mode);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetNamedPipeInfo(IntPtr handle, out uint flags,
        out uint output, out uint input, out uint instances);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetNamedPipeHandleState(IntPtr handle, out uint mode,
        IntPtr instances, IntPtr collection, IntPtr timeout, IntPtr user, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetNamedPipeHandleState(IntPtr handle, ref uint mode, IntPtr collection, IntPtr timeout);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool WriteFile(IntPtr handle, byte[] data, uint count, out uint written, IntPtr overlapped);
    static readonly object Gate = new object();
    public static int Available(SafeFileHandle handle) {
        uint available;
        if (PeekNamedPipe(handle,IntPtr.Zero,0,IntPtr.Zero,out available,IntPtr.Zero))
            return checked((int)available);
        int error=Marshal.GetLastWin32Error();
        if (error==109 || error==232) return 0;
        throw new Win32Exception(error);
    }
    static Win32Exception Failure(string operation, int code) {
        var error=new Win32Exception(code);
        error.Data["operation"]=operation;
        return error;
    }
    static uint QueryMode(IntPtr handle, string operation) {
        uint mode;
        if (!GetNamedPipeHandleState(handle,out mode,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,0))
            throw Failure(operation,Marshal.GetLastWin32Error());
        return mode;
    }
    public static uint Mode(IntPtr handle) { return QueryMode(handle,"mode-query"); }
    internal static bool Scope(IntPtr handle, bool pipe, Action action) {
        lock (Gate) {
            uint before=0;
            bool changed=false;
            try {
                if (pipe) {
                    before=Mode(handle);
                    uint mode=before|1u;
                    if (!SetNamedPipeHandleState(handle,ref mode,IntPtr.Zero,IntPtr.Zero)) {
                        int error=Marshal.GetLastWin32Error();
                        // Only this operation's BUSY, with positively unchanged
                        // mode and no write issued, is deferred backpressure.
                        if (error==231 && QueryMode(handle,"busy-mode-query")==before) return false;
                        throw Failure("mode-set",error);
                    }
                    changed=true;
                }
                action();
                return true;
            } finally {
                // Duplicated/inherited write handles share mode: restore before
                // returning to PowerShell, never leave NOWAIT enabled between calls.
                if (changed) {
                    uint mode=before;
                    if (!SetNamedPipeHandleState(handle,ref mode,IntPtr.Zero,IntPtr.Zero))
                        throw Failure("mode-restore",Marshal.GetLastWin32Error());
                    if (QueryMode(handle,"restore-query")!=before)
                        throw new IOException("presentation pipe mode restoration mismatch");
                }
            }
        }
    }
    internal static int WriteBytes(IntPtr handle, byte[] data) {
        uint written;
        if (!WriteFile(handle,data,(uint)data.Length,out written,IntPtr.Zero))
            throw Failure("write",Marshal.GetLastWin32Error());
        if (written>data.Length) throw new IOException("invalid presentation write count");
        return checked((int)written);
    }
    public static int Write(IntPtr handle, byte[] data, bool pipe) {
        int written=0;
        Scope(handle,pipe,delegate { written=WriteBytes(handle,data); });
        return written;
    }
}
public sealed class InstallerDiagnosticOutput {
    public const int Limit=262144, Chunk=1024, Work=16384;
    readonly Queue<byte[]> queue=new Queue<byte[]>();
    readonly TextWriter writer;
    readonly IntPtr handle;
    readonly uint type;
    readonly Encoder encoder;
    int offset;
    public int Pending { get; private set; }
    public int Peak { get; private set; }
    public long Dropped { get; private set; }
    public long Written { get; private set; }
    public long ZeroWrites { get; private set; }
    public long Calls { get; private set; }
    public long BusyModes { get; private set; }
    public long NativeFailures { get; private set; }
    public string Loss { get; private set; }
    public InstallerDiagnosticOutput(int number, TextWriter destination) {
        writer=destination; encoder=destination.Encoding.GetEncoder();
        handle=InstallerDiagnosticsPipeV1.GetStdHandle(number);
        type=InstallerDiagnosticsPipeV1.GetFileType(handle);
        if (type!=1 && type!=2 && type!=3) Disable("unsupported-destination");
        uint mode,flags,output,input,instances;
        if (type==2 && !InstallerDiagnosticsPipeV1.GetConsoleMode(handle,out mode))
            Disable("unsupported-character-device");
        if (type==3 && (!InstallerDiagnosticsPipeV1.GetNamedPipeInfo(
                handle,out flags,out output,out input,out instances) || (flags&4u)!=0))
            Disable("unsupported-pipe");
    }
    public void Disable(string reason) {
        if (Loss==null) Loss=reason;
        Dropped+=Pending; Pending=0; offset=0; queue.Clear();
    }
    public bool CanAccept(int characters) {
        return Loss==null && writer.Encoding.GetMaxByteCount(characters)<=Limit-Pending;
    }
    public void Add(char[] text, int count, bool final) {
        try {
            if (type==2 && Loss==null) {
                writer.Write(text,0,count); writer.Flush(); return;
            }
            byte[] data=new byte[writer.Encoding.GetMaxByteCount(count)];
            int length=encoder.GetBytes(text,0,count,data,0,final);
            if (Loss!=null) { Dropped+=length; return; }
            if (length>Limit-Pending) { Dropped+=length; Disable("queue-saturated"); return; }
            if (length==0) return;
            Array.Resize(ref data,length); queue.Enqueue(data); Pending+=length; Peak=Math.Max(Peak,Pending);
        } catch { Disable("encoding-failed"); }
    }
    public void Pump() {
        if (Loss!=null || Pending==0) return;
        try {
            bool entered=InstallerDiagnosticsPipeV1.Scope(handle,type==3,delegate {
                int budget=Work, attempts=Work/Chunk;
                while (Pending>0 && budget>0 && attempts-- > 0) {
                    byte[] head=queue.Peek();
                    int count=Math.Min(Math.Min(head.Length-offset,Chunk),budget);
                    byte[] data=new byte[count]; Array.Copy(head,offset,data,0,count);
                    Calls++;
                    int written=InstallerDiagnosticsPipeV1.WriteBytes(handle,data);
                    if (written==0) { ZeroWrites++; break; }
                    Written+=written; offset+=written; Pending-=written; budget-=written;
                    if (offset==head.Length) { queue.Dequeue(); offset=0; }
                }
            });
            if (!entered) BusyModes++;
        } catch (Win32Exception error) {
            NativeFailures++;
            Disable("native-"+error.Data["operation"]+"-"+error.NativeErrorCode);
        } catch (Exception error) {
            NativeFailures++;
            Disable("presentation-"+error.GetType().Name);
        }
    }
}
