"""Launch bundled sibling repositories without installing Python dependencies."""
import os
from pathlib import Path
import sys
root=Path(__file__).resolve().parent
paths=[root,root.parent/'genesis-level-2',root.parent/'genesis-level-3']
for path in reversed(paths):sys.path.insert(0,str(path))
os.environ['PYTHONPATH']=os.pathsep.join(map(str,paths))+os.pathsep+os.environ.get('PYTHONPATH','')
if __name__=='__main__':
    from local_panel.server import main
    main()
