#!/usr/bin/env python3
"""What the scheduled job runs: scan new blocks, then publish to GitHub Pages.

Started through Python (not /bin/sh) because macOS only lets the background job
read the Desktop folder via an app that already has that permission.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
print(f"=== {datetime.now():%Y-%m-%d %H:%M} ===", flush=True)
subprocess.run([sys.executable, str(ROOT / "scan.py")], cwd=ROOT, check=True, timeout=20 * 60)
subprocess.run(["/bin/sh", str(ROOT / "publish.sh")], cwd=ROOT, check=True)
