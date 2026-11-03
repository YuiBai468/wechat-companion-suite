"""Build the self-contained dashboard from the public sources."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
source=root/'desktop/dashboard-src'
subprocess.run([sys.executable,str(source/'build-dashboard-v2.py')],check=True,cwd=source)
target=root/'desktop/console.html'
temporary=target.with_name(target.name+'.tmp')
shutil.copyfile(source/'dashboard-v2.html',temporary)
os.replace(temporary,target)
print('Dashboard ready: desktop/console.html')
print('SHA256: '+hashlib.sha256(target.read_bytes()).hexdigest())
