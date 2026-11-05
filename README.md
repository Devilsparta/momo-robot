# Momo — StackChan K151 development candidate

Momo 是基于 StackChan K151 的自有后端与固件/App 改动。目标包括设备身份、设备显示一次性码并由手机确认、控制、对话接口、隐私权限及 OTA 验证。手机目标为 iOS；社交和应用中心不在范围内。

这是公开前审阅候选，包含源码覆盖文件和补丁，**不是完整上游 checkout、现成产品或可直接刷写的固件**。不含 SDK、模型、素材、真实凭据或编译产物。尚未上传或发布。

- `implementation/backend/`：Python 标准库服务，默认仅回环 HTTP；身份、配对、归属、工具/记忆与音频协议。
- `implementation/firmware/`、`app/`、`server/`：相对固定上游版本的改动文件；六个 `.patch` 包含对应差异。
- `implementation/tests/`、`implementation/app/test/`：电脑端协议、状态与 UI 模块测试。
- `config/`：仅占位的离线配置，不是生产部署配置。
- [能力矩阵](implementation/CAPABILITY_MATRIX.md)、[协议](implementation/SERVICE_CONTRACT.md)、[音频与 OTA](implementation/AUDIO_OTA_IMPLEMENTATION.md)。

上游固定版本：StackChan `f901cc2969189f3f0e96af1442c0f96e77e1e2c1`；xiaozhi-esp32 `e77dedb1309153bb63fed285772962c920c97dd4`。详见 [baselines.json](baselines.json) 和 [构建说明](implementation/BUILD_AND_RECOVERY.md)。

使用 Python 3.11+，在 `implementation` 目录运行 `python -B tools/run_checks.py`。该测试会开启短时回环 HTTP/WS，不采集传感器或调用外部模型。原生 codec 测试需显式设置 `STACKCHAN_TEST_OPUS_LIBRARY` 为现有本地 libopus 文件；未设置会跳过，不能据此宣称音频已验证。C++ 策略编译需设置 `MOMO_CXX`。本包不自动安装依赖。

在根目录运行 `python -B -m unittest discover -s tests -v` 验证离线占位配置。旧快照专属测试未随包分发，不能将本命令视为上游源码一致性验证。配对 UI 的独立 Flutter harness 可由 `python -B tools/prepare_pairing_harness.py` 生成；它不验证整个 App 或原生插件。

默认没有真实设备身份、用户登录、模型或 OTA 信任根；相机、媒体、外发和工具执行均不默认开放。真实设备、iOS、回声消除、断电升级与恢复仍需验证。身份签发/安全存储、HTTPS、模型接入及发布政策需要后续集成。

Momo 自有代码使用 [MIT](LICENSE)。上游及第三方权利独立，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)、[SOURCE_ORIGINS.json](SOURCE_ORIGINS.json)。本包不代表对所有依赖或素材的统一再许可。

具体候选内容以 `PUBLIC_MANIFEST.json` 与 `SHA256SUMS.txt` 为准；扫描、测试和未验证项见 [PUBLIC_REVIEW.md](PUBLIC_REVIEW.md)。
