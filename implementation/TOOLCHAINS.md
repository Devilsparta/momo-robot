# Development toolchain baseline

| Tool | Recorded version | Official source |
|---|---|---|
| ESP-IDF | 5.5.4 | https://github.com/espressif/esp-idf/tree/v5.5.4 |
| ESP32-S3 compiler | Xtensa GCC 14.2.0_20260121, selected by the IDF tool manifest | ESP-IDF `tools/tools.json` |
| Flutter / Dart | 3.47.7 / 3.13.5 | https://docs.flutter.dev/install/archive |
| Go | 1.26.3 | https://go.dev/dl/ |
| Python | 3.11+ | https://www.python.org/downloads/ |
| Opus test library | libopus 1.5.2, external `opus_codec_windows` 3.0.5 | https://pub.dev/packages/opus_codec_windows/versions/3.0.5 |

Version and archive hashes are retained in `provenance/`. They describe the development baseline, not a recommendation to install arbitrary newer versions. No SDK, dependency cache, install state, personal path or binary is bundled.

Full Windows native App tests need MSVC/Windows SDK and OpenCV plugin prerequisites. iOS build, signing and native sensor/lifecycle verification need macOS/Xcode. These are not validated by isolated Dart tests.

Set `STACKCHAN_TEST_OPUS_LIBRARY` to a trusted existing libopus file for codec tests. Set `MOMO_CXX` to an existing compatible C++17 compiler for the compile-time policy checks. These runners do not install or fetch dependencies and perform no hardware I/O.
