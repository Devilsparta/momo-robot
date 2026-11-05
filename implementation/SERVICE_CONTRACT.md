# 私有服务接口与原厂协议对接

固定源码基线见 README。本服务是独立 Python 开发实现，官方 GoFrame 只保留审阅补丁与构建结果；不能视为整个官方云已替换。默认仅 127.0.0.1 HTTP，无账户或真实凭据。所有非健康检查路径都要求 bearer 或经过注册验证器的设备证明。

## 身份与配对（自定义扩展）

可信嵌入 API `Identity.register(mac, key_id, verify)` 注册验证器，HTTP 不接受注册或密钥。`POST /private/v1/identity/challenge` 的 `{mac}` 只获得 60 秒挑战，不能获得会话；必须是已注册设备，5 次/分钟、最多 128 个未完成挑战。签名原文是 ASCII：

```
stackchan-device-session-v1
<12位小写MAC>
<key_id>
<challenge_id>
<nonce>
```

上面表示五行，最后无换行。`challenge_id` 为 32 位小写十六进制，nonce 为 64 位。`POST /private/v1/identity/prove` 为 `{challenge_id,proof}`，任何尝试均消费挑战；检查注册代际、绑定 owner/epoch、期限及注入验证器。返回 300 秒 RAM device_token：未绑定仅 claim scope，已绑定为 device/control/voice。不接受请求指定 owner/scope。服务重启需重新注入注册信息；不生成或保存真实长期凭据。

固件 `install_identity_signer(key_id, signer)` 是可信启动集成接口，签名器不存在时禁用。签名器只签本地重建的上述域分隔消息；请求固定私有管理 HTTPS origin，无重定向。后台约在会话到期前更新；失败不会延长旧会话。当前没有真实签名器/安全存储集成，因此不能声称已能实际开机恢复。

配对：设备 POST `/private/v1/claim` 获 6 位码/claim_id/120 秒；手机 manage scope POST `/private/v1/bind` `{mac,code}`；原设备同一 grant GET `/private/v1/claim/status?claim_id=...` 领取临时 bound token。验证码共享 5 次/120 秒尝试预算、SQLite 唯一 proof 消费。DELETE claim 只取消请求，不撤销已经完成的绑定。解绑清理凭据、未完成挑战、配对、权限、notes/config/name，并使在途请求失效。

## HTTP

管理响应为 `{code,message,data}`，成功 code=0；400 非法，403 拒绝，409 工具超时，501 未实现。OTA 成功不带管理 envelope；经授权的镜像下载返回 application/octet-stream。

| 路径 | 约束与行为 |
|---|---|
| GET /health | 无身份数据、无凭据 |
| POST /private/v1/identity/challenge、/prove | 上述证明交换；不是 MAC 登录 |
| /private/v1/claim、/status、/bind | 设备显示/手机确认 |
| GET /private/v1/capabilities?mac= | owner/control scope；有效 60 秒的逐设备命令集，媒体需策略与该 owner 权限同时允许 |
| GET /private/v1/devices | 当前 owner 的 mac/name 列表 |
| GET/PUT /private/v1/config | 配置白名单存储，未自动下发硬件 |
| PUT /stackChan/v2/device/update | 官方 `{mac,name}` 路径；其他字段 501 |
| POST /stackChan/v2/device/unbind | 手机 manage scope 解绑 |
| GET /stackChan/device/info、/user | 设备 scope；data.name / data.username |
| POST /stackChan/device/unbind | 设备绑定会话解绑 |
| /private/v1/memory[/consent] | opt-in notes CRUD/导出 |
| POST /private/v1/tools/call | 注入工具、参数/权限/时限检查；非官方 MCP |
| /stackChan/v2/dance | App 原生 CRUD 字段，当前 owner + 设备归属检查；数据/数量上限、解绑清理；音乐 URL 默认拒绝 |
| GET /stackChan/[v2/]danceData?id=、/stackChan/dance?id= | 当前 owner 的详情；最后一条兼容当前 App 实际详情调用，不能用 id 跨设备归属 |
| POST /xiaozhi/ota/ | device scope + 匹配 Device-Id，请求 application.version；原始响应含 firmware.version、websocket.version=1、server_time；默认无 URL/token/强制更新；可信发布目录可附加 private_release（manifest/signature） |
| 其他 /stackChan/、/api/ | 501，不伪造成功 |

OTA 私有固件使用管理 origin 的 `/xiaozhi/ota/`，不向独立未知 origin 转发设备 bearer。解析后不写 MQTT/WebSocket NVS。私有 Upgrade 仅消费已经验证的 offer：具体签名/下载/安装/journal/恢复逻辑见 AUDIO_OTA_IMPLEMENTATION。当前未配置生产 Trust，默认拒绝下载/写入。可信 ReleaseCatalog.stage 在 RAM 验证并保存单份发布及明确设备名单；无 HTTP 上传接口。GET `/private/v1/releases/<sha256>` 要求已绑定 device scope，逐块检查撤销并按精确 Content-Length 返回原始镜像。没有真实发布、签名或硬件操作。

## 控制 WebSocket（原厂帧）

`/stackChan/ws?deviceType=StackChan`；手机 `deviceType=App&mac=<12hex>`。绑定身份不可被参数改变。帧：type 1 byte + 大端 payload 长度 4 bytes；整体上限 256 KiB。WS 支持规范的 7/16/64 位长度，不支持扩展或分片。原厂 JSON hello、0x10/11 心跳及两个精确相机文本确认被接受，不放行任意文本命令。

- 3/4：剥离 12 字节目标 MAC 转发，默认 owner 允许。
- 13/14：名称来自已授权 HTTP 存储，不能借通知帧绕过重命名校验。设备发 14 时私有服务回 13，避免官方两端 14 查询/回复互相回声。
- 20：舞蹈帧按当前设备转发，使用运动权限；21 姿态按原厂帧双向转发，但上游固件/手机没有完整实现其含义，不伪造返回。
- 5/6 与 24/25：摄像/音频订阅；第一订阅启动、最后退出停止；跨连接计数，重连清空。默认全拒绝。
- 9/10/11/12/15：通话请求/拒绝/接听/挂断/占用；只有活动通话的手机可发送 Opus。此状态机不代表固件音频硬件链已实现。
- 18/19 与 2：独占手机画面会话，验证目标与拥有该会话的 socket。
- 26：单次拍照，仅向请求者返回，10 秒窗口；固件私有补丁复用实际相机捕获路径，未实测。
- 7 文本消息和 27 LAN 广播仍不开放；不做任意默认转发。

排队写出前复验 bearer、绑定 epoch、目标连接及媒体权限。权限撤销的 idle socket 被 watchdog 关闭；最后订阅者断开会发停止帧。设备断线/替换清空媒体状态，活动媒体手机需重连并显式重订阅。

## 声音与隐私

`/xiaozhi/v1/` 为已绑定 device/voice。可信 Service 注入 codec 后使用 PcmVoice：v1 hello 固定 opus/16000/mono/60ms，真实逐帧解码与 PCM 流式 provider，输出重组/限速，manual/auto、默认关闭的 realtime，abort/alert/TTS 与 session 绑定。没有注入 codec 时仍是旧合成字节适配路径，不能把它称为真实编解码。没有默认 ASR/LLM/TTS 或外部 MCP，所有外发默认拒绝。PCM/provider 声明、内存/时间预算、唤醒预卷和静音端点边界见 [VOICE_PIPELINE.md](VOICE_PIPELINE.md)。App 管理/agent/MCP 的 audience 分离；采集需要短期设备权限，不能仅凭打开页面触发传感器。真实设备和 native 生命周期尚未验证。
