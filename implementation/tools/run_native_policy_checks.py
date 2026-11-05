"""Compile firmware policy assertions with an explicitly supplied compiler; no device I/O."""
from pathlib import Path
import os, subprocess, tempfile
root=Path(__file__).resolve().parents[1]
compiler=os.environ.get("MOMO_CXX", "")
if not compiler or not Path(compiler).is_file():
    raise SystemExit("Set MOMO_CXX to an existing C++17 compiler executable; nothing is installed")
with tempfile.TemporaryDirectory(prefix="momo-policy-") as temp:
    result=subprocess.run([compiler,"-std=c++17","-Wall","-Wextra","-Werror","-c",str(root/"tests/native_state_checks.cpp"),"-I"+str(root/"firmware/main"),"-o",str(Path(temp)/"checks.o")],timeout=30)
    if result.returncode: raise SystemExit(result.returncode)
print("Seven firmware constexpr scenario groups passed; not hardware emulation.")
