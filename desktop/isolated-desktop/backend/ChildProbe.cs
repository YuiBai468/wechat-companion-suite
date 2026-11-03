// ChildProbe.cs
// Isolated-desktop verification probe.
//
// Target: .NET Framework 4.8, C# 5 (in-box csc.exe /langversion:5). Console program.
//
// Usage:
//   ChildProbe.exe --parent-session <N> --out <absolute-json-path>
//
// Behaviour:
//   * First action: resolve the current process session and compare it with --parent-session.
//     If they are equal, the probe REFUSES to run (exit code 2, nothing written to disk, no mouse
//     or clipboard call is made). This guard exists so the probe can never touch the parent desktop.
//   * Only when the current session differs from the parent session it performs a few small,
//     reversible mouse moves, records cursor / foreground HWND / screen size / session id, and runs
//     a READ-ONLY clipboard probe (format ids and availability only; clipboard data is never read,
//     and the clipboard is never written). No keyboard injection, no window automation, no other
//     software is touched.
//   * The result is only considered successful ("ok": true, exit 0) when ALL of the following hold:
//       - the initial GetCursorPos succeeded (if it fails the probe fails immediately and does NOT
//         move the cursor at all);
//       - the screen and virtual-screen dimensions are valid (> 0);
//       - all 4 small mouse moves report success AND the recorded actual cursor position equals the
//         requested target position for every move;
//       - the original cursor position was restored successfully and is confirmed by a final read.
//     Otherwise "ok": false and exit code 1 (a failure JSON is still written to --out).
//   * The JSON result is written only to the exact --out path; stdout is kept empty.
//
// Exit codes:
//   0  probe completed and all success criteria met, JSON written with "ok": true
//   1  probe ran but a success criterion failed, or an unexpected runtime error
//      (JSON written with "ok": false; best effort when an exception occurs)
//   2  refused: current session == --parent-session (nothing written)
//   3  invalid arguments (nothing written)
//   4  cannot determine current session / session 0 (nothing written)
//
// This program is self-contained (no dependency on SessionBackend.dll) so it can be copied into a
// child session on its own.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace WeChatIsolatedDesktop
{
    internal sealed class MoveRecord
    {
        public int Dx;
        public int Dy;
        public int TargetX;
        public int TargetY;
        public bool SetOk;
        public int SetError;
        public bool ReadOk;
        public int ActualX;
        public int ActualY;
    }

    internal sealed class ProbeEvidence
    {
        public int ParentSessionArg;
        public int SessionId;
        public int ProcessId;
        public string WindowStation;
        public string Desktop;

        public int ScreenWidth;
        public int ScreenHeight;
        public int VirtualX;
        public int VirtualY;
        public int VirtualWidth;
        public int VirtualHeight;

        public bool InitialCursorOk;
        public int InitialCursorError;
        public int InitialX;
        public int InitialY;
        public bool Restored;
        public bool FinalCursorOk;
        public int FinalX;
        public int FinalY;

        public List<MoveRecord> Moves = new List<MoveRecord>();

        public long ForegroundBeforeHwnd;
        public long ForegroundAfterHwnd;

        public bool ClipboardOpened;
        public int ClipboardError;
        public int ClipboardFormatCount;
        public List<uint> ClipboardFormats = new List<uint>();
        public bool ClipboardHasUnicodeText;
        public bool ClipboardHasText;
        public bool ClipboardHasFileDrop;

        public DateTime StartedUtc;
        public DateTime FinishedUtc;
    }

    internal sealed class ProbeEvaluation
    {
        public bool Ok;
        public List<string> FailureReasons = new List<string>();

        public void Fail(string reason)
        {
            FailureReasons.Add(reason);
            Ok = false;
        }
    }

    internal sealed class ProbeRun
    {
        public bool Ok;
        public string Json;

        public ProbeRun(bool ok, string json)
        {
            Ok = ok;
            Json = json;
        }
    }

    internal static class ChildProbe
    {
        private const int EXIT_OK = 0;
        private const int EXIT_ERROR = 1;
        private const int EXIT_REFUSED = 2;
        private const int EXIT_BAD_ARGS = 3;
        private const int EXIT_NO_SESSION = 4;

        internal const int ExpectedMoveCount = 4;

        private const int SM_CXSCREEN = 0;
        private const int SM_CYSCREEN = 1;
        private const int SM_XVIRTUALSCREEN = 76;
        private const int SM_YVIRTUALSCREEN = 77;
        private const int SM_CXVIRTUALSCREEN = 78;
        private const int SM_CYVIRTUALSCREEN = 79;

        private const uint CF_TEXT = 1;
        private const uint CF_UNICODETEXT = 13;
        private const uint CF_HDROP = 15;
        private const int UOI_NAME = 2;

        private const int MAX_CLIPBOARD_FORMATS = 64;

        #region Win32

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int X;
            public int Y;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ProcessIdToSessionId(uint dwProcessId, out uint pSessionId);

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentProcessId();

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool GetCursorPos(out POINT lpPoint);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool SetCursorPos(int x, int y);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int nIndex);

        [DllImport("user32.dll")]
        private static extern IntPtr GetProcessWindowStation();

        [DllImport("user32.dll")]
        private static extern IntPtr GetThreadDesktop(uint dwThreadId);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "GetUserObjectInformationW")]
        private static extern bool GetUserObjectInformationW(
            IntPtr hObj, int nIndex, StringBuilder pvInfo, uint nLength, out uint lpnLengthNeeded);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool OpenClipboard(IntPtr hWndNewOwner);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool CloseClipboard();

        [DllImport("user32.dll")]
        private static extern int CountClipboardFormats();

        [DllImport("user32.dll")]
        private static extern uint EnumClipboardFormats(uint format);

        [DllImport("user32.dll")]
        private static extern bool IsClipboardFormatAvailable(uint format);

        #endregion

        private static int Main(string[] args)
        {
            int parentSession = -1;
            string outPath = null;

            if (!TryParseArguments(args, out parentSession, out outPath))
            {
                PrintUsage();
                return EXIT_BAD_ARGS;
            }

            // ---- GUARD: must be the very first real action. Nothing is written or probed if it fails.
            uint currentSession;
            if (!ProcessIdToSessionId(GetCurrentProcessId(), out currentSession))
            {
                Console.Error.WriteLine(
                    "ChildProbe: cannot determine current session (Win32 error " +
                    Marshal.GetLastWin32Error().ToString(CultureInfo.InvariantCulture) + "); aborting.");
                return EXIT_NO_SESSION;
            }

            if (currentSession == 0)
            {
                Console.Error.WriteLine("ChildProbe: current session is 0 (non-interactive); aborting.");
                return EXIT_NO_SESSION;
            }

            if (unchecked((int)currentSession) == parentSession)
            {
                Console.Error.WriteLine(string.Format(
                    CultureInfo.InvariantCulture,
                    "ChildProbe REFUSED: current session {0} equals --parent-session {0}. "
                    + "The probe never moves the parent cursor or touches the parent clipboard.",
                    parentSession));
                return EXIT_REFUSED; // nothing written on purpose
            }

            if (outPath == null || !Path.IsPathRooted(outPath))
            {
                Console.Error.WriteLine("ChildProbe: --out must be an absolute path; aborting.");
                return EXIT_BAD_ARGS;
            }
            string outDirectory = Path.GetDirectoryName(outPath);
            if (string.IsNullOrEmpty(outDirectory) || !Directory.Exists(outDirectory))
            {
                Console.Error.WriteLine("ChildProbe: directory of --out does not exist: " + outDirectory);
                return EXIT_BAD_ARGS;
            }

            ProbeRun run;
            try
            {
                run = RunProbe(parentSession, unchecked((int)currentSession));
            }
            catch (Exception ex)
            {
                string errorJson = BuildErrorJson(parentSession, unchecked((int)currentSession), ex);
                try
                {
                    File.WriteAllText(outPath, errorJson, new UTF8Encoding(false));
                }
                catch (Exception)
                {
                    // writing the error result is best effort
                }
                Console.Error.WriteLine("ChildProbe: probe failed: " + ex.GetType().Name + ": " + ex.Message);
                return EXIT_ERROR;
            }

            try
            {
                File.WriteAllText(outPath, run.Json, new UTF8Encoding(false));
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("ChildProbe: cannot write --out: " + ex.GetType().Name + ": " + ex.Message);
                return EXIT_ERROR;
            }

            return run.Ok ? EXIT_OK : EXIT_ERROR;
        }

        #region argument parsing / usage

        private static bool TryParseArguments(string[] args, out int parentSession, out string outPath)
        {
            parentSession = -1;
            outPath = null;
            if (args == null || args.Length == 0)
            {
                return false;
            }

            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                if (string.Equals(arg, "--parent-session", StringComparison.OrdinalIgnoreCase))
                {
                    if (i + 1 >= args.Length)
                    {
                        return false;
                    }
                    int parsed;
                    if (!int.TryParse(args[++i], NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed))
                    {
                        return false;
                    }
                    if (parsed <= 0)
                    {
                        return false;
                    }
                    parentSession = parsed;
                }
                else if (string.Equals(arg, "--out", StringComparison.OrdinalIgnoreCase))
                {
                    if (i + 1 >= args.Length)
                    {
                        return false;
                    }
                    outPath = args[++i];
                    if (outPath == null || outPath.Length == 0)
                    {
                        return false;
                    }
                }
                else if (string.Equals(arg, "--help", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(arg, "-h", StringComparison.OrdinalIgnoreCase) ||
                         string.Equals(arg, "/?", StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
                else
                {
                    return false;
                }
            }

            return parentSession > 0 && outPath != null;
        }

        private static void PrintUsage()
        {
            Console.Error.WriteLine("Usage: ChildProbe.exe --parent-session <N> --out <absolute-json-path>");
            Console.Error.WriteLine(
                "Exit codes: 0 ok (all success criteria met), 1 probe/result failure, "
                + "2 refused (parent session), 3 bad args, 4 no session.");
        }

        #endregion

        #region probe execution

        private static ProbeRun RunProbe(int parentSession, int currentSession)
        {
            ProbeEvidence evidence = new ProbeEvidence();
            evidence.ParentSessionArg = parentSession;
            evidence.SessionId = currentSession;
            evidence.ProcessId = unchecked((int)GetCurrentProcessId());
            evidence.StartedUtc = DateTime.UtcNow;

            evidence.ScreenWidth = GetSystemMetrics(SM_CXSCREEN);
            evidence.ScreenHeight = GetSystemMetrics(SM_CYSCREEN);
            evidence.VirtualX = GetSystemMetrics(SM_XVIRTUALSCREEN);
            evidence.VirtualY = GetSystemMetrics(SM_YVIRTUALSCREEN);
            evidence.VirtualWidth = GetSystemMetrics(SM_CXVIRTUALSCREEN);
            evidence.VirtualHeight = GetSystemMetrics(SM_CYVIRTUALSCREEN);

            POINT initialCursor;
            evidence.InitialCursorOk = GetCursorPos(out initialCursor);
            evidence.InitialCursorError = evidence.InitialCursorOk ? 0 : Marshal.GetLastWin32Error();
            evidence.InitialX = initialCursor.X;
            evidence.InitialY = initialCursor.Y;

            if (!evidence.InitialCursorOk)
            {
                // MANDATORY: fail immediately, before any SetCursorPos call. Mouse must not move.
                ProbeEvaluation failed = EvaluateProbe(evidence);
                evidence.FinishedUtc = DateTime.UtcNow;
                return new ProbeRun(false, BuildProbeJson(evidence, failed));
            }

            evidence.ForegroundBeforeHwnd = GetForegroundWindow().ToInt64();

            int[][] deltas = new int[][]
            {
                new int[] { 1, 1 },
                new int[] { -1, -1 },
                new int[] { 3, 0 },
                new int[] { -3, 0 }
            };

            for (int i = 0; i < deltas.Length && i < ExpectedMoveCount; i++)
            {
                MoveRecord move = new MoveRecord();
                move.Dx = deltas[i][0];
                move.Dy = deltas[i][1];
                move.TargetX = evidence.InitialX + move.Dx;
                move.TargetY = evidence.InitialY + move.Dy;

                if (evidence.VirtualWidth > 0 && evidence.VirtualHeight > 0)
                {
                    move.TargetX = Clamp(move.TargetX, evidence.VirtualX, evidence.VirtualX + evidence.VirtualWidth - 1);
                    move.TargetY = Clamp(move.TargetY, evidence.VirtualY, evidence.VirtualY + evidence.VirtualHeight - 1);
                }

                move.SetOk = SetCursorPos(move.TargetX, move.TargetY);
                move.SetError = move.SetOk ? 0 : Marshal.GetLastWin32Error();

                POINT after;
                move.ReadOk = GetCursorPos(out after);
                move.ActualX = after.X;
                move.ActualY = after.Y;

                evidence.Moves.Add(move);
                System.Threading.Thread.Sleep(15);
            }

            // Restore the original cursor position (important: still inside the child session only).
            bool restoredSetOk = false;
            bool restored = false;
            if (evidence.InitialCursorOk)
            {
                restoredSetOk = SetCursorPos(evidence.InitialX, evidence.InitialY);
                POINT verify;
                bool verifyReadOk = GetCursorPos(out verify);
                restored = restoredSetOk && verifyReadOk &&
                    verify.X == evidence.InitialX && verify.Y == evidence.InitialY;
            }
            evidence.Restored = restored;

            POINT finalCursor;
            evidence.FinalCursorOk = GetCursorPos(out finalCursor);
            evidence.FinalX = finalCursor.X;
            evidence.FinalY = finalCursor.Y;

            evidence.ForegroundAfterHwnd = GetForegroundWindow().ToInt64();

            ProbeClipboardReadOnly(evidence);

            evidence.WindowStation = GetWindowStationName();
            evidence.Desktop = GetDesktopName();
            evidence.FinishedUtc = DateTime.UtcNow;

            ProbeEvaluation evaluation = EvaluateProbe(evidence);
            return new ProbeRun(evaluation.Ok, BuildProbeJson(evidence, evaluation));
        }

        /// <summary>
        /// Pure result judgment (no Win32 calls): true only when the evidence proves a fully
        /// successful probe. Extracted so failing evidence can be validated without touching the
        /// parent desktop (see verify/ProbeLogicTest.cs).
        /// </summary>
        internal static ProbeEvaluation EvaluateProbe(ProbeEvidence evidence)
        {
            ProbeEvaluation evaluation = new ProbeEvaluation();

            if (!evidence.InitialCursorOk)
            {
                evaluation.Fail("initial GetCursorPos failed (Win32 error " +
                    evidence.InitialCursorError.ToString(CultureInfo.InvariantCulture) +
                    "); the probe aborted immediately and did not move the cursor");
                return evaluation;
            }

            if (evidence.ScreenWidth <= 0 || evidence.ScreenHeight <= 0)
            {
                evaluation.Fail(string.Format(
                    CultureInfo.InvariantCulture,
                    "invalid screen size: {0}x{1}",
                    evidence.ScreenWidth, evidence.ScreenHeight));
            }

            if (evidence.VirtualWidth <= 0 || evidence.VirtualHeight <= 0)
            {
                evaluation.Fail(string.Format(
                    CultureInfo.InvariantCulture,
                    "invalid virtual screen size: {0}x{1} at ({2},{3})",
                    evidence.VirtualWidth, evidence.VirtualHeight, evidence.VirtualX, evidence.VirtualY));
            }

            if (evidence.Moves.Count != ExpectedMoveCount)
            {
                evaluation.Fail(string.Format(
                    CultureInfo.InvariantCulture,
                    "expected {0} mouse moves, recorded {1}",
                    ExpectedMoveCount, evidence.Moves.Count));
            }

            for (int i = 0; i < evidence.Moves.Count; i++)
            {
                MoveRecord move = evidence.Moves[i];
                if (!move.SetOk)
                {
                    evaluation.Fail(string.Format(
                        CultureInfo.InvariantCulture,
                        "move[{0}] SetCursorPos({1},{2}) failed (Win32 error {3})",
                        i, move.TargetX, move.TargetY, move.SetError));
                    continue;
                }
                if (!move.ReadOk)
                {
                    evaluation.Fail(string.Format(
                        CultureInfo.InvariantCulture,
                        "move[{0}] GetCursorPos after the move failed",
                        i));
                    continue;
                }
                if (move.ActualX != move.TargetX || move.ActualY != move.TargetY)
                {
                    evaluation.Fail(string.Format(
                        CultureInfo.InvariantCulture,
                        "move[{0}] did not reach the target: requested ({1},{2}), actual ({3},{4})",
                        i, move.TargetX, move.TargetY, move.ActualX, move.ActualY));
                }
            }

            if (!evidence.Restored)
            {
                evaluation.Fail("the original cursor position was not restored/confirmed");
            }

            if (!evidence.FinalCursorOk)
            {
                evaluation.Fail("final GetCursorPos failed");
            }
            else if (evidence.FinalX != evidence.InitialX || evidence.FinalY != evidence.InitialY)
            {
                evaluation.Fail(string.Format(
                    CultureInfo.InvariantCulture,
                    "final cursor position ({0},{1}) is not the original ({2},{3})",
                    evidence.FinalX, evidence.FinalY, evidence.InitialX, evidence.InitialY));
            }

            evaluation.Ok = evaluation.FailureReasons.Count == 0;
            return evaluation;
        }

        private static void ProbeClipboardReadOnly(ProbeEvidence evidence)
        {
            try
            {
                evidence.ClipboardOpened = OpenClipboard(IntPtr.Zero);
                evidence.ClipboardError = evidence.ClipboardOpened ? 0 : Marshal.GetLastWin32Error();
                if (!evidence.ClipboardOpened)
                {
                    return;
                }

                evidence.ClipboardFormatCount = CountClipboardFormats();
                uint format = 0;
                while (evidence.ClipboardFormats.Count < MAX_CLIPBOARD_FORMATS)
                {
                    format = EnumClipboardFormats(format);
                    if (format == 0)
                    {
                        break;
                    }
                    evidence.ClipboardFormats.Add(format);
                }
                evidence.ClipboardHasUnicodeText = IsClipboardFormatAvailable(CF_UNICODETEXT);
                evidence.ClipboardHasText = IsClipboardFormatAvailable(CF_TEXT);
                evidence.ClipboardHasFileDrop = IsClipboardFormatAvailable(CF_HDROP);
                CloseClipboard();
            }
            catch (Exception ex)
            {
                evidence.ClipboardOpened = false;
                evidence.ClipboardError = -1;
                Console.Error.WriteLine("ChildProbe: clipboard probe skipped: " + ex.Message);
            }
        }

        private static int Clamp(int value, int min, int max)
        {
            if (value < min)
            {
                return min;
            }
            if (value > max)
            {
                return max;
            }
            return value;
        }

        private static string GetWindowStationName()
        {
            try
            {
                return GetUserObjectName(GetProcessWindowStation());
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static string GetDesktopName()
        {
            try
            {
                return GetUserObjectName(GetThreadDesktop(GetCurrentThreadId()));
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static string GetUserObjectName(IntPtr handle)
        {
            if (handle == IntPtr.Zero)
            {
                return null;
            }
            StringBuilder buffer = new StringBuilder(256);
            uint needed;
            if (!GetUserObjectInformationW(
                    handle, UOI_NAME, buffer, unchecked((uint)(buffer.Capacity * 2)), out needed))
            {
                return null;
            }
            return buffer.ToString();
        }

        #endregion

        #region JSON output

        internal static string BuildProbeJson(ProbeEvidence evidence, ProbeEvaluation evaluation)
        {
            StringBuilder sb = new StringBuilder(2560);
            sb.Append('{');
            AppendJsonString(sb, "tool", "ChildProbe").Append(',');
            AppendJsonString(sb, "version", "1.1").Append(',');
            AppendJsonBool(sb, "ok", evaluation.Ok).Append(',');
            sb.Append("\"failureReasons\":[");
            for (int i = 0; i < evaluation.FailureReasons.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                AppendJsonQuoted(sb, evaluation.FailureReasons[i]);
            }
            sb.Append("],");
            AppendJsonString(sb, "generatedAtUtc",
                evidence.StartedUtc.ToString("o", CultureInfo.InvariantCulture)).Append(',');
            AppendJsonBool(sb, "refused", false).Append(',');
            AppendJsonBool(sb, "guardPassed", true).Append(',');
            AppendJsonInt(sb, "parentSessionArg", evidence.ParentSessionArg).Append(',');
            AppendJsonInt(sb, "sessionId", evidence.SessionId).Append(',');
            AppendJsonInt(sb, "processId", evidence.ProcessId).Append(',');
            AppendJsonString(sb, "windowStation", evidence.WindowStation).Append(',');
            AppendJsonString(sb, "desktop", evidence.Desktop).Append(',');

            sb.Append("\"screen\":{");
            AppendJsonInt(sb, "width", evidence.ScreenWidth).Append(',');
            AppendJsonInt(sb, "height", evidence.ScreenHeight).Append(',');
            AppendJsonInt(sb, "virtualX", evidence.VirtualX).Append(',');
            AppendJsonInt(sb, "virtualY", evidence.VirtualY).Append(',');
            AppendJsonInt(sb, "virtualWidth", evidence.VirtualWidth).Append(',');
            AppendJsonInt(sb, "virtualHeight", evidence.VirtualHeight);
            sb.Append("},");

            sb.Append("\"cursor\":{");
            AppendJsonBool(sb, "initialOk", evidence.InitialCursorOk).Append(',');
            AppendJsonInt(sb, "initialError", evidence.InitialCursorError).Append(',');
            AppendJsonInt(sb, "initialX", evidence.InitialX).Append(',');
            AppendJsonInt(sb, "initialY", evidence.InitialY).Append(',');
            AppendJsonBool(sb, "finalOk", evidence.FinalCursorOk).Append(',');
            AppendJsonInt(sb, "finalX", evidence.FinalX).Append(',');
            AppendJsonInt(sb, "finalY", evidence.FinalY).Append(',');
            AppendJsonBool(sb, "restored", evidence.Restored);
            sb.Append("},");

            sb.Append("\"mouse\":{");
            AppendJsonInt(sb, "moveCount", evidence.Moves.Count).Append(',');
            AppendJsonInt(sb, "moveOkCount", CountSuccessfulMoves(evidence)).Append(',');
            sb.Append("\"moves\":[");
            for (int i = 0; i < evidence.Moves.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                MoveRecord move = evidence.Moves[i];
                sb.Append('{');
                AppendJsonInt(sb, "dx", move.Dx).Append(',');
                AppendJsonInt(sb, "dy", move.Dy).Append(',');
                AppendJsonInt(sb, "targetX", move.TargetX).Append(',');
                AppendJsonInt(sb, "targetY", move.TargetY).Append(',');
                AppendJsonBool(sb, "setOk", move.SetOk).Append(',');
                AppendJsonInt(sb, "setError", move.SetError).Append(',');
                AppendJsonBool(sb, "readOk", move.ReadOk).Append(',');
                AppendJsonInt(sb, "actualX", move.ReadOk ? move.ActualX : -1).Append(',');
                AppendJsonInt(sb, "actualY", move.ReadOk ? move.ActualY : -1).Append(',');
                AppendJsonBool(sb, "reachedTarget",
                    move.SetOk && move.ReadOk && move.ActualX == move.TargetX && move.ActualY == move.TargetY);
                sb.Append('}');
            }
            sb.Append("]},");

            sb.Append("\"foreground\":{");
            AppendJsonInt64(sb, "beforeHwnd", evidence.ForegroundBeforeHwnd).Append(',');
            AppendJsonInt64(sb, "afterHwnd", evidence.ForegroundAfterHwnd).Append(',');
            AppendJsonBool(sb, "changed", evidence.ForegroundBeforeHwnd != evidence.ForegroundAfterHwnd);
            sb.Append("},");

            sb.Append("\"clipboard\":{");
            AppendJsonString(sb, "method", "read-only").Append(',');
            AppendJsonBool(sb, "opened", evidence.ClipboardOpened).Append(',');
            AppendJsonInt(sb, "error", evidence.ClipboardError).Append(',');
            AppendJsonInt(sb, "formatCount", evidence.ClipboardFormatCount).Append(',');
            sb.Append("\"formats\":[");
            for (int i = 0; i < evidence.ClipboardFormats.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                sb.Append(evidence.ClipboardFormats[i].ToString(CultureInfo.InvariantCulture));
            }
            sb.Append("],");
            AppendJsonBool(sb, "hasUnicodeText", evidence.ClipboardHasUnicodeText).Append(',');
            AppendJsonBool(sb, "hasText", evidence.ClipboardHasText).Append(',');
            AppendJsonBool(sb, "hasFileDrop", evidence.ClipboardHasFileDrop).Append(',');
            sb.Append("\"modified\":false},");

            double durationMs = 0;
            if (evidence.FinishedUtc > evidence.StartedUtc)
            {
                durationMs = (evidence.FinishedUtc - evidence.StartedUtc).TotalMilliseconds;
            }
            sb.Append("\"timing\":{");
            AppendJsonString(sb, "startedAtUtc",
                evidence.StartedUtc.ToString("o", CultureInfo.InvariantCulture)).Append(',');
            AppendJsonString(sb, "finishedAtUtc",
                evidence.FinishedUtc.ToString("o", CultureInfo.InvariantCulture)).Append(',');
            AppendJsonDouble(sb, "durationMs", durationMs);
            sb.Append("},");

            AppendJsonString(sb, "note",
                "Probe ran inside a non-parent session. ok=true only if the initial cursor read succeeded, "
                + "screen sizes are valid, all 4 small mouse moves reached their exact targets, and the "
                + "original cursor position was restored. Clipboard was inspected read-only (no data copied) "
                + "and never modified; no keyboard input and no window automation.");
            sb.Append('}');
            return sb.ToString();
        }

        private static int CountSuccessfulMoves(ProbeEvidence evidence)
        {
            int count = 0;
            for (int i = 0; i < evidence.Moves.Count; i++)
            {
                MoveRecord move = evidence.Moves[i];
                if (move.SetOk && move.ReadOk &&
                    move.ActualX == move.TargetX && move.ActualY == move.TargetY)
                {
                    count++;
                }
            }
            return count;
        }

        private static string BuildErrorJson(int parentSession, int currentSession, Exception ex)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append('{');
            AppendJsonString(sb, "tool", "ChildProbe").Append(',');
            AppendJsonBool(sb, "ok", false).Append(',');
            sb.Append("\"failureReasons\":[");
            AppendJsonQuoted(sb, ex.GetType().Name + ": " + ex.Message);
            sb.Append("],");
            AppendJsonBool(sb, "refused", false).Append(',');
            AppendJsonBool(sb, "guardPassed", true).Append(',');
            AppendJsonInt(sb, "parentSessionArg", parentSession).Append(',');
            AppendJsonInt(sb, "sessionId", currentSession).Append(',');
            AppendJsonInt(sb, "processId", unchecked((int)GetCurrentProcessId())).Append(',');
            AppendJsonString(sb, "generatedAtUtc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture));
            sb.Append('}');
            return sb.ToString();
        }

        #region JSON helpers

        private static StringBuilder AppendJsonName(StringBuilder sb, string name)
        {
            AppendJsonQuoted(sb, name);
            return sb.Append(':');
        }

        private static StringBuilder AppendJsonBool(StringBuilder sb, string name, bool value)
        {
            return AppendJsonName(sb, name).Append(value ? "true" : "false");
        }

        private static StringBuilder AppendJsonInt(StringBuilder sb, string name, int value)
        {
            return AppendJsonName(sb, name).Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static StringBuilder AppendJsonInt64(StringBuilder sb, string name, long value)
        {
            return AppendJsonName(sb, name).Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static StringBuilder AppendJsonDouble(StringBuilder sb, string name, double value)
        {
            return AppendJsonName(sb, name).Append(value.ToString("0.###", CultureInfo.InvariantCulture));
        }

        private static StringBuilder AppendJsonString(StringBuilder sb, string name, string value)
        {
            AppendJsonName(sb, name);
            if (value == null)
            {
                sb.Append("null");
            }
            else
            {
                AppendJsonQuoted(sb, value);
            }
            return sb;
        }

        private static void AppendJsonQuoted(StringBuilder sb, string value)
        {
            sb.Append('"');
            if (value != null)
            {
                for (int i = 0; i < value.Length; i++)
                {
                    char c = value[i];
                    switch (c)
                    {
                        case '"':
                            sb.Append("\\\"");
                            break;
                        case '\\':
                            sb.Append("\\\\");
                            break;
                        case '\b':
                            sb.Append("\\b");
                            break;
                        case '\f':
                            sb.Append("\\f");
                            break;
                        case '\n':
                            sb.Append("\\n");
                            break;
                        case '\r':
                            sb.Append("\\r");
                            break;
                        case '\t':
                            sb.Append("\\t");
                            break;
                        default:
                            if (c < 0x20)
                            {
                                sb.Append("\\u");
                                sb.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                            }
                            else
                            {
                                sb.Append(c);
                            }
                            break;
                    }
                }
            }
            sb.Append('"');
        }

        #endregion

        #endregion
    }
}
