"""Run synthetic codec tests with an explicitly supplied local Opus library."""
from pathlib import Path
import os, subprocess, sys
root=Path(__file__).resolve().parents[1]
library=os.environ.get("STACKCHAN_TEST_OPUS_LIBRARY", "")
if not library or not Path(library).is_file():
    raise SystemExit("Set STACKCHAN_TEST_OPUS_LIBRARY to an existing local libopus; nothing is downloaded")
raise SystemExit(subprocess.run([sys.executable,"-B","-m","unittest","discover","-s","tests","-p","test_pcm_voice.py","-v"],cwd=root,timeout=60).returncode)
