using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Threading;

internal static class BootstrapProbe
{
    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "leaf")
        {
            Thread.Sleep(120000);
            return 0;
        }
        if (args.Length == 1 && args[0] == "--version")
        {
            Console.WriteLine("uv 0.9.4");
            return 0;
        }
        using (var current = Process.GetCurrentProcess())
        using (var leaf = Process.Start(new ProcessStartInfo {
            FileName = current.MainModule.FileName, Arguments = "leaf",
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
        }))
        {
            leaf.StandardInput.Close();
            string value = string.Join(" ", new[] {
                current.Id.ToString(CultureInfo.InvariantCulture),
                current.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(CultureInfo.InvariantCulture),
                leaf.Id.ToString(CultureInfo.InvariantCulture),
                leaf.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(CultureInfo.InvariantCulture),
            });
            string marker = Environment.GetEnvironmentVariable("BOOTSTRAP_PROBE_MARKER");
            File.WriteAllText(marker + ".pending", value);
            File.Move(marker + ".pending", marker);
            Thread.Sleep(120000);
            return 0;
        }
    }
}
