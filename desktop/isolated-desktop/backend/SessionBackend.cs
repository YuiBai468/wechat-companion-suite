// SessionBackend.cs
// Windows native child-session backend for the isolated WeChat desktop.
//
// Target: .NET Framework 4.8, C# 5 (compiled with the in-box csc.exe /langversion:5).
//
// Shared contract (consumed by the GPT-owned WinForms RDP ActiveX launcher):
//   public static int    GetCurrentSessionId();          // session id of the calling process
//   public static bool   IsChildSessionsEnabled();       // WTSIsChildSessionsEnabled
//   public static int    GetChildSessionId();            // child session of the current session, -1 if none
//   public static string StatusJson();                   // non-secret status only
//   public static int    LaunchInChild(string executable, string arguments, string workingDirectory);
//
// Safety rules implemented here:
//   * LaunchInChild only ever targets the session returned by WTSGetChildSessionId() for the
//     current session. It never falls back to the parent/current session.
//   * The token used to create the process is taken from an existing process that lives in the
//     target child session AND belongs to the same user (SID comparison). The token's session id
//     is verified before CreateProcessAsUser.
//   * No SYSTEM assumption, no password, no privilege escalation, no service/task creation, no
//     security-policy change.
//   * The target process is created with CREATE_SUSPENDED (documented CreateProcessAsUser
//     dwCreationFlags value). Before its main thread is resumed the new pid's session is verified
//     with ProcessIdToSessionId; if that query fails or returns any session other than the expected
//     child session, the freshly created suspended process is terminated and the call fails with a
//     clear exception. Only after a successful session verification is ResumeThread called; a
//     resume failure also terminates the process and throws.
//   * CreateProcessWithTokenW is intentionally NOT used as a fallback: its default logon/session
//     semantics do not match this cross-session scenario, and it would only add a second, ambiguous
//     failure mode. Only CreateProcessAsUserW is attempted, so a privilege problem surfaces as its
//     explicit Win32 error (e.g. ERROR_PRIVILEGE_NOT_HELD 1314 / ERROR_ACCESS_DENIED 5).
//
// This file contains only P/Invoke declarations and managed glue; it does not open UI, does not
// touch WeChat and does not change any system setting.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace WeChatIsolatedDesktop
{
    public static class SessionBackend
    {
        #region Win32 constants

        private const int ERROR_SUCCESS = 0;
        private const int ERROR_NOT_FOUND = 1168;
        private const int ERROR_INSUFFICIENT_BUFFER = 122;
        private const int ERROR_PRIVILEGE_NOT_HELD = 1314;
        private const int ERROR_ACCESS_DENIED = 5;

        private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
        private const uint PROCESS_TERMINATE = 0x0001;

        private const uint TOKEN_ASSIGN_PRIMARY = 0x0001;
        private const uint TOKEN_DUPLICATE = 0x0002;
        private const uint TOKEN_QUERY = 0x0008;
        private const uint TOKEN_ALL_ACCESS = 0x000F01FF;
        private const uint MAXIMUM_ALLOWED = 0x02000000;

        private const int SecurityImpersonation = 2;
        private const int TokenPrimary = 1;

        private const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;
        private const uint CREATE_SUSPENDED = 0x00000004;
        private const uint THREAD_SUSPEND_FAILED = 0xFFFFFFFF;

        // TOKEN_INFORMATION_CLASS values (winnt.h)
        private const int TokenUserClass = 1;
        private const int TokenGroupsClass = 2;
        private const int TokenPrivilegesClass = 3;
        private const int TokenSessionIdClass = 12;
        private const int TokenElevationClass = 20;

        private const int SE_PRIVILEGE_ENABLED = 0x00000002;
        private const int SE_GROUP_ENABLED = 0x00000004;

        private const string BUILTIN_ADMINISTRATORS_SID = "S-1-5-32-544";

        #endregion

        #region Win32 structs

        [StructLayout(LayoutKind.Sequential)]
        private struct LUID
        {
            public uint LowPart;
            public int HighPart;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct LUID_AND_ATTRIBUTES
        {
            public LUID Luid;
            public uint Attributes;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SID_AND_ATTRIBUTES
        {
            public IntPtr Sid;
            public uint Attributes;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct STARTUPINFO
        {
            public int cb;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string lpReserved;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string lpDesktop;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string lpTitle;
            public int dwX;
            public int dwY;
            public int dwXSize;
            public int dwYSize;
            public int dwXCountChars;
            public int dwYCountChars;
            public int dwFillAttribute;
            public int dwFlags;
            public short wShowWindow;
            public short cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_INFORMATION
        {
            public IntPtr hProcess;
            public IntPtr hThread;
            public int dwProcessId;
            public int dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct RTL_OSVERSIONINFOW
        {
            public uint dwOSVersionInfoSize;
            public uint dwMajorVersion;
            public uint dwMinorVersion;
            public uint dwBuildNumber;
            public uint dwPlatformId;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string szCSDVersion;
        }

        #endregion

        #region P/Invoke

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ProcessIdToSessionId(uint dwProcessId, out uint pSessionId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, uint dwProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr LocalFree(IntPtr hMem);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr hProcess, uint uExitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr hThread);

        [DllImport("wtsapi32.dll", SetLastError = true)]
        private static extern bool WTSIsChildSessionsEnabled(
            [MarshalAs(UnmanagedType.Bool)] out bool pbEnabled);

        [DllImport("wtsapi32.dll", SetLastError = true)]
        private static extern bool WTSGetChildSessionId(out uint pSessionId);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool OpenProcessToken(IntPtr ProcessHandle, uint DesiredAccess, out IntPtr TokenHandle);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool DuplicateTokenEx(
            IntPtr hExistingToken,
            uint dwDesiredAccess,
            IntPtr lpTokenAttributes,
            int ImpersonationLevel,
            int TokenType,
            out IntPtr phNewToken);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool GetTokenInformation(
            IntPtr TokenHandle,
            int TokenInformationClass,
            IntPtr TokenInformation,
            uint TokenInformationLength,
            out uint ReturnLength);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool ConvertSidToStringSidW(IntPtr Sid, out IntPtr StringSid);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool LookupPrivilegeValueW(string lpSystemName, string lpName, out LUID lpLuid);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CreateProcessAsUserW")]
        private static extern bool CreateProcessAsUserW(
            IntPtr hToken,
            string lpApplicationName,
            StringBuilder lpCommandLine,
            IntPtr lpProcessAttributes,
            IntPtr lpThreadAttributes,
            bool bInheritHandles,
            uint dwCreationFlags,
            IntPtr lpEnvironment,
            string lpCurrentDirectory,
            ref STARTUPINFO lpStartupInfo,
            out PROCESS_INFORMATION lpProcessInformation);

        [DllImport("userenv.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool CreateEnvironmentBlock(out IntPtr lpEnvironment, IntPtr hToken, bool bInherit);

        [DllImport("userenv.dll", SetLastError = true)]
        private static extern bool DestroyEnvironmentBlock(IntPtr lpEnvironment);

        [DllImport("ntdll.dll", CharSet = CharSet.Unicode)]
        private static extern int RtlGetVersion(ref RTL_OSVERSIONINFOW versionInformation);

        #endregion

        #region Public contract

        /// <summary>Session id of the calling process.</summary>
        public static int GetCurrentSessionId()
        {
            int sessionId;
            int error;
            if (!TryGetCurrentSessionId(out sessionId, out error))
            {
                throw new Win32Exception(error, "ProcessIdToSessionId failed for the current process");
            }
            return sessionId;
        }

        /// <summary>True when the local machine enables Windows child sessions (nested sessions).</summary>
        public static bool IsChildSessionsEnabled()
        {
            bool enabled;
            try
            {
                if (!WTSIsChildSessionsEnabled(out enabled))
                {
                    return false;
                }
                return enabled;
            }
            catch (EntryPointNotFoundException)
            {
                return false;
            }
        }

        /// <summary>
        /// Child session of the current session, or -1 when the current session has no child session
        /// (WTSGetChildSessionId returns ERROR_NOT_FOUND).
        /// </summary>
        public static int GetChildSessionId()
        {
            uint childSession;
            int error;
            if (!TryGetChildSessionId(out childSession, out error))
            {
                return -1;
            }
            if (childSession == 0 || childSession == uint.MaxValue)
            {
                return -1;
            }
            return unchecked((int)childSession);
        }

        /// <summary>
        /// Launch <paramref name="executable"/> inside the child session of the current session.
        /// Returns the new process id. Throws on any failure; never falls back to the parent session.
        /// </summary>
        public static int LaunchInChild(string executable, string arguments, string workingDirectory)
        {
            if (executable == null)
            {
                throw new ArgumentNullException("executable");
            }
            if (arguments == null)
            {
                arguments = String.Empty;
            }
            if (workingDirectory == null)
            {
                workingDirectory = String.Empty;
            }

            int currentSession = GetCurrentSessionId();

            uint childSession;
            int childQueryError;
            if (!TryGetChildSessionId(out childSession, out childQueryError))
            {
                throw new Win32Exception(
                    childQueryError,
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "WTSGetChildSessionId failed with Win32 error {0} ({1}); the current session {2} has no child session. Refusing to launch: this API never falls back to the parent/current session.",
                        childQueryError, FormatWin32Error(childQueryError), currentSession));
            }

            if (childSession == 0 || childSession == uint.MaxValue)
            {
                throw new InvalidOperationException(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "WTSGetChildSessionId returned invalid session id {0}; refusing to launch.",
                        childSession));
            }

            if (unchecked((int)childSession) == currentSession)
            {
                throw new InvalidOperationException(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "WTSGetChildSessionId returned the current session {0}; refusing to launch in the parent session.",
                        currentSession));
            }

            string executableFull = executable;
            if (!Path.IsPathRooted(executableFull))
            {
                executableFull = Path.GetFullPath(executableFull);
            }
            if (!File.Exists(executableFull))
            {
                throw new FileNotFoundException(
                    "Target executable does not exist: " + executableFull, executableFull);
            }

            string workDir = workingDirectory;
            if (workDir.Length > 0)
            {
                if (!Path.IsPathRooted(workDir))
                {
                    workDir = Path.GetFullPath(workDir);
                }
                if (!Directory.Exists(workDir))
                {
                    throw new DirectoryNotFoundException(
                        "Working directory does not exist: " + workDir);
                }
            }
            else
            {
                workDir = null;
            }

            // The first token of the command line is the executable; arguments are appended verbatim
            // (the caller is responsible for quoting argument values).
            string commandLine = QuoteExecutableForCommandLine(executableFull) +
                (arguments.Length > 0 ? " " + arguments : String.Empty);

            string currentUserSid = GetCurrentUserSidString();
            List<string> diagnostics = new List<string>();
            string sourceDescription;
            IntPtr primaryToken = TryFindSameUserPrimaryTokenInSession(
                childSession, currentUserSid, out sourceDescription, diagnostics);

            if (primaryToken == IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "Cannot launch into child session {0}: no usable same-user process token was found in that session. "
                        + "A token source in the target session is required, and opening it may also be denied by session isolation. "
                        + "Diagnostics: {1}",
                        childSession, JoinDiagnostics(diagnostics)));
            }

            try
            {
                int pid = CreateProcessInSessionWithToken(
                    primaryToken, childSession, executableFull, commandLine, workDir);

                TraceLaunch(sourceDescription, pid, childSession);
                return pid;
            }
            finally
            {
                CloseHandle(primaryToken);
            }
        }

        /// <summary>
        /// Non-secret machine/session status as JSON. Never throws; on an unexpected failure a minimal
        /// JSON object with "statusError" is returned.
        /// </summary>
        public static string StatusJson()
        {
            try
            {
                int currentSession;
                int currentError;
                bool currentOk = TryGetCurrentSessionId(out currentSession, out currentError);

                uint childSession;
                int childError;
                bool childOk = TryGetChildSessionId(out childSession, out childError);

                bool enabled = IsChildSessionsEnabled();
                bool elevated = IsProcessElevated();
                bool adminGroup = IsInAdministratorsGroup();
                LaunchPrivilegeStates launchPrivileges = GetLaunchPrivilegeStates();

                StringBuilder sb = new StringBuilder(1024);
                sb.Append('{');
                AppendJsonBool(sb, "enabled", enabled).Append(',');
                AppendJsonInt(sb, "parentSessionId", currentOk ? currentSession : -1).Append(',');
                AppendJsonInt(sb, "childSessionId", childOk ? unchecked((int)childSession) : -1).Append(',');
                AppendJsonBool(sb, "isAdmin", elevated).Append(',');
                AppendJsonBool(sb, "isAdminGroupMember", adminGroup).Append(',');
                AppendJsonInt(sb, "currentSessionId", currentOk ? currentSession : -1).Append(',');
                AppendJsonInt(sb, "currentProcessId", Process.GetCurrentProcess().Id).Append(',');
                AppendJsonString(sb, "osVersion", GetOsVersionString()).Append(',');
                AppendJsonString(
                    sb,
                    "sessionQueryError",
                    currentOk ? null : FormatWin32Error(currentError)).Append(',');
                AppendJsonString(
                    sb,
                    "childSessionQueryError",
                    childOk ? null : FormatWin32Error(childError));
                sb.Append(",\"privileges\":{");
                AppendPrivilegeJson(
                    sb, "SeAssignPrimaryTokenPrivilege",
                    launchPrivileges.AssignPrimaryTokenPresent,
                    launchPrivileges.AssignPrimaryTokenEnabled);
                sb.Append(',');
                AppendPrivilegeJson(
                    sb, "SeIncreaseQuotaPrivilege",
                    launchPrivileges.IncreaseQuotaPresent,
                    launchPrivileges.IncreaseQuotaEnabled);
                sb.Append(',');
                AppendPrivilegeJson(
                    sb, "SeImpersonatePrivilege",
                    launchPrivileges.ImpersonatePresent,
                    launchPrivileges.ImpersonateEnabled);
                sb.Append("},");
                AppendJsonString(sb, "generatedAtUtc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture));
                sb.Append('}');
                return sb.ToString();
            }
            catch (Exception ex)
            {
                StringBuilder sb = new StringBuilder();
                sb.Append('{');
                AppendJsonBool(sb, "enabled", false).Append(',');
                AppendJsonInt(sb, "parentSessionId", -1).Append(',');
                AppendJsonInt(sb, "childSessionId", -1).Append(',');
                AppendJsonBool(sb, "isAdmin", false).Append(',');
                AppendJsonString(sb, "statusError", ex.GetType().Name + ": " + ex.Message);
                sb.Append('}');
                return sb.ToString();
            }
        }

        #endregion

        #region Session queries

        private static bool TryGetCurrentSessionId(out int sessionId, out int error)
        {
            sessionId = -1;
            error = ERROR_SUCCESS;
            uint value;
            uint pid = unchecked((uint)Process.GetCurrentProcess().Id);
            if (!ProcessIdToSessionId(pid, out value))
            {
                error = Marshal.GetLastWin32Error();
                return false;
            }
            sessionId = unchecked((int)value);
            return true;
        }

        private static bool TryGetChildSessionId(out uint sessionId, out int error)
        {
            sessionId = 0;
            error = ERROR_SUCCESS;
            if (!WTSGetChildSessionId(out sessionId))
            {
                error = Marshal.GetLastWin32Error();
                sessionId = 0;
                return false;
            }
            return true;
        }

        #endregion

        #region Token discovery / process creation

        /// <summary>
        /// Finds a primary token for a process that is inside <paramref name="targetSession"/> and has
        /// the same user SID as the current process. Returns IntPtr.Zero and fills diagnostics on failure.
        /// </summary>
        private static IntPtr TryFindSameUserPrimaryTokenInSession(
            uint targetSession,
            string currentUserSid,
            out string sourceDescription,
            List<string> diagnostics)
        {
            sourceDescription = null;
            Process[] processes;
            try
            {
                processes = Process.GetProcesses();
            }
            catch (Exception ex)
            {
                diagnostics.Add("Process.GetProcesses failed: " + ex.Message);
                return IntPtr.Zero;
            }

            int inSession = 0;
            int sameUser = 0;
            int openProcessFailures = 0;
            int openTokenFailures = 0;
            int duplicateFailures = 0;

            try
            {
                foreach (Process process in processes)
                {
                    int candidatePid;
                    int candidateSession;
                    try
                    {
                        candidatePid = process.Id;
                        candidateSession = process.SessionId;
                    }
                    catch (Exception)
                    {
                        continue;
                    }

                    if (candidateSession != unchecked((int)targetSession))
                    {
                        continue;
                    }
                    inSession++;

                    IntPtr processHandle = OpenProcess(
                        PROCESS_QUERY_LIMITED_INFORMATION, false, unchecked((uint)candidatePid));
                    if (processHandle == IntPtr.Zero)
                    {
                        openProcessFailures++;
                        continue;
                    }

                    IntPtr tokenHandle = IntPtr.Zero;
                    try
                    {
                        uint desiredAccess = TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY;
                        if (!OpenProcessToken(processHandle, desiredAccess, out tokenHandle))
                        {
                            if (tokenHandle != IntPtr.Zero)
                            {
                                CloseHandle(tokenHandle);
                                tokenHandle = IntPtr.Zero;
                            }
                            if (!OpenProcessToken(
                                    processHandle,
                                    TOKEN_QUERY | TOKEN_DUPLICATE | MAXIMUM_ALLOWED,
                                    out tokenHandle))
                            {
                                openTokenFailures++;
                                continue;
                            }
                        }

                        uint tokenSession;
                        if (!GetTokenUInt32(tokenHandle, TokenSessionIdClass, out tokenSession) ||
                            tokenSession != targetSession)
                        {
                            continue;
                        }

                        string tokenUserSid = GetTokenUserSidString(tokenHandle);
                        if (tokenUserSid == null ||
                            !string.Equals(tokenUserSid, currentUserSid, StringComparison.OrdinalIgnoreCase))
                        {
                            continue;
                        }
                        sameUser++;

                        IntPtr primaryToken;
                        if (!DuplicateTokenEx(
                                tokenHandle,
                                TOKEN_ALL_ACCESS,
                                IntPtr.Zero,
                                SecurityImpersonation,
                                TokenPrimary,
                                out primaryToken))
                        {
                            duplicateFailures++;
                            continue;
                        }

                        sourceDescription = string.Format(
                            CultureInfo.InvariantCulture,
                            "process pid={0} in session {1}", candidatePid, targetSession);
                        return primaryToken;
                    }
                    finally
                    {
                        if (tokenHandle != IntPtr.Zero)
                        {
                            CloseHandle(tokenHandle);
                        }
                        CloseHandle(processHandle);
                    }
                }
            }
            finally
            {
                foreach (Process process in processes)
                {
                    try
                    {
                        process.Dispose();
                    }
                    catch (Exception)
                    {
                        // best effort
                    }
                }
            }

            diagnostics.Add(string.Format(
                CultureInfo.InvariantCulture,
                "enumerated {0} processes, {1} in child session {2}, {3} with a same-user token, "
                + "OpenProcess failures {4}, OpenProcessToken failures {5}, DuplicateTokenEx failures {6}",
                processes.Length, inSession, targetSession, sameUser,
                openProcessFailures, openTokenFailures, duplicateFailures));
            return IntPtr.Zero;
        }

        private static int CreateProcessInSessionWithToken(
            IntPtr primaryToken,
            uint expectedSession,
            string executableFull,
            string commandLine,
            string workingDirectory)
        {
            IntPtr environmentBlock = IntPtr.Zero;
            bool haveEnvironment = CreateEnvironmentBlock(out environmentBlock, primaryToken, false);

            try
            {
                // CREATE_SUSPENDED is a documented CreateProcessAsUser dwCreationFlags value. The new
                // process cannot execute any code (including any UI) until its main thread is resumed
                // below - i.e. only after the session of the new pid has been verified successfully.
                uint creationFlags = CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED;

                STARTUPINFO startupInfo = new STARTUPINFO();
                startupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFO));
                startupInfo.lpDesktop = "winsta0\\default";

                PROCESS_INFORMATION processInfo = new PROCESS_INFORMATION();
                StringBuilder mutableCommandLine = new StringBuilder(commandLine);
                bool created = CreateProcessAsUserW(
                    primaryToken,
                    executableFull,
                    mutableCommandLine,
                    IntPtr.Zero,
                    IntPtr.Zero,
                    false,
                    creationFlags,
                    environmentBlock,
                    workingDirectory,
                    ref startupInfo,
                    out processInfo);

                if (!created)
                {
                    int error = Marshal.GetLastWin32Error();
                    throw new Win32Exception(
                        error,
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "CreateProcessAsUserW failed with Win32 error {0} ({1}); the target process was not created. "
                            + "Cross-session creation requires SeAssignPrimaryTokenPrivilege + SeIncreaseQuotaPrivilege "
                            + "(elevated administrator or SYSTEM). CreateProcessWithTokenW is deliberately not used as a "
                            + "fallback, and there is no fallback to the parent session.",
                            error, FormatWin32Error(error)));
                }

                try
                {
                    // ---- Mandatory session verification BEFORE the suspended process may run. ----
                    uint actualSession;
                    if (!ProcessIdToSessionId(unchecked((uint)processInfo.dwProcessId), out actualSession))
                    {
                        int error = Marshal.GetLastWin32Error();
                        TerminateSuspendedProcess(processInfo);
                        throw new Win32Exception(
                            error,
                            string.Format(
                                CultureInfo.InvariantCulture,
                                "Created pid {0} suspended, but ProcessIdToSessionId failed with Win32 error {1} ({2}); "
                                + "the session of the new process could not be verified, so the suspended process has been "
                                + "terminated and was never resumed.",
                                processInfo.dwProcessId, error, FormatWin32Error(error)));
                    }

                    if (actualSession != expectedSession)
                    {
                        TerminateSuspendedProcess(processInfo);
                        throw new InvalidOperationException(string.Format(
                            CultureInfo.InvariantCulture,
                            "Created pid {0} suspended in session {1}, but expected child session {2}; the suspended "
                            + "process has been terminated and was never resumed. Refusing to hand out a pid outside "
                            + "the child session.",
                            processInfo.dwProcessId, actualSession, expectedSession));
                    }

                    // ---- Session confirmed: only now is it safe to let the process run. ----
                    uint previousSuspendCount = ResumeThread(processInfo.hThread);
                    if (previousSuspendCount == THREAD_SUSPEND_FAILED)
                    {
                        int error = Marshal.GetLastWin32Error();
                        TerminateSuspendedProcess(processInfo);
                        throw new Win32Exception(
                            error,
                            string.Format(
                                CultureInfo.InvariantCulture,
                                "Session verification passed for pid {0} in child session {1}, but ResumeThread failed "
                                + "with Win32 error {2} ({3}); the process has been terminated.",
                                processInfo.dwProcessId, expectedSession, error, FormatWin32Error(error)));
                    }

                    return processInfo.dwProcessId;
                }
                finally
                {
                    if (processInfo.hThread != IntPtr.Zero)
                    {
                        CloseHandle(processInfo.hThread);
                    }
                    if (processInfo.hProcess != IntPtr.Zero)
                    {
                        CloseHandle(processInfo.hProcess);
                    }
                }
            }
            finally
            {
                if (haveEnvironment && environmentBlock != IntPtr.Zero)
                {
                    DestroyEnvironmentBlock(environmentBlock);
                }
            }
        }

        /// <summary>Terminates a process this call created and still holds suspended.</summary>
        private static void TerminateSuspendedProcess(PROCESS_INFORMATION processInfo)
        {
            if (processInfo.hProcess == IntPtr.Zero)
            {
                return;
            }
            TerminateProcess(processInfo.hProcess, 1);
        }

        private static void TraceLaunch(string sourceDescription, int pid, uint childSession)
        {
            try
            {
                Trace.WriteLine(string.Format(
                    CultureInfo.InvariantCulture,
                    "SessionBackend: launched pid={0} into child session {1} using source token {{{2}}}",
                    pid, childSession, sourceDescription));
            }
            catch (Exception)
            {
                // tracing must never break a launch
            }
        }

        private static string QuoteExecutableForCommandLine(string executableFull)
        {
            // Double quotes are illegal in Windows file names, so wrapping is sufficient here.
            // Arguments are appended verbatim by the caller and are not rewritten.
            return "\"" + executableFull + "\"";
        }

        private static string JoinDiagnostics(List<string> diagnostics)
        {
            if (diagnostics == null || diagnostics.Count == 0)
            {
                return "none";
            }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < diagnostics.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append("; ");
                }
                sb.Append(diagnostics[i]);
            }
            return sb.ToString();
        }

        #endregion

        #region Token helpers

        private static string GetCurrentUserSidString()
        {
            IntPtr currentProcess = GetCurrentProcess();
            IntPtr tokenHandle = IntPtr.Zero;
            try
            {
                if (!OpenProcessToken(currentProcess, TOKEN_QUERY, out tokenHandle))
                {
                    return null;
                }
                return GetTokenUserSidString(tokenHandle);
            }
            finally
            {
                if (tokenHandle != IntPtr.Zero)
                {
                    CloseHandle(tokenHandle);
                }
            }
        }

        private static string GetTokenUserSidString(IntPtr tokenHandle)
        {
            uint needed;
            GetTokenInformation(tokenHandle, TokenUserClass, IntPtr.Zero, 0, out needed);
            if (needed == 0)
            {
                return null;
            }

            IntPtr buffer = Marshal.AllocHGlobal(unchecked((int)needed));
            try
            {
                uint returned;
                if (!GetTokenInformation(tokenHandle, TokenUserClass, buffer, needed, out returned))
                {
                    return null;
                }
                IntPtr sid = Marshal.ReadIntPtr(buffer);
                if (sid == IntPtr.Zero)
                {
                    return null;
                }
                return ConvertSidToString(sid);
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        private static string ConvertSidToString(IntPtr sid)
        {
            IntPtr stringSid;
            if (!ConvertSidToStringSidW(sid, out stringSid))
            {
                return null;
            }
            try
            {
                return Marshal.PtrToStringUni(stringSid);
            }
            finally
            {
                LocalFree(stringSid);
            }
        }

        private static bool GetTokenUInt32(IntPtr tokenHandle, int informationClass, out uint value)
        {
            value = 0;
            IntPtr buffer = Marshal.AllocHGlobal(16);
            try
            {
                uint returned;
                if (!GetTokenInformation(tokenHandle, informationClass, buffer, 16, out returned))
                {
                    return false;
                }
                value = unchecked((uint)Marshal.ReadInt32(buffer));
                return true;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        private static bool IsProcessElevated()
        {
            IntPtr currentProcess = GetCurrentProcess();
            IntPtr tokenHandle = IntPtr.Zero;
            try
            {
                if (!OpenProcessToken(currentProcess, TOKEN_QUERY, out tokenHandle))
                {
                    return false;
                }
                uint elevated;
                return GetTokenUInt32(tokenHandle, TokenElevationClass, out elevated) && elevated != 0;
            }
            finally
            {
                if (tokenHandle != IntPtr.Zero)
                {
                    CloseHandle(tokenHandle);
                }
            }
        }

        private static bool IsInAdministratorsGroup()
        {
            IntPtr currentProcess = GetCurrentProcess();
            IntPtr tokenHandle = IntPtr.Zero;
            try
            {
                if (!OpenProcessToken(currentProcess, TOKEN_QUERY, out tokenHandle))
                {
                    return false;
                }
                return TokenContainsGroup(tokenHandle, BUILTIN_ADMINISTRATORS_SID);
            }
            finally
            {
                if (tokenHandle != IntPtr.Zero)
                {
                    CloseHandle(tokenHandle);
                }
            }
        }

        private static bool TokenContainsGroup(IntPtr tokenHandle, string targetSid)
        {
            uint needed;
            GetTokenInformation(tokenHandle, TokenGroupsClass, IntPtr.Zero, 0, out needed);
            if (needed == 0)
            {
                return false;
            }

            IntPtr buffer = Marshal.AllocHGlobal(unchecked((int)needed));
            try
            {
                uint returned;
                if (!GetTokenInformation(tokenHandle, TokenGroupsClass, buffer, needed, out returned))
                {
                    return false;
                }

                int groupCount = Marshal.ReadInt32(buffer);
                int entrySize = Marshal.SizeOf(typeof(SID_AND_ATTRIBUTES));
                int entriesOffset = IntPtr.Size == 8 ? 8 : 4;

                for (int i = 0; i < groupCount; i++)
                {
                    IntPtr entry = new IntPtr(
                        buffer.ToInt64() + entriesOffset + (long)i * entrySize);
                    SID_AND_ATTRIBUTES item = (SID_AND_ATTRIBUTES)Marshal.PtrToStructure(
                        entry, typeof(SID_AND_ATTRIBUTES));
                    if ((item.Attributes & SE_GROUP_ENABLED) == 0)
                    {
                        continue;
                    }
                    string sid = ConvertSidToString(item.Sid);
                    if (string.Equals(sid, targetSid, StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
                return false;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        private sealed class LaunchPrivilegeStates
        {
            public bool AssignPrimaryTokenPresent;
            public bool AssignPrimaryTokenEnabled;
            public bool IncreaseQuotaPresent;
            public bool IncreaseQuotaEnabled;
            public bool ImpersonatePresent;
            public bool ImpersonateEnabled;
        }

        private static LaunchPrivilegeStates GetLaunchPrivilegeStates()
        {
            LaunchPrivilegeStates states = new LaunchPrivilegeStates();

            List<KeyValuePair<string, bool>> tokenPrivileges = GetEnabledPrivileges();
            foreach (KeyValuePair<string, bool> pair in tokenPrivileges)
            {
                if (string.Equals(pair.Key, "SeAssignPrimaryTokenPrivilege", StringComparison.OrdinalIgnoreCase))
                {
                    states.AssignPrimaryTokenPresent = true;
                    states.AssignPrimaryTokenEnabled = pair.Value;
                }
                else if (string.Equals(pair.Key, "SeIncreaseQuotaPrivilege", StringComparison.OrdinalIgnoreCase))
                {
                    states.IncreaseQuotaPresent = true;
                    states.IncreaseQuotaEnabled = pair.Value;
                }
                else if (string.Equals(pair.Key, "SeImpersonatePrivilege", StringComparison.OrdinalIgnoreCase))
                {
                    states.ImpersonatePresent = true;
                    states.ImpersonateEnabled = pair.Value;
                }
            }

            return states;
        }

        /// <summary>Returns the list of privileges present in the current token, with enabled state.</summary>
        private static List<KeyValuePair<string, bool>> GetEnabledPrivileges()
        {
            List<KeyValuePair<string, bool>> result = new List<KeyValuePair<string, bool>>();
            IntPtr currentProcess = GetCurrentProcess();
            IntPtr tokenHandle = IntPtr.Zero;
            try
            {
                if (!OpenProcessToken(currentProcess, TOKEN_QUERY, out tokenHandle))
                {
                    return result;
                }

                uint needed;
                GetTokenInformation(tokenHandle, TokenPrivilegesClass, IntPtr.Zero, 0, out needed);
                if (needed == 0)
                {
                    return result;
                }

                IntPtr buffer = Marshal.AllocHGlobal(unchecked((int)needed));
                try
                {
                    uint returned;
                    if (!GetTokenInformation(tokenHandle, TokenPrivilegesClass, buffer, needed, out returned))
                    {
                        return result;
                    }

                    int privilegeCount = Marshal.ReadInt32(buffer);
                    int entrySize = Marshal.SizeOf(typeof(LUID_AND_ATTRIBUTES));
                    int entriesOffset = IntPtr.Size == 8 ? 8 : 4;

                    for (int i = 0; i < privilegeCount; i++)
                    {
                        IntPtr entry = new IntPtr(
                            buffer.ToInt64() + entriesOffset + (long)i * entrySize);
                        LUID_AND_ATTRIBUTES item = (LUID_AND_ATTRIBUTES)Marshal.PtrToStructure(
                            entry, typeof(LUID_AND_ATTRIBUTES));
                        string name = LookupPrivilegeName(item.Luid);
                        if (name == null)
                        {
                            continue;
                        }
                        bool enabled = (item.Attributes & SE_PRIVILEGE_ENABLED) != 0;
                        result.Add(new KeyValuePair<string, bool>(name, enabled));
                    }
                }
                finally
                {
                    Marshal.FreeHGlobal(buffer);
                }
            }
            finally
            {
                if (tokenHandle != IntPtr.Zero)
                {
                    CloseHandle(tokenHandle);
                }
            }
            return result;
        }

        private static string LookupPrivilegeName(LUID luid)
        {
            // Reverse lookup is not bound here; only the three privileges that matter for
            // LaunchInChild are mapped by LUID. Everything else is ignored.
            string[] names = new string[]
            {
                "SeAssignPrimaryTokenPrivilege",
                "SeIncreaseQuotaPrivilege",
                "SeImpersonatePrivilege",
                "SeTcbPrivilege",
                "SeDebugPrivilege",
                "SeBackupPrivilege",
                "SeRestorePrivilege",
                "SeShutdownPrivilege",
                "SeTakeOwnershipPrivilege",
                "SeCreateTokenPrivilege",
                "SeLoadDriverPrivilege"
            };

            for (int i = 0; i < names.Length; i++)
            {
                LUID known;
                if (LookupPrivilegeValueW(null, names[i], out known) &&
                    known.LowPart == luid.LowPart && known.HighPart == luid.HighPart)
                {
                    return names[i];
                }
            }
            return null;
        }

        #endregion

        #region JSON helpers

        private static StringBuilder AppendJsonBool(StringBuilder sb, string name, bool value)
        {
            return AppendJsonName(sb, name).Append(value ? "true" : "false");
        }

        private static StringBuilder AppendJsonInt(StringBuilder sb, string name, int value)
        {
            return AppendJsonName(sb, name).Append(value.ToString(CultureInfo.InvariantCulture));
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

        private static StringBuilder AppendJsonName(StringBuilder sb, string name)
        {
            AppendJsonQuoted(sb, name);
            return sb.Append(':');
        }

        private static void AppendPrivilegeJson(
            StringBuilder sb, string name, bool present, bool enabled)
        {
            AppendJsonQuoted(sb, name);
            sb.Append(":{");
            AppendJsonBool(sb, "present", present).Append(',');
            AppendJsonBool(sb, "enabled", enabled);
            sb.Append('}');
        }

        internal static void AppendJsonQuoted(StringBuilder sb, string value)
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

        #region Diagnostics

        private static string GetOsVersionString()
        {
            try
            {
                RTL_OSVERSIONINFOW info = new RTL_OSVERSIONINFOW();
                info.dwOSVersionInfoSize = unchecked((uint)Marshal.SizeOf(typeof(RTL_OSVERSIONINFOW)));
                if (RtlGetVersion(ref info) == 0)
                {
                    return string.Format(
                        CultureInfo.InvariantCulture,
                        "{0}.{1}.{2}",
                        info.dwMajorVersion, info.dwMinorVersion, info.dwBuildNumber);
                }
            }
            catch (Exception)
            {
                // fall through to the managed value
            }
            return Environment.OSVersion.VersionString;
        }

        private static string FormatWin32Error(int error)
        {
            try
            {
                string message = new Win32Exception(error).Message;
                return string.Format(CultureInfo.InvariantCulture, "ERROR {0}: {1}", error, message);
            }
            catch (Exception)
            {
                return "ERROR " + error.ToString(CultureInfo.InvariantCulture);
            }
        }

        #endregion
    }
}
