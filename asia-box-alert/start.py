import os
import runpy
from pathlib import Path

root = Path(__file__).resolve().parent
os.chdir(root)
runpy.run_path(str(root / "app.py"), run_name="__main__")
