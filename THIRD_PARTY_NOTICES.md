# Copyright and license boundaries

The root MIT license applies to original Momo contributions. Existing copyright headers and upstream license conditions remain in force for modified source and patch context. No trademark endorsement is implied.

| Source | Included scope | License evidence |
|---|---|---|
| [m5stack/StackChan](https://github.com/m5stack/StackChan/tree/f901cc2969189f3f0e96af1442c0f96e77e1e2c1) | Firmware/App/Go source overlays, declarations, lock metadata and four patches | [Firmware MIT](LICENSES/M5Stack-firmware-MIT.txt), [server MIT](LICENSES/M5Stack-server-MIT.txt), retained source headers |
| M5Stack App | Source only; no asset or dependency implementations | [App declaration](LICENSES/M5Stack-App-declaration.md); all copied pre-existing Dart files have upstream MIT SPDX headers |
| [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32/tree/e77dedb1309153bb63fed285772962c920c97dd4) | Context and changes in `firmware-private-ota-precedence.patch` and `firmware-private-voice-session.patch`; integration attribution | [Original MIT notice](LICENSES/xiaozhi-esp32-MIT.txt) |
| Opus | C ABI integration; license notice and metadata for an optional external test library | [Original notice](LICENSES/Opus-BSD.txt); no library binaries or codec implementation are distributed |

`SOURCE_ORIGINS.json` maps source overlays to their baseline or Momo origin. Original Momo changes to upstream files do not remove upstream ownership. The manifest is not a complete dependency SBOM.

The App README references LICENSE and THIRD_PARTY_NOTICES files absent from the fixed archive. Its explicit MIT declaration and the copied Dart files' SPDX headers establish the source scope used here. This does not establish rights to bundled images, fonts, models, music, 3D content, or transitive packages; those are excluded.

Flutter/pub, ESP-IDF/component and Go dependencies are referenced, not vendored. Their individual licenses apply when obtained or distributed. SDKs, model weights, FFmpeg, libopus DLLs and generated applications/firmware are not included. A future binary release requires its own dependency/license review.

The AGPL reference server snapshot and other reference implementations are excluded. Only the Momo implementation and verified MIT upstream material listed above are included; no AGPL implementation is relicensed under the root MIT file. Automated source similarity screening is supporting evidence, not a legal provenance guarantee.
