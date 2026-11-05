# Build scope and hardware prerequisites

The overlays are not a complete build tree. Obtain the exact StackChan and dependency revisions in `../baselines.json` separately, retaining their licenses. No SDK or upstream fetch/install script is run as part of this public preparation.

Apply `app-private-endpoints.patch`, `server-frame-bounds.patch`, `firmware-private-mode.patch`, and `firmware-private-pairing.patch` at the fixed StackChan root. These four patches correspond to the included source overlays; copying overlays as well is redundant. Do not apply against a newer upstream without reviewing conflicts.

For xiaozhi-esp32, first apply StackChan's original `firmware/patches/xiaozhi-esp32.patch` to its fixed dependency revision. Then apply `firmware-private-ota-precedence.patch` and `firmware-private-voice-session.patch` at that dependency root. The original dependency patch is obtained with StackChan, not copied from an unreviewed reference server.

Use the versions in TOOLCHAINS.md. Firmware requires a full ESP-IDF component tree. On Windows, response files (`CMAKE_NINJA_FORCE_RESPONSE_FILE=ON`) and a short build path address command-line length; build with at most two workers. This package deliberately excludes scripts coupled to the previous local SDK/build tree and archive/log layout. It does not provide a one-command full product build.

App dependencies and native plugin assets must be obtained separately. The independent pairing harness exercises selected actual Dart modules and cannot replace a full iOS build. Go changes require the complete upstream module. Recheck patch application and run the relevant tests after any source/configuration change.

No flashable images are included. Before hardware testing, identify the hardware revision and current firmware, confirm an appropriate recovery image and serial access, determine whether permitted backups are usable, and configure authentic device enrollment, user sessions, TLS and release trust. Do not treat a checksum as a signature or assume every failed update is recoverable.

OTA requires an explicit trust verifier, trusted clock, health callback and deployment policy; it refuses installation without them. The application-level NVS journal is not eFuse or secure-boot anti-tamper protection. iOS installation needs a Mac/Xcode environment and an appropriate signing setup. None of these prerequisites is supplied by this public candidate.
