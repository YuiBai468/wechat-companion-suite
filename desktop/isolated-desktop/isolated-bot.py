import argparse
import contextlib
import ctypes
import io
import json
import os
import re
import runpy
import sys
from ctypes import wintypes
ENTRY_VERSION = '1.0'
BOT_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'wechat-bot.py')
SM_REMOTESESSION = 4096
TH32CS_SNAPPROCESS = 2
WEIXIN_EXE = 'weixin.exe'
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_SESSION_REFUSED = 2
EXIT_ENVIRONMENT_REFUSED = 3
EXIT_BINDING_REFUSED = 4
_K32 = None
_USER32 = None

class GuardResult:

    def __init__(self, ok, exit_code, reason):
        self.ok = bool(ok)
        self.exit_code = int(exit_code)
        self.reason = str(reason)

class BindingRefused(Exception):
    pass

class Binding:

    def __init__(self, pid, pids, db_root, account, cfg_wxid, sdk):
        self.pid = int(pid)
        self.pids = tuple((int(p) for p in pids))
        self.db_root = db_root
        self.account = account
        self.cfg_wxid = cfg_wxid
        self.sdk = sdk

class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG), ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

def _win32():
    global _K32, _USER32
    if _K32 is not None:
        return (_K32, _USER32)
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.ProcessIdToSessionId.restype = wintypes.BOOL
    k32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _K32, _USER32 = (k32, user32)
    return (_K32, _USER32)

def win32_current_session():
    k32, _ = _win32()
    sid = wintypes.DWORD(0)
    if not k32.ProcessIdToSessionId(os.getpid() & 4294967295, ctypes.byref(sid)):
        raise OSError(ctypes.get_last_error(), 'ProcessIdToSessionId failed')
    return int(sid.value)

def win32_is_remote_session():
    _, user32 = _win32()
    return bool(user32.GetSystemMetrics(SM_REMOTESESSION))

def win32_process_session(pid):
    k32, _ = _win32()
    sid = wintypes.DWORD(0)
    if not k32.ProcessIdToSessionId(int(pid) & 4294967295, ctypes.byref(sid)):
        raise OSError(ctypes.get_last_error(), 'ProcessIdToSessionId failed')
    return int(sid.value)

def win32_enumerate_processes():
    k32, _ = _win32()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snap or snap == invalid:
        raise OSError(ctypes.get_last_error(), 'CreateToolhelp32Snapshot failed')
    entries = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            entries.append((int(entry.th32ProcessID), str(entry.szExeFile)))
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return entries

def collect_process_entries():
    result = []
    for pid, name in win32_enumerate_processes():
        try:
            session = win32_process_session(pid)
        except OSError:
            session = -1
        result.append((pid, name, session))
    return result

def evaluate_guard(parent_session, expected_session, own_session, remote_session, session_error=None):
    if parent_session is None or expected_session is None:
        return GuardResult(False, EXIT_SESSION_REFUSED, 'missing session arguments')
    if parent_session <= 0 or expected_session <= 0:
        return GuardResult(False, EXIT_SESSION_REFUSED, 'parent-session and expected-session must both be > 0')
    if parent_session == expected_session:
        return GuardResult(False, EXIT_SESSION_REFUSED, 'expected-session must differ from parent-session')
    if session_error:
        return GuardResult(False, EXIT_ENVIRONMENT_REFUSED, 'cannot determine current session: %s' % session_error)
    if own_session is None or own_session <= 0:
        return GuardResult(False, EXIT_ENVIRONMENT_REFUSED, 'cannot determine current session')
    if own_session != expected_session:
        return GuardResult(False, EXIT_SESSION_REFUSED, 'current session %d does not match expected-session %d' % (own_session, expected_session))
    if not remote_session:
        return GuardResult(False, EXIT_ENVIRONMENT_REFUSED, 'current session is not a remote/nested session (SM_REMOTESESSION=0); refusing to run in the main local desktop')
    return GuardResult(True, EXIT_OK, 'session guard passed')

def run_guard(parent_session, expected_session, session_getter=None, remote_getter=None):
    session_getter = session_getter or win32_current_session
    remote_getter = remote_getter or win32_is_remote_session
    own = None
    remote = False
    session_error = None
    try:
        own = session_getter()
    except Exception as exc:
        session_error = '%s: %s' % (type(exc).__name__, exc)
    if session_error is None:
        try:
            remote = bool(remote_getter())
        except Exception as exc:
            session_error = '%s: %s' % (type(exc).__name__, exc)
    return evaluate_guard(parent_session, expected_session, own, remote, session_error=session_error)

def select_session_weixin_pids(process_entries, expected_session):
    pids = []
    for entry in process_entries:
        try:
            pid, name, session = entry
        except (TypeError, ValueError):
            continue
        if str(name).casefold() != WEIXIN_EXE:
            continue
        try:
            if int(session) != int(expected_session):
                continue
            pids.append(int(pid))
        except (TypeError, ValueError):
            continue
    return sorted(set(pids))

def find_session_weixin_pids(expected_session, entries_provider=None):
    provider = entries_provider or collect_process_entries
    return select_session_weixin_pids(provider(), expected_session)

def normalize_account_wxid(account_dir_name):
    return re.sub('_\\w{4}$', '', str(account_dir_name))

def discover_account_dirs(db_root):
    result = []
    try:
        names = os.listdir(db_root)
    except OSError:
        return result
    for name in names:
        path = os.path.join(db_root, name)
        if os.path.isdir(path) and os.path.isdir(os.path.join(path, 'db_storage')):
            result.append(name)
    return sorted(result)

def match_account_dir(cfg_wxid, account_dirs):
    if not cfg_wxid:
        raise BindingRefused('the SDK process-cfg extractor did not return a wxid for the current-session WeChat process')
    target = str(cfg_wxid).casefold()
    matches = [d for d in account_dirs if normalize_account_wxid(d).casefold() == target]
    if not matches:
        raise BindingRefused('no account directory matches the current-session WeChat wxid (refusing to fall back to a recently modified directory)')
    if len(matches) > 1:
        raise BindingRefused('multiple account directories match the current-session WeChat wxid; refusing to guess')
    return matches[0]

def _load_sdk():
    import wechatauto.db as wechatauto_db
    return wechatauto_db

def resolve_binding(expected_session, pid_finder=None, sdk=None):
    pid_finder = pid_finder or find_session_weixin_pids
    sdk = sdk or _load_sdk()
    pids = list(pid_finder(expected_session))
    if not pids:
        raise BindingRefused('no Weixin.exe process was found in the current session %d' % expected_session)
    cfg = None
    chosen_pid = None
    for pid in pids:
        try:
            got = sdk.extract_master_key_from_cfg(pid)
        except Exception:
            got = None
        if got and len(got) >= 3 and got[2]:
            cfg = got
            chosen_pid = pid
            break
    if cfg is None:
        raise BindingRefused('could not extract the current-session WeChat account id from the process cfg (WeChat not logged in, version changed, or access denied)')
    cfg_wxid = cfg[2]
    try:
        db_root = sdk.auto_detect_db_dir()
    except Exception:
        db_root = None
    if not db_root:
        raise BindingRefused('WeChat database root could not be detected')
    account_dirs = discover_account_dirs(db_root)
    account = match_account_dir(cfg_wxid, account_dirs)
    return Binding(chosen_pid, pids, db_root, account, cfg_wxid, sdk)

def install_sdk_guards(db_class, db_module, allowed_pids, bound_account, bound_db_root):
    if getattr(db_class, '_isolated_guards_installed', False):
        return
    allowed = tuple(sorted(set((int(p) for p in allowed_pids))))
    allowed_set = set(allowed)
    root_norm = os.path.normcase(os.path.abspath(bound_db_root))

    def _find_weixin_pids(self):
        return list(allowed)

    def _pick_account(self):
        root = getattr(self, 'db_dir', None)
        if not root or os.path.normcase(os.path.abspath(root)) != root_norm:
            raise RuntimeError('isolated-bot: refusing to pick an account for an unexpected database root')
        return bound_account

    def _try_other_accounts(self):
        return False

    def _select_account_by_keys(self, cands):
        return False

    def _save_keys(self):
        return None

    def _auto_diagnose_key_failure(self, rel):
        return None
    original_cfg = db_module.extract_master_key_from_cfg

    def _extract_master_key_from_cfg(pid):
        try:
            if int(pid) not in allowed_set:
                return None
        except (TypeError, ValueError):
            return None
        return original_cfg(pid)
    db_class._find_weixin_pids = _find_weixin_pids
    db_class._pick_account = _pick_account
    db_class._try_other_accounts = _try_other_accounts
    db_class._select_account_by_keys = _select_account_by_keys
    db_class._save_keys = _save_keys
    db_class._auto_diagnose_key_failure = _auto_diagnose_key_failure
    db_module.extract_master_key_from_cfg = _extract_master_key_from_cfg
    db_class._isolated_guards_installed = True

@contextlib.contextmanager
def _quiet_io():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        yield buffer

def verify_binding(binding):
    install_sdk_guards(binding.sdk.WeChatDB, binding.sdk, binding.pids, binding.account, binding.db_root)
    with _quiet_io():
        db = binding.sdk.WeChatDB(db_dir=binding.db_root, account=binding.account)
    account_ok = getattr(db, 'account', None) == binding.account
    db_files = list(getattr(db, '_db_files', []) or [])
    unkeyed = list(getattr(db, 'unkeyed', []) or [])
    total = len(db_files)
    keyed = max(0, total - len(unkeyed))
    if not account_ok:
        return (False, total, keyed, 'SDK switched away from the bound account directory')
    if total <= 0:
        return (False, total, keyed, 'bound account has no database files')
    if keyed <= 0:
        return (False, total, keyed, 'bound account directory was selected but no database key could be verified; refusing to start the bot')
    return (True, total, keyed, 'bound to the current-session WeChat account')

def launch_bot(binding, expected_session):
    install_sdk_guards(binding.sdk.WeChatDB, binding.sdk, binding.pids, binding.account, binding.db_root)
    sys.argv = [BOT_SCRIPT]
    bot_dir = os.path.dirname(os.path.abspath(BOT_SCRIPT))
    if bot_dir not in sys.path:
        sys.path.insert(0, bot_dir)
    print('isolated-bot: guard ok session=%d parent!=expected remote=1 weixinPid=%d weixinPidCount=%d; launching %s' % (expected_session, binding.pid, len(binding.pids), os.path.basename(BOT_SCRIPT)), file=sys.stderr, flush=True)
    runpy.run_path(BOT_SCRIPT, run_name='__main__')

def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='isolated-bot.py', description='Run the WeChat bot bound to the current Windows child session only.')
    parser.add_argument('--parent-session', type=int, required=True, help='session id of the parent (main desktop) session')
    parser.add_argument('--expected-session', type=int, required=True, help='session id this process must be running in')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='only check session + current-session WeChat + DB binding')
    mode.add_argument('--guard-only', action='store_true', help='only verify the session guard and exit')
    return parser.parse_args(argv)

def _emit(payload, stream=None):
    print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), file=stream or sys.stdout, flush=True)

def _guard_payload(mode, args, guard, own_session, remote_session):
    return {'ok': guard.ok, 'mode': mode, 'sessionId': own_session, 'parentSession': args.parent_session, 'expectedSession': args.expected_session, 'remoteSession': bool(remote_session), 'reason': guard.reason}

def _check_payload(binding, ok, reason):
    payload = {'ok': bool(ok), 'mode': 'check', 'accountBound': bool(ok), 'reason': reason}
    if binding is not None:
        payload.update({'weixinPid': binding.pid, 'weixinPidCount': len(binding.pids)})
    return payload

def main(argv=None):
    args = parse_args(argv)
    own = None
    remote = False
    guard = run_guard(args.parent_session, args.expected_session, session_getter=win32_current_session, remote_getter=win32_is_remote_session)
    try:
        own = win32_current_session()
    except Exception:
        own = None
    try:
        remote = win32_is_remote_session()
    except Exception:
        remote = False
    if args.guard_only:
        _emit(_guard_payload('guard-only', args, guard, own, remote))
        return EXIT_OK if guard.ok else guard.exit_code
    if not guard.ok:
        if args.check:
            _emit(_guard_payload('check', args, guard, own, remote))
        else:
            print('isolated-bot: refused: %s' % guard.reason, file=sys.stderr, flush=True)
        return guard.exit_code
    binding = None
    try:
        with _quiet_io():
            binding = resolve_binding(args.expected_session)
    except BindingRefused as exc:
        if args.check:
            _emit(_check_payload(None, False, str(exc)))
        else:
            print('isolated-bot: binding refused: %s' % exc, file=sys.stderr, flush=True)
        return EXIT_BINDING_REFUSED
    except Exception as exc:
        reason = 'unexpected error while binding: %s' % type(exc).__name__
        if args.check:
            _emit(_check_payload(None, False, reason))
        else:
            print('isolated-bot: %s' % reason, file=sys.stderr, flush=True)
        return EXIT_UNEXPECTED
    if args.check:
        try:
            ok, _db_total, _db_keyed, reason = verify_binding(binding)
        except Exception as exc:
            ok = False
            reason = 'binding verification failed: %s' % type(exc).__name__
        _emit(_check_payload(binding, ok, reason))
        return EXIT_OK if ok else EXIT_BINDING_REFUSED
    try:
        launch_bot(binding, args.expected_session)
        return EXIT_OK
    except KeyboardInterrupt:
        print('isolated-bot: interrupted', file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        print('isolated-bot: unexpected error while starting the bot: %s' % type(exc).__name__, file=sys.stderr, flush=True)
        return EXIT_UNEXPECTED
if __name__ == '__main__':
    sys.exit(main())
