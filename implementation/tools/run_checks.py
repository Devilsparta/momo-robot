"""Run only this project's tests, with a bounded child process lifetime."""
import subprocess
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
try:
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                            cwd=root, timeout=45)
except subprocess.TimeoutExpired:
    print("Test subprocess exceeded 45 seconds. No environment repair attempted.", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(result.returncode)
