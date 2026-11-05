# AVATAR 音频桥与设备 OTA 安装实现

AVATAR 音频桥与 OTA 状态机已接入固件源码。未配置发布信任时，OTA 在下载/写入前拒绝；本包不含设备验证或可刷写产物。

## AVATAR 音频

代码：`firmware/main/hal/utils/private_audio/`，接入 `hal_ws_avatar.cpp`，资源互斥接入 `board/hal_bridge.cc` 和 `audio.cpp`。

- 管理 WS 原厂帧 1 的 payload 为 Opus；手机方向剥离设备 MAC 后解码；24/25 开关设备采集，设备本地接受通话才允许播放。保留原有接听/拒绝/挂断流程。音频资源不可用时本地接听转为拒绝。
- 采用专用 ESP Opus 编解码器，16 kHz 单声道、20 ms；硬件 24 kHz 输入按第一个麦克风通道取样，忽略第二个参考通道，再用既有 ESP rate converter 转换。输出解码后转为 24 kHz，交给 CoreS3 AudioCodec。TOC 时长/单声道、包长、完整解码样本及已消耗输入均检查。
- 两个专用 pthread 分别采集/编码和解码/播放，网络不在音频线程运行。WS 收包直接送有界播放队列，上行由后台连接任务以 10 ms 周期取出；不等待摄像头/JPEG/UI 工作循环。每方向队列最多六帧；设备回传积压丢弃最旧帧，播放队列满时拒绝新帧。录放音句柄、重采样器只由所属线程操作；通话/连接代际切换重置状态并丢弃旧队列。
- 停止/断线立即撤销发送/播放代际；控制停止不会被满媒体队列挤掉。旧启动帧也不能在停止后重新启用采集。发送检查连接代际，避免旧连接音频发送到新 socket。
- 资源由 AVATAR 独占至对象销毁；AI 应用和麦克风诊断使用同一 ownership guard。线程创建失败可清理；析构先撤销、唤醒并 join 所有已创建线程，再释放 codec/队列。没有直接删除仍访问对象的任务。旧 remote worker 退出后不再通过可能失效的 TaskHandle 发通知，而是等待已有退出信号。
- 原 CoreS3 Read/Write 忽略驱动失败而返回成功，本轮改为返回失败。既有 I2S 驱动 timeout 为 1000 ms；取消不能收回已经送入硬件的一帧，析构会等待在途 I/O 返回，不能承诺停止延迟恒为 20 ms。

这实现了此前为空的 AVATAR 音频分支，但没有实现 AVATAR 声学 AEC。参考通道的存在不等于回声已消除。全双工链路、回声/啸叫、采样转换质量、Wi-Fi 抖动下连续接听/挂断以及 iOS native 行为仍须实机验证；默认不会自动启用麦克风或相机。

## OTA 验证、下载、安装和恢复

代码：`firmware/main/hal/utils/private_ota/`，接入实际 `Ota::CheckVersion` / `Upgrade` / `MarkCurrentVersionValid`，以及 HAL 的维护/进入配网页面路径。`backend/ota.py` 与 `backend/service.py` 提供匹配的清单和受设备身份保护的镜像下载。

1. 可信启动代码必须显式安装 `Trust`：签名验证器、可信时间、健康检查、精确 HTTPS artifact origin、最低版本序号及 allow_install。当前没有任何调用者/真实公钥配置，默认拒绝。可用的 `verify_p256` 实现 SHA-256、NIST P-256 公钥 PEM、DER 签名小写 hex 验证，调用 mbedTLS；也允许以后注入其他经批准验证器。摘要匹配不能替代签名。
2. OTA 响应新增私有扩展 `private_release:{manifest,signature}`，不把未经验证的 URL 放入原厂 firmware.url。固件检查精确字段、重复字段、K151/esp32s3、合法版本、序号、有效期、分区容量、SHA256、精确来源及签名。签名原文与 Python 一致，是键排序、紧凑 ASCII JSON。
3. 经验证 offer 只可消费一次。Upgrade 再验签/检查序号与有效期；TLS 使用系统证书 bundle，禁重定向、状态必须 200、Content-Length 必须精确。仅当 artifact origin 等于设备管理 origin 时转发设备 bearer；不会把管理凭据发往其他发布域名。
4. 验证通过后才调用 ESP OTA 写入非运行分区。4 KiB 分块、持续 SHA256、可取消、读取超时或长度/摘要失败均退出并 abort。末尾再检查到期，`esp_ota_end` 验证 ESP 镜像，成功后才持久化候选记录并切换 boot partition。失败不切换；安装函数自身不重启。
5. 一个 NVS blob 记录已确认序号、候选序号、分区地址、大小和摘要。写 journal 失败不切换；切换失败清候选但不降低已确认序号。启动后区分尚未重启、运行候选和 bootloader 已回滚；清理回滚记录不降低版本下限。
6. 健康回调明确返回 Pending/Healthy/Failed；Pending 不确认、不猜测超时。候选只有 Healthy、重新散列运行分区及 ESP 状态检查通过后才确认有效并持久化新下限。健康/journal 检查每秒最多一次；一次失败回滚调用不在每个显示 tick 重试。明确 Failed 或运行镜像验证失败只对 PENDING_VERIFY 候选调用 ESP 的标记无效并回滚重启入口；没有真实执行。确认后写 journal 失败仍保留候选，后续重试；旧“20 秒自动确认”和“进配网页面就确认”在私有模式被替换。构建已开启 ESP bootloader rollback，但不擅自配置 eFuse 或 secure boot。

服务端 `ReleaseCatalog.stage` 是可信嵌入接口，不是上传 HTTP 端点。只保留一份上限 5,177,344 字节的已验签/已散列 RAM 镜像，明确指定最多 128 个设备；每个下载块复验绑定/撤销，发布撤销会截断连接，不往二进制末尾追加第二个 HTTP 响应。`GET /private/v1/releases/<sha256>` 需要 device scope；当前 CLI 没有默认发布目录、清单、镜像或签名。持久发布存储、域名及真实签名流程取决于部署与身份决定。

版本下限是应用级 NVS 防回滚，不是抵抗物理擦除、恶意固件或篡改闪存的硬件防回滚。当前没有 secure boot/eFuse 信任链，不能作此保证。P-256 辅助代码已编译，真实签名/公钥未配置、未对设备执行密码学验证。

## 验证边界

- 完整 Python 80 项回归：包含真实回环 HTTP/WS，已有真实 libopus/PCM 15 项，以及新发布目录/受保护清单下载、发布撤销、绑定隔离、合成镜像篡改拒绝。仅使用合成波形/非固件测试字节。20 ms 双向测试是电脑端 codec + 实际服务路由，不是 ESP codec/I2S 实测。
- `python tools/run_native_policy_checks.py` 使用已安装 Xtensa 编译器，在编译期执行真实固件纯策略头中的七组 constexpr 场景：所有权、代际、升级成功、验证拒绝、中断/取消/回滚、重启恢复决策、Opus 帧头检查。没有另写一套 Python 状态机来冒充固件测试；这些也不模拟 NVS/闪存驱动或 FreeRTOS 调度。
- 固件真实 ESP-IDF 构建、镜像和分区检查以 STATUS/evidence 为准。新音频线程、重采样、OTA 下载和密码学调用已进入编译。App/Go 未改，本轮不重复其已通过检查；既有 iOS/MSVC 限制仍在。

## 剩余项的依赖

仍需完成以下集成和验证：

- 真实身份来源、安全存储及手机登录：必须确定信任根/注册和用户登录方案，才能完成具体集成并授权真实凭据。已有挑战/签名器/续期/撤销接口不能充当已部署身份。
- 模型与对话编排：需要明确 ASR/LLM/TTS provider、外发类别、工具/记忆许可；真实 Opus/PCM 和取消链路已完成，不能擅自选择真实云服务。
- 真实发布：需要确定发布公钥/轮换、健康验收和恢复规则、可信时间来源、artifact origin 以及存储/部署。当前 verify/health/clock 的接口和具体 P-256 验证已写好；没有生产配置就拒绝安装，不把安全决策换成任意默认值。
- 媒体实测：相机政策需由部署者配置；音频硬件、AEC、iOS 生命周期、断电写入、失败启动和真实回滚需要 K151/iOS。不会仅因编译通过就宣称任何刷坏都能恢复。
- 完整手机原生验证：完整 Windows 原生测试需要 MSVC/Windows SDK，iOS 最终构建需 Mac/Xcode；工具要求见 TOOLCHAINS.md。
- 尚未明确的产品接口：上游姿态读回无 schema、额外设置缺具体下发语义、音乐来源/存储政策未定。这些需要明确产品契约，不能假造原厂成功返回。