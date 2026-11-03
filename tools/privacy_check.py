"""Scan publishable Git content without echoing potential secret values.

Use --staged before a commit. Without Git, source files are scanned while
normal local runtime/build directories are excluded. This is a heuristic
guard; do not use it as a reason to publish real conversations or health data.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path

RULES = {
    "provider_key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "aws_access_key": re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "personal_windows_path": re.compile(rb"(?i)\b[A-Z]:[\\/]+(?:Users[\\/]+(?!Public[\\/]|Default[\\/]|ExampleUser[\\/]|Username[\\/]|runneradmin[\\/])[^\s\"'<>\\/]+|(?:work|gpt|llm|Python\d*)[\\/])"),
}
RUNTIME_FILE = re.compile(r"(?i)(^|/)(?:\.env(?:\..*)?|local\.properties|[^/]*\.(?:jks|keystore|p12|pfx|dpapi|sealed|exe|dll|pdb|apk|aab)|(?:host-state|host-command|child-worker-state|worker-command|bot-running-status|bot-preflight-output|probe-request-child|capture-check|child-probe|service-setup-result|service-state-before|isolation-verdict|account-binding)\.json)$")
PRIVATE_DESKTOP = re.compile(r"(?i)^desktop/(?:bot-config\.json|config\.json|contexts[^/]*\.json|memory[^/]*\.json|conversation-sessions[^/]*\.json|reminders\.json|intents\.json|schedule-state\.json|(?:data|companion-data|dashboard-data|images_out|voice_tmp|wechatauto_logs|secrets)/)")
SKIP_PARTS = {'.git','.gradle','.idea','__pycache__','.venv','venv','node_modules','build','artifacts','dist','bin','obj','.test-tmp'}

def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)

def candidates(root: Path, staged: bool):
    try:
        names=git(root,'ls-files','-z').decode('utf-8').split('\0')
        return [(name,git(root,'show',':'+name) if staged else (root/name).read_bytes())
                for name in names if name and (staged or (root/name).is_file())]
    except (subprocess.CalledProcessError,FileNotFoundError):
        if staged:raise RuntimeError('--staged requires a Git repository')
        return [(p.relative_to(root).as_posix(),p.read_bytes()) for p in root.rglob('*')
                if p.is_file() and not any(part in SKIP_PARTS for part in p.relative_to(root).parts)]

def scan(root: Path, staged=False):
    files=candidates(root,staged)
    findings=[]
    for name,data in files:
        if name.endswith('.env.example') or name=='.env.example':
            pass
        elif RUNTIME_FILE.search(name) or PRIVATE_DESKTOP.search(name):
            findings.append({'file':name,'line':1,'rule':'runtime_or_secret_file'})
        for rule,pattern in RULES.items():
            for match in pattern.finditer(data):
                findings.append({'file':name,'line':data.count(b'\n',0,match.start())+1,'rule':rule})
    return {'ok':not findings,'filesScanned':len(files),'findings':findings}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--staged',action='store_true',help='scan index blobs, not local uncommitted files')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    report=scan(args.root.resolve(),args.staged)
    serialized=json.dumps(report,ensure_ascii=False,indent=2)
    if args.report:args.report.write_text(serialized+'\n',encoding='utf-8')
    print(serialized)
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
