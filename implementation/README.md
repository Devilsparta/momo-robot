# Momo implementation

This directory contains development source overlays, patches, the local Python service and tests. See the [project README](../README.md) and [license boundaries](../THIRD_PARTY_NOTICES.md).

- [Capability matrix](CAPABILITY_MATRIX.md)
- [Service contract](SERVICE_CONTRACT.md)
- [Voice pipeline](VOICE_PIPELINE.md)
- [Audio and OTA implementation](AUDIO_OTA_IMPLEMENTATION.md)
- [Build and hardware prerequisites](BUILD_AND_RECOVERY.md)
- [Toolchains](TOOLCHAINS.md)
- [Verification status](STATUS.md)

Run `python -B tools/run_checks.py` from this directory. For a local empty development service, use `python -B -m backend.service --port 12800`; it listens only on loopback and has no configured identities, user sessions, model provider or OTA trust. Authentication requires a trusted integration; a MAC address alone is not a credential. Do not expose this development service directly to the internet.
