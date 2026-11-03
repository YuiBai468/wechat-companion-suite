// StatusTool.cs
// Verification-only helper (NOT part of the launcher API). It lets a human/CI exercise the
// SessionBackend contract from the command line without writing any UI:
//
//   StatusTool.exe                          -> prints SessionBackend.StatusJson()
//   StatusTool.exe --status                 -> same
//   StatusTool.exe --api                    -> prints the shared contract signatures via reflection
//   StatusTool.exe --snapshot               -> prints read-only desktop state (cursor, foreground
//                                              HWND, screen, clipboard format count, session)
//   StatusTool.exe --launch <exe> [args...] -> calls SessionBackend.LaunchInChild and prints the
//                                              result; exits 1 when the launch fails
//
// It changes nothing: --snapshot only reads state, --launch only calls the guarded backend API.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;

namespace WeChatIsolatedDesktop
{
    internal static class StatusTool
    {
        private const int EXIT_OK = 0;
        private const int EXIT_FAILED = 1;
        private const int EXIT_BAD_ARGS = 3;

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int X;
            public int Y;
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool GetCursorPos(out POINT lpPoint);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int nIndex);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool OpenClipboard(IntPtr hWndNewOwner);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool CloseClipboard();

        [DllImport("user32.dll")]
        private static extern int CountClipboardFormats();

        [DllImport("user32.dll")]
        private static extern bool IsClipboardFormatAvailable(uint format);

        private static int Main(string[] args)
        {
            if (args == null || args.Length == 0)
            {
                Console.WriteLine(SessionBackend.StatusJson());
                return EXIT_OK;
            }

            string mode = args[0];
            if (string.Equals(mode, "--status", StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine(SessionBackend.StatusJson());
                return EXIT_OK;
            }

            if (string.Equals(mode, "--api", StringComparison.OrdinalIgnoreCase))
            {
                return PrintApi();
            }

            if (string.Equals(mode, "--snapshot", StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine(SnapshotJson());
                return EXIT_OK;
            }

            if (string.Equals(mode, "--launch", StringComparison.OrdinalIgnoreCase))
            {
                if (args.Length < 2)
                {
                    Console.Error.WriteLine("StatusTool: --launch needs an executable path.");
                    return EXIT_BAD_ARGS;
                }

                string executable = args[1];
                StringBuilder arguments = new StringBuilder();
                for (int i = 2; i < args.Length; i++)
                {
                    if (arguments.Length > 0)
                    {
                        arguments.Append(' ');
                    }
                    arguments.Append(args[i]);
                }

                try
                {
                    int pid = SessionBackend.LaunchInChild(executable, arguments.ToString(), null);
                    Console.WriteLine("LAUNCHED pid=" + pid.ToString(CultureInfo.InvariantCulture));
                    return EXIT_OK;
                }
                catch (Win32Exception ex)
                {
                    Console.WriteLine("LAUNCH-FAILED Win32Exception nativeError=" +
                        ex.NativeErrorCode.ToString(CultureInfo.InvariantCulture));
                    Console.WriteLine(ex.Message);
                    return EXIT_FAILED;
                }
                catch (Exception ex)
                {
                    Console.WriteLine("LAUNCH-FAILED " + ex.GetType().FullName);
                    Console.WriteLine(ex.Message);
                    return EXIT_FAILED;
                }
            }

            Console.Error.WriteLine("Unknown mode: " + mode);
            PrintUsage();
            return EXIT_BAD_ARGS;
        }

        private static void PrintUsage()
        {
            Console.Error.WriteLine("Usage: StatusTool.exe [--status | --api | --snapshot | --launch <exe> [args...]]");
        }

        private static int PrintApi()
        {
            Type type = typeof(SessionBackend);
            List<string> lines = new List<string>();
            MethodInfo[] methods = type.GetMethods(BindingFlags.Public | BindingFlags.Static);
            foreach (MethodInfo method in methods)
            {
                if (method.IsSpecialName)
                {
                    continue;
                }
                StringBuilder parameters = new StringBuilder();
                ParameterInfo[] parameterInfos = method.GetParameters();
                for (int i = 0; i < parameterInfos.Length; i++)
                {
                    if (i > 0)
                    {
                        parameters.Append(", ");
                    }
                    parameters.Append(parameterInfos[i].ParameterType.Name);
                }
                lines.Add(string.Format(
                    CultureInfo.InvariantCulture,
                    "{0} {1}({2})",
                    method.ReturnType.Name,
                    method.Name,
                    parameters.ToString()));
            }
            lines.Sort(StringComparer.Ordinal);
            foreach (string line in lines)
            {
                Console.WriteLine(line);
            }
            Console.WriteLine("assembly=" + type.Assembly.Location);
            return EXIT_OK;
        }

        private static string SnapshotJson()
        {
            int sessionId;
            try
            {
                sessionId = SessionBackend.GetCurrentSessionId();
            }
            catch (Exception)
            {
                sessionId = -1;
            }

            POINT cursor;
            bool cursorOk = GetCursorPos(out cursor);
            long foregroundHwnd = GetForegroundWindow().ToInt64();

            int clipboardFormatCount = 0;
            bool clipboardUnicodeText = false;
            bool clipboardOpened = OpenClipboard(IntPtr.Zero);
            if (clipboardOpened)
            {
                clipboardFormatCount = CountClipboardFormats();
                clipboardUnicodeText = IsClipboardFormatAvailable(13);
                CloseClipboard();
            }

            StringBuilder sb = new StringBuilder(512);
            sb.Append('{');
            sb.Append("\"sessionId\":").Append(sessionId.ToString(CultureInfo.InvariantCulture)).Append(',');
            sb.Append("\"cursor\":{");
            sb.Append("\"ok\":").Append(cursorOk ? "true" : "false").Append(',');
            sb.Append("\"x\":").Append((cursorOk ? cursor.X : -1).ToString(CultureInfo.InvariantCulture)).Append(',');
            sb.Append("\"y\":").Append((cursorOk ? cursor.Y : -1).ToString(CultureInfo.InvariantCulture));
            sb.Append("},");
            sb.Append("\"foregroundHwnd\":").Append(foregroundHwnd.ToString(CultureInfo.InvariantCulture)).Append(',');
            sb.Append("\"screen\":{");
            sb.Append("\"width\":").Append(GetSystemMetrics(0).ToString(CultureInfo.InvariantCulture)).Append(',');
            sb.Append("\"height\":").Append(GetSystemMetrics(1).ToString(CultureInfo.InvariantCulture));
            sb.Append("},");
            sb.Append("\"clipboard\":{");
            sb.Append("\"opened\":").Append(clipboardOpened ? "true" : "false").Append(',');
            sb.Append("\"formatCount\":").Append(clipboardFormatCount.ToString(CultureInfo.InvariantCulture)).Append(',');
            sb.Append("\"hasUnicodeText\":").Append(clipboardUnicodeText ? "true" : "false");
            sb.Append("},");
            sb.Append("\"generatedAtUtc\":\"")
                .Append(DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture))
                .Append("\"}");
            return sb.ToString();
        }
    }
}
