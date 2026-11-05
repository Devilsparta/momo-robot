# 真实 Opus 与本地流式语音链路

验证仅使用合成波与静音。此结果证明电脑端编解码和协议链路，不证明 ASR/LLM/TTS、原厂音色、设备回声消除或 iOS 通话可用。

## 实现与运行

- `backend/opus.py`：显式本地 libopus 路径，ctypes C ABI；16 kHz、单声道、PCM16 little-endian；支持 20/60 ms 原始 Opus 帧。解码前检查包长度、声道和实际时长，解码后检查样本数。编码/解码状态按连接持有，abort 不重置历史，新连接创建新状态，关闭释放。
- `backend/pcm_voice.py`：实际 `/xiaozhi/v1/` 的 v1 hello/listen/abort/TTS 路径。输入在 listen.stop 前解码并逐帧交给处理器；输出任意 PCM 字节分块重组为 60 ms 包，仅最后一个完整样本的短帧补零，奇数字节尾拒绝。按至少 60 ms 间隔发送，避免响应突发灌满播放队列；取消后不再发送旧代际输出。
- `backend/vad.py`：确定性 RMS 静音端点，支持原厂默认 auto；不是训练过的语音 VAD。60 ms 帧 RMS 阈值 500，至少 2 个有声帧、10 个静音帧结束；无声 84 帧结束。声学阈值尚未用实机校准。
- realtime 默认拒绝。可信集成显式 `allow_realtime=True` 后，播放中 2 个有声帧可打断，保留这两帧至固件下一次 listen.start。第一帧 TTS 之前不使用该重启逻辑，因为原厂只在 speaking 状态处理 tts.stop。此为状态机测试，不是设备 AEC 或无回声保证。
- 上游唤醒可在 listen.start 前发送音频和 listen.detect：最多 64 个预卷包只校验/解码并丢弃，唤醒文本不落盘、不送 provider。auto 的后续尾包最多 50 帧；realtime 响应期间最多 500 帧。输入队列最多 8 个 PCM 帧加 EOF；过载明确拒绝，单轮输入最多 30 秒，PCM 输出最多 30 秒，默认总处理期限 30 秒。
- provider 异常不回传原始异常内容。按真实 `application.cc` 分支发送 `alert` 的 status/message/emotion，再用 tts.start/stop 完成设备状态；空响应也完成状态，避免停在等待中。

无真实 provider 默认仍不可对话。可信嵌入方式为 `Service(codec=OpusLibrary(absolute_local_library_path), provider=provider, privacy=Privacy(...))`，服务仍只绑定回环。库路径不是 HTTP 输入。Provider 必须声明 `pcm_format=(16000,1,"s16le")`、`egress_categories=frozenset(...)` 并实现 `async respond_pcm(chunks)`，消费异步 PCM 输入并产生 PCM bytes。必须及时消费输入；输出在输入结束前不会下发设备。声明的外发类别需逐项允许；空集合仅适用于真正本地处理器。第三方 provider 是可信代码，声明检查不是 OS 网络沙箱。

`python tools/run_voice_checks.py` 使用 STACKCHAN_TEST_OPUS_LIBRARY 指定的本地 libopus，不下载任何内容；缺失时退出。测试仅开启短时回环 TCP。普通 `python tools/run_checks.py` 未指定库时会跳过原生 codec 测试，不应把该结果当编解码已测。完整含 codec 的回归使用同一 `STACKCHAN_TEST_OPUS_LIBRARY` 环境变量，见结构化验证记录。

## 证据与准确边界

本轮累计 15 项专用测试涵盖真实 20/60 ms 编解码、Opus 延迟补偿后的波形相关性、无效/错误时长帧、PCM 字节边界/填充、流式输入、60 ms 输出节奏、背压、默认外发拒绝、auto 静音结束、realtime 门控/打断、迟到输出、超时/撤销、实际 WebSocket 接收/编码/解绑、断线取消及新会话拒绝旧 session_id。

另用已安装 FFmpeg 将测试构造的 Ogg 包装解码，核对样本数和相关性。此验证独立调用 FFmpeg 的容器/编解码接线，FFmpeg 也使用 libopus，不能说是完全独立的第二套编解码算法。测试未使用任何录音文件，亦未输出持久音频文件。

库版本 `libopus 1.5.2`；来自固定 App 依赖 `opus_codec_windows 3.0.5` 的既有 x64 DLL blob。来源/哈希见 `provenance/voice-codec.json`，原许可见 `provenance/opus-license.txt`。审阅包不分发 DLL/FFmpeg。

## AVATAR 与 OTA 后续状态

实现包括：专用 AVATAR 20 ms 音频桥及线程 join/资源互斥；设备端验签、下载、候选 journal、安装和回滚确认状态机。准确源码、测试与尚需生产信任/硬件验证的边界见 [AUDIO_OTA_IMPLEMENTATION.md](AUDIO_OTA_IMPLEMENTATION.md)。新增真实 20 ms 双向管理 WS 回归后，本文件对应 codec/streaming 测试共 15 项，纳入完整 80 项回归。
