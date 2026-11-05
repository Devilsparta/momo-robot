"""Copy exact private source/tests into a native-plugin-free Flutter test harness.
This checks these modules only, NOT full App/native-plugin/iOS integration.
"""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[1]
source=root/"implementation/app"
target=root/"worktrees/pairing-harness"
files=["lib/network/private_endpoints.dart", "lib/network/private_session.dart",
       "lib/network/private_pairing.dart", "lib/view/popup/private_pairing_dialog.dart",
       "lib/network/private_control_policy.dart", "lib/view/popup/private_connection_banner.dart"]
files += [str(p.relative_to(source)) for p in (source/"test").glob("private_*test.dart")]
for rel in files:
    p=target/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/rel,p)
(target/"pubspec.yaml").write_text("name: stack_chan\nenvironment:\n  sdk: 3.13.5\ndependencies:\n  flutter:\n    sdk: flutter\n  dio: 5.11.1\ndev_dependencies:\n  flutter_test:\n    sdk: flutter\n")
print("Prepared exact-source pairing-only harness; native plugins excluded.")
