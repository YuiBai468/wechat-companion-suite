import json
import os
import subprocess
import shutil
import sys
import time
import uuid
from datetime import datetime
DEFAULT_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.environ.get('COMPANION_PYTHON') or sys.executable
PWSH = os.environ.get('COMPANION_PWSH') or shutil.which('pwsh') or shutil.which('powershell') or 'powershell.exe'
BOT_PORT = 8765
CONSOLE_URL = 'http://127.0.0.1:8765/console'
LOCK_STALE_SECONDS = 180
HOST_WAIT_SECONDS = 90
PREFLIGHT_WAIT_SECONDS = 120
BOT_WAIT_SECONDS = 120
WORKER_IDENTITY_TOLERANCE_SECONDS = 180
CREATE_NO_WINDOW = 134217728

class SuiteError(Exception):

    def __init__(self, code, reason, detail=None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.detail = detail or {}

def _read_json(path, default=None):
    try:
        with open(path, encoding='utf-8-sig') as handle:
            return json.load(handle)
    except Exception:
        return default

def _write_json(path, payload):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _atomic_write_text(path, text):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    os.replace(tmp, path)

def _parse_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except ValueError:
        pass
    try:
        base = text.split('+')[0].split('-')[0] if 'T' not in text else text
        if len(base) >= 14 and base[:14].isdigit():
            return datetime.strptime(base[:14], '%Y%m%d%H%M%S').timestamp()
    except ValueError:
        pass
    return None

class SuiteController:

    def __init__(self, root=DEFAULT_ROOT, data_dir=None, runner=None, ps_command=None, popen=None, session_api=None, current_session=None, active_console_session=None, list_host_processes=None, process_info=None, port_listener=None, browser_open=None, clock=time.time, sleep=time.sleep, log=None):
        self.root = root
        self.data_dir = data_dir or os.path.join(root, 'suite-state')
        os.makedirs(self.data_dir, exist_ok=True)
        self.clock = clock
        self.sleep = sleep
        self.log = log or (lambda *a: None)
        self.host_exe = os.path.join(root, 'WeChatDesktop.exe')
        self.backend_dll = os.path.join(root, 'backend', 'SessionBackend.dll')
        self.child_state = os.path.join(root, 'child-worker-state.json')
        self.host_command = os.path.join(root, 'host-command.json')
        self.bot_status = os.path.join(root, 'bot-running-status.json')
        self.bot_cmd = os.path.join(root, 'isolated-bot.cmd')
        self.bot_script = os.path.join(root, 'isolated-bot.py')
        self.lock_path = os.path.join(self.data_dir, 'start-suite.lock')
        self.runner = runner or self._default_runner
        self.ps_command = ps_command or (lambda script, timeout=180: self.runner([PWSH, '-NoProfile', '-Command', script], timeout=timeout))
        self.popen = popen or self._default_popen
        self.session_api = session_api or self._default_session_api
        self.current_session = current_session or self._default_current_session
        self.active_console_session = active_console_session or self._default_active_console_session
        self.list_host_processes = list_host_processes or self._default_list_host_processes
        self.process_info = process_info or self._default_process_info
        self.port_listener = port_listener or self._default_port_listener
        self.browser_open = browser_open or self._default_browser_open
        self._host_started_at = None
        self._host_popen = None
        self._validated_host = None

    def _default_runner(self, args, timeout=180):
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace', creationflags=CREATE_NO_WINDOW)

    def _default_popen(self, args, cwd=None):
        return subprocess.Popen(args, cwd=cwd or self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, close_fds=True, creationflags=CREATE_NO_WINDOW)

    def _run_ps(self, script, timeout=180):
        return self.ps_command(script, timeout=timeout)

    def _default_session_api(self):
        script = "$ErrorActionPreference='Stop';Add-Type -Path '%s';$p=[WeChatIsolatedDesktop.SessionBackend]::GetCurrentSessionId();$c=[WeChatIsolatedDesktop.SessionBackend]::GetChildSessionId();@{parentSessionId=$p;childSessionId=$c}|ConvertTo-Json -Compress" % self.backend_dll.replace("'", "''")
        result = self.ps_command(script, timeout=60)
        data = json.loads((result.stdout or '').strip())
        return (int(data.get('parentSessionId') or -1), int(data.get('childSessionId') or -1))

    def _default_current_session(self):
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            sid = wintypes.DWORD(0)
            if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
                return -1
            return int(sid.value)
        except Exception:
            return -1

    def _default_active_console_session(self):
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
            value = int(kernel32.WTSGetActiveConsoleSessionId())
            if value in (4294967295, 0):
                return None
            return value
        except Exception:
            return None

    def _default_list_host_processes(self):
        script = '$rows=Get-CimInstance Win32_Process -Filter "Name=\'WeChatDesktop.exe\'" -ErrorAction SilentlyContinue | Select-Object ProcessId,SessionId,ExecutablePath,CreationDate,CommandLine;if($rows){@($rows)|ConvertTo-Json -Compress}'
        result = self.ps_command(script, timeout=30)
        text = (result.stdout or '').strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        return [self._normalize_process(item) for item in data]

    def _default_process_info(self, pid):
        script = "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d' -ErrorAction SilentlyContinue | Select-Object -First 1;if($p){$p|Select-Object ProcessId,SessionId,ExecutablePath,CreationDate,CommandLine|ConvertTo-Json -Compress}" % int(pid)
        result = self.ps_command(script, timeout=30)
        text = (result.stdout or '').strip()
        return self._normalize_process(json.loads(text)) if text else None

    @staticmethod
    def _normalize_process(item):
        if not isinstance(item, dict):
            return None
        return {'pid': int(item.get('ProcessId') or item.get('pid') or 0), 'sessionId': int(item.get('SessionId') or item.get('sessionId') or -1), 'exePath': str(item.get('ExecutablePath') or item.get('exePath') or ''), 'createdAt': _parse_time(item.get('CreationDate') or item.get('createdAt')), 'commandLine': str(item.get('CommandLine') or item.get('commandLine') or '')}

    def _default_port_listener(self):
        script = '$c=Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;if($c){@{pid=$c.OwningProcess}|ConvertTo-Json -Compress}' % BOT_PORT
        result = self.ps_command(script, timeout=30)
        text = (result.stdout or '').strip()
        if not text:
            return None
        return int(json.loads(text).get('pid') or 0) or None

    def _default_browser_open(self, url):
        os.startfile(url)

    def _pid_alive(self, pid):
        pid = int(pid or 0)
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        try:
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel32.OpenProcess.restype = wintypes.HANDLE
                kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel32.OpenProcess(4096, False, pid)
                if not handle:
                    return False
                try:
                    code = wintypes.DWORD(0)
                    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                        return False
                    return code.value == 259
                finally:
                    kernel32.CloseHandle(handle)
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def _acquire_lock(self):
        current = _read_json(self.lock_path)
        now = self.clock()
        if isinstance(current, dict):
            age = now - float(current.get('startedAt') or 0)
            owner = int(current.get('pid') or 0)
            if age < LOCK_STALE_SECONDS and owner and self._pid_alive(owner):
                return False
            try:
                os.remove(self.lock_path)
            except OSError:
                pass
        payload = {'pid': os.getpid(), 'startedAt': now, 'nonce': uuid.uuid4().hex}
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle)
            return True
        except FileExistsError:
            return False

    def _release_lock(self):
        try:
            current = _read_json(self.lock_path)
            if isinstance(current, dict) and int(current.get('pid') or 0) == os.getpid():
                os.remove(self.lock_path)
        except OSError:
            pass

    def _host_processes(self, parent=None):
        hosts = self._filter_hosts(self.list_host_processes() or [])
        if parent is not None:
            hosts = [host for host in hosts if host['sessionId'] == int(parent)]
        return hosts

    def _filter_hosts(self, processes):
        hosts = []
        for process in processes or []:
            process = self._normalize_process(process)
            if not process:
                continue
            if os.path.normcase(os.path.abspath(process['exePath'] or '')) != os.path.normcase(os.path.abspath(self.host_exe)):
                continue
            hosts.append(process)
        return hosts

    def _validate_parent_context(self):
        current = int(self.current_session() or -1)
        if current <= 0:
            raise SuiteError('session_query_failed', '无法确定当前会话')
        active = self.active_console_session()
        if not active:
            raise SuiteError('session_query_failed', '无法确认活动父会话（不会冒险在子会话启动）')
        marker = _read_json(self.child_state)
        marker_parent = 0
        if isinstance(marker, dict):
            marker_parent = int(marker.get('parentSessionId') or 0)
            marker_child = int(marker.get('sessionId') or 0)
            if marker_parent and marker_parent != current:
                raise SuiteError('not_parent_session', '一键启动只能在父会话运行（当前会话不是 worker 的父会话）')
            if marker_child and marker_child == current:
                raise SuiteError('not_parent_session', '一键启动不能在子会话中运行')
        if current != active and marker_parent != current:
            raise SuiteError('not_parent_session', '当前不是活动父会话（疑似在子会话中运行）')
        raw_parent = [self._normalize_process(item) for item in self.list_host_processes() or []]
        raw_parent = [item for item in raw_parent if item and item['sessionId'] == current]
        hosts = self._filter_hosts(raw_parent)
        if raw_parent and (not hosts):
            raise SuiteError('host_identity_mismatch', '发现同名 host 但可执行文件路径不匹配，拒绝重复启动')
        if hosts:
            self._validated_host = hosts[0]
        return (current, hosts)

    def _running_bot_session(self, parent):
        pid = self.port_listener()
        if not pid:
            return None
        info = self._normalize_process(self.process_info(pid))
        if not info or 'isolated-bot.py' not in str(info['commandLine']):
            return None
        if info['sessionId'] <= 0 or info['sessionId'] == parent:
            return None
        return info['sessionId']

    def _start_host_async(self):
        if self._host_popen is not None:
            return
        if not os.path.isfile(self.host_exe):
            raise SuiteError('host_missing', '找不到 WeChatDesktop.exe')
        self._host_started_at = self.clock()
        try:
            self._host_popen = self.popen([self.host_exe, '--background'], cwd=self.root)
        except Exception as exc:
            raise SuiteError('host_start_failed', 'host 后台启动失败：%s' % type(exc).__name__)
        self.log('host started in background')

    def _wait_host_process(self, parent, deadline):
        while self.clock() < deadline:
            hosts = self._host_processes(parent)
            if hosts:
                self._validated_host = hosts[0]
                return hosts[0]
            self.sleep(1.0)
        return None

    def _worker_identity(self, parent, child):
        marker = _read_json(self.child_state)
        if not isinstance(marker, dict):
            return None
        marker_parent = int(marker.get('parentSessionId') or 0)
        marker_child = int(marker.get('sessionId') or 0)
        worker_pid = int(marker.get('processId') or 0)
        if marker_parent != parent or marker_child != child or worker_pid <= 0:
            return None
        info = self._normalize_process(self.process_info(worker_pid))
        if not info or info['pid'] != worker_pid:
            return None
        if info['sessionId'] != child:
            return None
        if os.path.normcase(os.path.abspath(info['exePath'] or '')) != os.path.normcase(os.path.abspath(self.host_exe)):
            return None
        marker_started = _parse_time(marker.get('started'))
        if marker_started is None:
            try:
                marker_started = os.path.getmtime(self.child_state)
            except OSError:
                return None
        created = info['createdAt']
        if created is not None and abs(marker_started - created) > WORKER_IDENTITY_TOLERANCE_SECONDS:
            return None
        if self._host_started_at is not None and marker_started + 5 < self._host_started_at:
            return None
        return {'marker': marker, 'process': info}

    def _resolve_child(self, parent, hosts):
        deadline = self.clock() + HOST_WAIT_SECONDS
        started_host = False
        while self.clock() < deadline:
            running = self._running_bot_session(parent)
            if running:
                return running
            try:
                parent_now, child = self.session_api()
            except Exception:
                parent_now, child = (parent, -1)
            if parent_now == parent and child > 0 and (child != parent):
                if self._worker_identity(parent, child):
                    return child
            if not hosts and (not started_host):
                self._start_host_async()
                started_host = True
            if self._host_popen is not None and self._validated_host is None:
                self._wait_host_process(parent, min(deadline, self.clock() + 15))
            hosts = self._host_processes(parent)
            self.sleep(1.0)
        raise SuiteError('child_not_ready', '子会话未就绪：需要权威 childId、存活且身份匹配的 worker 标记/PID（不会退回父会话或复用旧 PID）')

    def _ensure_valid_host(self, parent):
        raw = [self._normalize_process(item) for item in self.list_host_processes() or []]
        raw_parent = [item for item in raw if item and item['sessionId'] == int(parent)]
        hosts = self._filter_hosts(raw_parent)
        if raw_parent and (not hosts):
            raise SuiteError('host_identity_mismatch', '发现同名 host 但可执行文件路径不匹配，拒绝重复启动')
        if hosts:
            self._validated_host = hosts[0]
            return
        self._start_host_async()
        host = self._wait_host_process(parent, self.clock() + HOST_WAIT_SECONDS)
        if host is None:
            raise SuiteError('host_not_ready', '宿主未能在父会话恢复，未启动机器人')

    def _task_script(self, name, exe, arguments, workdir, child):
        return "$ErrorActionPreference='Stop';$s=New-Object -ComObject Schedule.Service;$s.Connect();$f=$s.GetFolder('\\');$n='%s';$def=$s.NewTask(0);$def.Principal.LogonType=3;$def.Principal.RunLevel=0;$def.Settings.AllowDemandStart=$true;$def.Settings.DisallowStartIfOnBatteries=$false;$def.Settings.StopIfGoingOnBatteries=$false;$def.Settings.ExecutionTimeLimit='PT0S';$a=$def.Actions.Create(0);$a.Path='%s';$a.Arguments='%s';$a.WorkingDirectory='%s';$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$t=$f.RegisterTaskDefinition($n,$def,2,$sid,$null,3,$null);$t.RunEx($null,4,%d,$null)" % (name, exe.replace("'", "''"), arguments.replace("'", "''"), workdir.replace("'", "''"), int(child))

    def _delete_task_script(self, name):
        return "$ErrorActionPreference='SilentlyContinue';$s=New-Object -ComObject Schedule.Service;$s.Connect();$s.GetFolder('\\').DeleteTask('%s',0)" % name.replace("'", "''")

    def _register_and_run_task(self, name, exe, arguments, child):
        self.runner([PWSH, '-NoProfile', '-Command', self._task_script(name, exe, arguments, self.root, child)], timeout=60)

    def _delete_task(self, name):
        try:
            self.runner([PWSH, '-NoProfile', '-Command', self._delete_task_script(name)], timeout=30)
        except Exception:
            pass

    def _run_preflight(self, parent, child):
        nonce = uuid.uuid4().hex
        out_path = os.path.join(self.data_dir, 'preflight-%s.json' % nonce)
        log_path = os.path.join(self.data_dir, 'preflight-%s.log' % nonce)
        try:
            os.remove(out_path)
        except OSError:
            pass
        args = '/c ""%s" -X utf8 "%s" --check --parent-session %d --expected-session %d > "%s" 2>"%s""' % (PYTHON, self.bot_script, parent, child, out_path, log_path)
        name = 'WeChatSuitePreflight-' + uuid.uuid4().hex
        registered = False
        try:
            self._register_and_run_task(name, 'C:\\Windows\\System32\\cmd.exe', args, child)
            registered = True
            deadline = self.clock() + PREFLIGHT_WAIT_SECONDS
            while self.clock() < deadline:
                if os.path.isfile(out_path):
                    payload = _read_json(out_path)
                    if not isinstance(payload, dict) or 'ok' not in payload:
                        self.sleep(1.0)
                        continue
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
                    ok = bool(payload.get('ok')) and bool(payload.get('accountBound'))
                    reason = str(payload.get('reason') or '')
                    if ok:
                        return {'ok': True, 'nonce': nonce, 'reason': reason}
                    lowered = reason.lower()
                    needs_login = any((token in lowered for token in ('weixin', 'wxid', 'account', 'login', '登录')))
                    return {'ok': False, 'nonce': nonce, 'code': 'needs_login' if needs_login else 'preflight_failed', 'reason': reason or 'preflight 未通过'}
                self.sleep(1.0)
            return {'ok': False, 'nonce': nonce, 'code': 'preflight_failed', 'reason': '本次 preflight 未产生结果（不使用过期成功）'}
        except Exception as exc:
            return {'ok': False, 'nonce': nonce, 'code': 'preflight_failed', 'reason': 'preflight 启动失败：%s' % type(exc).__name__}
        finally:
            if registered:
                self._delete_task(name)
            for path in (out_path, log_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _launch_bot(self, parent, child):
        name = 'WeChatSuiteBot-' + uuid.uuid4().hex
        try:
            try:
                self._register_and_run_task(name, 'C:\\Windows\\System32\\cmd.exe', '/c ""%s" %d %d"' % (self.bot_cmd, parent, child), child)
            except Exception as exc:
                raise SuiteError('launch_failed', '子会话启动任务失败：%s' % type(exc).__name__)
            deadline = self.clock() + BOT_WAIT_SECONDS
            pid = self._wait_bot(child, deadline)
            if not pid:
                raise SuiteError('bot_not_ready', '机器人在子会话启动超时，请查看启动日志')
            return pid
        finally:
            self._delete_task(name)

    def _wait_bot(self, child, deadline):
        while self.clock() < deadline:
            pid = self.port_listener()
            if pid:
                info = self._normalize_process(self.process_info(pid))
                if info and info['sessionId'] == child and ('isolated-bot.py' in str(info['commandLine'])):
                    return pid
            self.sleep(1.0)
        return None

    def _hide_host(self):
        try:
            _atomic_write_text(self.host_command, json.dumps({'command': 'hide', 'nonce': uuid.uuid4().hex}, ensure_ascii=False))
        except OSError:
            pass

    def _open_console(self):
        try:
            self.browser_open(CONSOLE_URL)
        except Exception:
            pass

    def _status(self, ok, action, reason, pid=None, parent=None, child=None, extra=None):
        status = {'ok': bool(ok), 'action': action, 'reason': reason, 'botPid': pid, 'parentSessionId': parent, 'sessionId': child, 'monitorPort': BOT_PORT, 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        if extra:
            status.update(extra)
        return status

    def run(self, parent_session=None):
        if not self._acquire_lock():
            return self._status(True, 'busy', '另一个启动流程正在进行，请稍候', extra={'reused': True})
        try:
            parent, hosts = self._validate_parent_context()
            if parent_session is not None and int(parent_session) != parent:
                raise SuiteError('wrong_parent_session', '当前不在预期父会话，拒绝启动')
            child = self._resolve_child(parent, hosts)
            existing_pid = self.port_listener()
            if existing_pid:
                info = self._normalize_process(self.process_info(existing_pid))
                if not info or info['sessionId'] != child or 'isolated-bot.py' not in str(info['commandLine']):
                    raise SuiteError('port_conflict', '8765 被非子会话机器人进程占用，未做任何改动')
                self._ensure_valid_host(parent)
                self._hide_host()
                self._open_console()
                return self._status(True, 'already_running', '机器人与看板已在子会话运行', pid=existing_pid, parent=parent, child=child)
            self._ensure_valid_host(parent)
            preflight = self._run_preflight(parent, child)
            if not preflight.get('ok'):
                code = 'needs_login' if preflight.get('code') == 'needs_login' else 'preflight_failed'
                raise SuiteError(code, preflight.get('reason') or '启动前检查未通过', {'preflight': preflight})
            pid = self._launch_bot(parent, child)
            self._hide_host()
            self._open_console()
            result = self._status(True, 'started', '机器人与看板已在子会话启动', pid=pid, parent=parent, child=child)
            _write_json(self.bot_status, result)
            return result
        finally:
            self._release_lock()

def main(argv=None):
    controller = SuiteController()
    try:
        result = controller.run()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('ok') else 1
    except SuiteError as exc:
        print(json.dumps({'ok': False, 'error': exc.code, 'reason': exc.reason, 'detail': exc.detail, 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z')}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': 'internal_error', 'reason': '启动器内部错误：%s' % type(exc).__name__, 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z')}, ensure_ascii=False))
        return 1
if __name__ == '__main__':
    sys.exit(main())
