// ProbeLogicTest.cs
// Verification-only unit harness for the pure result judgment of ChildProbe
// (ChildProbe.EvaluateProbe). It is compiled together with ChildProbe.cs into a separate test
// executable, so no real cursor movement, no clipboard access and no child session is required.
//
// Build (done by build.ps1):
//   csc /target:exe /main:WeChatIsolatedDesktop.ProbeLogicTest
//       /out:verify\ProbeLogicTest.exe ChildProbe.cs verify\ProbeLogicTest.cs
//
// The harness feeds synthetic ProbeEvidence objects (including deliberately broken ones) to the
// same EvaluateProbe function used at runtime and asserts that "ok" is false whenever any of the
// success criteria does not hold.

using System;
using System.Collections.Generic;
using System.Globalization;

namespace WeChatIsolatedDesktop
{
    internal static class ProbeLogicTest
    {
        private static int Main()
        {
            int passed = 0;
            int failed = 0;

            // 1. Fully valid evidence -> ok=true, no failure reasons.
            ProbeEvidence valid = ValidEvidence();
            Check("valid evidence -> ok=true", valid, true, true, ref passed, ref failed);

            // 2. Initial GetCursorPos failed -> ok=false (and no movement is implied).
            ProbeEvidence initialCursorFailed = ValidEvidence();
            initialCursorFailed.InitialCursorOk = false;
            initialCursorFailed.InitialCursorError = 5;
            initialCursorFailed.Moves.Clear();
            Check("initial GetCursorPos failure -> ok=false", initialCursorFailed, false, true, ref passed, ref failed);

            // 3. Invalid physical screen size -> ok=false.
            ProbeEvidence badScreen = ValidEvidence();
            badScreen.ScreenWidth = 0;
            Check("invalid screen size -> ok=false", badScreen, false, true, ref passed, ref failed);

            // 4. Invalid virtual screen size -> ok=false.
            ProbeEvidence badVirtualScreen = ValidEvidence();
            badVirtualScreen.VirtualHeight = 0;
            Check("invalid virtual screen size -> ok=false", badVirtualScreen, false, true, ref passed, ref failed);

            // 5. Fewer than the expected 4 moves -> ok=false.
            ProbeEvidence missingMove = ValidEvidence();
            missingMove.Moves.RemoveAt(missingMove.Moves.Count - 1);
            Check("missing mouse move -> ok=false", missingMove, false, true, ref passed, ref failed);

            // 6. A SetCursorPos failure -> ok=false.
            ProbeEvidence setFailed = ValidEvidence();
            setFailed.Moves[1].SetOk = false;
            setFailed.Moves[1].SetError = 5;
            Check("SetCursorPos failure -> ok=false", setFailed, false, true, ref passed, ref failed);

            // 7. A failed GetCursorPos after a move -> ok=false.
            ProbeEvidence readFailed = ValidEvidence();
            readFailed.Moves[2].ReadOk = false;
            Check("GetCursorPos after move failure -> ok=false", readFailed, false, true, ref passed, ref failed);

            // 8. Actual cursor coordinate differs from the requested target -> ok=false.
            ProbeEvidence offTarget = ValidEvidence();
            offTarget.Moves[3].ActualX = offTarget.Moves[3].TargetX + 1;
            Check("cursor did not reach target -> ok=false", offTarget, false, true, ref passed, ref failed);

            // 9. Original cursor position not restored -> ok=false.
            ProbeEvidence notRestored = ValidEvidence();
            notRestored.Restored = false;
            Check("cursor not restored -> ok=false", notRestored, false, true, ref passed, ref failed);

            // 10. Final cursor position differs from the original -> ok=false.
            ProbeEvidence finalMismatch = ValidEvidence();
            finalMismatch.FinalX = finalMismatch.InitialX + 2;
            finalMismatch.FinalY = finalMismatch.InitialY + 2;
            Check("final cursor differs from original -> ok=false", finalMismatch, false, true, ref passed, ref failed);

            // 11. Final GetCursorPos failed -> ok=false.
            ProbeEvidence finalReadFailed = ValidEvidence();
            finalReadFailed.FinalCursorOk = false;
            Check("final GetCursorPos failure -> ok=false", finalReadFailed, false, true, ref passed, ref failed);

            // 12. A "silently ignored" failed move must not be reported as success: ensure that the
            //     moveOkCount helper stays consistent with the judgment for mixed evidence.
            ProbeEvidence mixed = ValidEvidence();
            mixed.Moves[0].SetOk = false;
            mixed.Moves[0].SetError = 5;
            ProbeEvaluation mixedEvaluation = ChildProbe.EvaluateProbe(mixed);
            bool consistent = !mixedEvaluation.Ok && mixedEvaluation.FailureReasons.Count > 0;
            Report("mixed failure evidence -> ok=false with reasons", consistent, ref passed, ref failed);

            // 13. The generated JSON must carry the judgment: ok=true for valid evidence, ok=false
            //     plus failureReasons for broken evidence. This validates the JSON wiring without any
            //     desktop access.
            ProbeEvaluation validEvaluation = ChildProbe.EvaluateProbe(ValidEvidence());
            string validJson = ChildProbe.BuildProbeJson(ValidEvidence(), validEvaluation);
            bool validJsonOk = validJson.Contains("\"ok\":true") &&
                validJson.Contains("\"failureReasons\":[]");
            Report("valid evidence JSON has ok=true and empty failureReasons", validJsonOk, ref passed, ref failed);

            ProbeEvidence brokenEvidence = ValidEvidence();
            brokenEvidence.Restored = false;
            ProbeEvaluation brokenEvaluation = ChildProbe.EvaluateProbe(brokenEvidence);
            string brokenJson = ChildProbe.BuildProbeJson(brokenEvidence, brokenEvaluation);
            bool brokenJsonOk = brokenJson.Contains("\"ok\":false") &&
                brokenJson.Contains("was not restored") &&
                brokenJson.Contains("\"failureReasons\":[\"");
            Report("broken evidence JSON has ok=false and failure reasons", brokenJsonOk, ref passed, ref failed);

            Console.WriteLine(string.Format(
                CultureInfo.InvariantCulture,
                "RESULT: {0} passed, {1} failed",
                passed, failed));

            return failed == 0 ? 0 : 1;
        }

        private static void Check(
            string name,
            ProbeEvidence evidence,
            bool expectedOk,
            bool requireReasonsWhenFailing,
            ref int passed,
            ref int failed)
        {
            ProbeEvaluation evaluation = ChildProbe.EvaluateProbe(evidence);

            bool ok = evaluation.Ok == expectedOk;
            if (requireReasonsWhenFailing && !expectedOk && evaluation.FailureReasons.Count == 0)
            {
                ok = false;
            }
            if (expectedOk && evaluation.FailureReasons.Count != 0)
            {
                ok = false;
            }

            Report(name, ok, ref passed, ref failed);
            if (!ok)
            {
                Console.WriteLine("      expected ok=" + expectedOk +
                    ", actual ok=" + evaluation.Ok +
                    ", reasons=" + string.Join(" | ", evaluation.FailureReasons.ToArray()));
            }
        }

        private static void Report(string name, bool ok, ref int passed, ref int failed)
        {
            if (ok)
            {
                passed++;
                Console.WriteLine("PASS  " + name);
            }
            else
            {
                failed++;
                Console.WriteLine("FAIL  " + name);
            }
        }

        /// <summary>Builds evidence that satisfies every success criterion (no Win32 involved).</summary>
        private static ProbeEvidence ValidEvidence()
        {
            ProbeEvidence evidence = new ProbeEvidence();
            evidence.ParentSessionArg = 1;
            evidence.SessionId = 99;
            evidence.ProcessId = 4242;
            evidence.WindowStation = "WinSta0";
            evidence.Desktop = "Default";
            evidence.ScreenWidth = 1920;
            evidence.ScreenHeight = 1080;
            evidence.VirtualX = 0;
            evidence.VirtualY = 0;
            evidence.VirtualWidth = 1920;
            evidence.VirtualHeight = 1080;

            evidence.InitialCursorOk = true;
            evidence.InitialCursorError = 0;
            evidence.InitialX = 500;
            evidence.InitialY = 400;

            int[][] deltas = new int[][]
            {
                new int[] { 1, 1 },
                new int[] { -1, -1 },
                new int[] { 3, 0 },
                new int[] { -3, 0 }
            };

            for (int i = 0; i < deltas.Length; i++)
            {
                MoveRecord move = new MoveRecord();
                move.Dx = deltas[i][0];
                move.Dy = deltas[i][1];
                move.TargetX = evidence.InitialX + move.Dx;
                move.TargetY = evidence.InitialY + move.Dy;
                move.SetOk = true;
                move.SetError = 0;
                move.ReadOk = true;
                move.ActualX = move.TargetX;
                move.ActualY = move.TargetY;
                evidence.Moves.Add(move);
            }

            evidence.Restored = true;
            evidence.FinalCursorOk = true;
            evidence.FinalX = evidence.InitialX;
            evidence.FinalY = evidence.InitialY;
            evidence.ForegroundBeforeHwnd = 100;
            evidence.ForegroundAfterHwnd = 100;

            evidence.ClipboardOpened = true;
            evidence.ClipboardError = 0;
            evidence.ClipboardFormatCount = 3;
            evidence.ClipboardFormats.Add(1);
            evidence.ClipboardFormats.Add(13);
            evidence.ClipboardFormats.Add(16);
            evidence.ClipboardHasUnicodeText = true;
            evidence.StartedUtc = DateTime.UtcNow;
            evidence.FinishedUtc = DateTime.UtcNow.AddMilliseconds(80);
            return evidence;
        }
    }
}
