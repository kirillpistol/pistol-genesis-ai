"""Run every bundled repository suite; no silently missing integration modules."""
import os
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parent
paths=[root,root.parent/'genesis-level-2',root.parent/'genesis-level-3']
env=dict(os.environ,PYTHONPATH=os.pathsep.join(map(str,paths)),GENESIS_REQUIRE_E2E='1')
for path in paths:
    if not (path/'tests').is_dir():raise SystemExit('Missing repository: '+str(path))
    subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=path,env=env,check=True)
print('PASS: all three repository suites')
