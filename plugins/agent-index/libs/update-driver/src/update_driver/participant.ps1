# Python-free owner of participant.py's version-1, pickle-free witness protocol.
if (-not ('CopilotExtensions.NativeParticipantWitness' -as [type])) {
    Add-Type -ReferencedAssemblies System.Core, System.Web.Extensions -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

namespace CopilotExtensions {
    public sealed class NativeParticipantWitness : IDisposable {
        private const int MaxFrame = 16384;
        private const int MaxParticipants = 16;
        private readonly object guard = new object();
        private readonly Action assertOwned;
        private readonly Thread acceptThread;
        private readonly HashSet<NamedPipeServerStream> connections = new HashSet<NamedPipeServerStream>();
        private readonly HashSet<Thread> handlers = new HashSet<Thread>();
        private readonly byte[] key = new byte[32];
        private readonly string root, requestId, name;
        private NamedPipeServerStream listener;
        private volatile bool active = true;
        private string failure;
        private bool unconfirmedExecution;

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool PeekNamedPipe(SafePipeHandle pipe, IntPtr buffer, uint length,
            IntPtr read, out uint available, IntPtr remaining);

        public NativeParticipantWitness(Action assertion, string root, string requestId) {
            assertOwned = assertion;
            assertOwned();
            this.root = root;
            this.requestId = requestId;
            name = "update-driver-" + Guid.NewGuid().ToString("N");
            using (var rng = RandomNumberGenerator.Create()) { rng.GetBytes(key); }
            listener = CreatePipe();
            acceptThread = new Thread(Accept);
            acceptThread.IsBackground = true;
            acceptThread.Start();
        }

        private NamedPipeServerStream CreatePipe() {
            var security = new PipeSecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,
                PipeAccessRights.FullControl, AccessControlType.Allow));
            return new NamedPipeServerStream(name, PipeDirection.InOut, NamedPipeServerStream.MaxAllowedServerInstances,
                PipeTransmissionMode.Message, PipeOptions.Asynchronous, MaxFrame, MaxFrame, security);
        }

        private static string Hex(byte[] data) {
            return BitConverter.ToString(data).Replace("-", "").ToLowerInvariant();
        }

        private static string Nonce() {
            var data = new byte[32];
            using (var rng = RandomNumberGenerator.Create()) { rng.GetBytes(data); }
            return Hex(data);
        }

        private string Mac(string label, string nonce) {
            using (var h = new HMACSHA256(key)) {
                return Hex(h.ComputeHash(Encoding.ASCII.GetBytes(label + nonce)));
            }
        }

        private static string Text(Dictionary<string, object> frame, string key) {
            object value;
            return frame.TryGetValue(key, out value) ? value as string : null;
        }

        private static bool Equal(string left, string right) {
            if (left == null || right == null || left.Length != right.Length) return false;
            int difference = 0;
            for (int i = 0; i < left.Length; i++) difference |= left[i] ^ right[i];
            return difference == 0;
        }

        private static void Send(NamedPipeServerStream pipe, Dictionary<string, object> frame) {
            byte[] data = Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(frame));
            var write = pipe.WriteAsync(data, 0, data.Length);
            // Task-owned handles must survive a deadline until I/O completes or is cancelled.
            if (!((IAsyncResult)write).AsyncWaitHandle.WaitOne(2000))
                throw new TimeoutException("native participant write deadline expired");
            write.GetAwaiter().GetResult();
        }

        private static Dictionary<string, object> Receive(NamedPipeServerStream pipe) {
            var data = new byte[MaxFrame + 1];
            var read = pipe.ReadAsync(data, 0, data.Length);
            if (!((IAsyncResult)read).AsyncWaitHandle.WaitOne(2000))
                throw new TimeoutException("native participant read deadline expired");
            int count = read.GetAwaiter().GetResult();
            if (count == 0 || count > MaxFrame || !pipe.IsMessageComplete)
                throw new InvalidDataException("invalid native participant message");
            var serializer = new JavaScriptSerializer();
            serializer.MaxJsonLength = MaxFrame;
            serializer.RecursionLimit = 8;
            Dictionary<string, object> frame;
            try {
                frame = serializer.Deserialize<Dictionary<string, object>>(
                    new UTF8Encoding(false, true).GetString(data, 0, count));
            } catch (MemberAccessException error) {
                // Malformed __type values can trigger reflection errors inside this serializer.
                throw new InvalidDataException("invalid native participant type metadata", error);
            }
            if (frame == null) throw new InvalidDataException("invalid native participant frame");
            return frame;
        }

        private void Accept() {
            try {
                while (active) {
                    NamedPipeServerStream pipe;
                    lock (guard) { pipe = listener; }
                    pipe.WaitForConnection();
                    lock (guard) {
                        if (!active) return;
                        // Keep a listening instance present while an authenticated client is joined.
                        listener = CreatePipe();
                        if (connections.Count >= MaxParticipants) {
                            pipe.Dispose();
                            continue;
                        }
                        connections.Add(pipe);
                        var handler = new Thread(delegate() { Serve(pipe); });
                        handler.IsBackground = true;
                        handlers.Add(handler);
                        handler.Start();
                    }
                }
            } catch (Exception error) {
                lock (guard) {
                    if (active) {
                        failure = "native participant accept failed: " + error.GetType().Name;
                        active = false;
                    }
                }
            }
        }

        private void Serve(NamedPipeServerStream pipe) {
            int executionDepth = 0;
            try {
                assertOwned();
                pipe.ReadMode = PipeTransmissionMode.Message;
                string server = Nonce();
                Send(pipe, new Dictionary<string, object> {{"nonce", server}});
                var proof = Receive(pipe);
                string client = Text(proof, "nonce");
                if (client == null || client.Length != 64 ||
                    !Equal(Text(proof, "proof"), Mac("client:", server))) return;
                assertOwned();
                Send(pipe, new Dictionary<string, object> {{"proof", Mac("server:", client)}});
                while (active) {
                    uint available;
                    if (!PeekNamedPipe(pipe.SafePipeHandle, IntPtr.Zero, 0, IntPtr.Zero,
                        out available, IntPtr.Zero)) return;
                    if (available == 0) { Thread.Sleep(10); continue; }
                    var frame = Receive(pipe);
                    string nonce = Text(frame, "nonce");
                    bool valid = Text(frame, "root") == root &&
                        Text(frame, "request_id") == requestId && nonce != null && nonce.Length == 64;
                    assertOwned();
                    object execution;
                    frame.TryGetValue("execution", out execution);
                    lock (guard) {
                        valid = valid && active && (!unconfirmedExecution || (execution as string) == "end");
                        if (valid && execution != null) {
                            if ((execution as string) == "begin" && executionDepth < Int32.MaxValue)
                                ++executionDepth;
                            else if ((execution as string) == "end" && executionDepth > 0)
                                --executionDepth;
                            else valid = false;
                        }
                    }
                    var response = new Dictionary<string, object> {{"valid", valid}, {"nonce", nonce}};
                    if (valid && execution != null) response["execution"] = execution;
                    Send(pipe, response);
                    object close;
                    if (!valid || (frame.TryGetValue("close", out close) && close is bool && (bool)close))
                        return;
                }
            } catch (IOException) {
                // Disconnected or malformed clients cannot retire genuine custody.
            } catch (InvalidDataException) {
            } catch (TimeoutException) {
            } catch (ArgumentException) {
            } catch (InvalidOperationException) {
                // Includes a revoked assertion after the actual operation handle closes.
            } catch (Exception error) {
                lock (guard) {
                    if (active) {
                        failure = "native participant handler failed: " + error.GetType().Name;
                        active = false;
                    }
                }
            } finally {
                lock (guard) {
                    if (executionDepth != 0) unconfirmedExecution = true;
                    connections.Remove(pipe);
                    handlers.Remove(Thread.CurrentThread);
                }
                pipe.Dispose();
            }
        }

        public string Environment() {
            lock (guard) {
                assertOwned();
                if (!active) throw new InvalidOperationException(failure ?? "native participant witness is closed");
                return new JavaScriptSerializer().Serialize(new Dictionary<string, object> {
                    {"version", 1}, {"family", "AF_PIPE"}, {"address", @"\\.\pipe\" + name},
                    {"key", Hex(key)}, {"root", root}, {"request_id", requestId}
                });
            }
        }

        public void Dispose() {
            Thread[] pending;
            lock (guard) {
                active = false;
                listener.Dispose();
                foreach (var pipe in connections) pipe.Dispose();
                pending = new Thread[handlers.Count];
                handlers.CopyTo(pending);
            }
            bool settled = acceptThread.Join(3000);
            foreach (var thread in pending) settled = thread.Join(3000) && settled;
            if (!settled) throw new InvalidOperationException("native participant threads did not settle");
            if (unconfirmedExecution)
                throw new InvalidOperationException("native participant execution cleanup is unconfirmed");
            if (failure != null) throw new InvalidOperationException(failure);
        }
    }
}
'@
}

function New-NativeParticipantWitness {
    param(
        [Parameter(Mandatory)]$Custody,
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$RequestId
    )
    if ($env:OS -ne 'Windows_NT') { throw 'Native participant custody requires Windows.' }
    if (-not ($Custody -is [CopilotExtensions.NativeOperationLock])) {
        throw 'Native participant witness requires actual operation custody.'
    }
    if (-not [IO.Path]::IsPathRooted($Root) -or [IO.Path]::GetFullPath($Root) -cne $Root) {
        throw 'Native participant root requires a normalized absolute path.'
    }
    $anchor = [IO.Path]::GetPathRoot($Root)
    while ($Root.Length -gt $anchor.Length -and $Root.EndsWith('\')) {
        $Root = $Root.Substring(0, $Root.Length - 1)
    }
    if ($RequestId -cnotmatch '^[0-9a-f]{32}$') { throw 'Invalid native participant operation identity.' }
    $assertion = $Custody.CreateWitnessAssertion([IO.Path]::Combine($Root, 'execution.lock'))
    return New-Object CopilotExtensions.NativeParticipantWitness -ArgumentList $assertion, $Root, $RequestId
}
