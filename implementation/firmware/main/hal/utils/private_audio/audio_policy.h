#pragma once
#include <cstddef>
#include <cstdint>

namespace private_audio {
// Pure policy shared by firmware and compile-time offline regression tests.
enum class Owner { None, Avatar, Ai, Diagnostics };
struct Ownership {
    Owner owner = Owner::None;
    constexpr bool acquire(Owner next) {
        if (next == Owner::None || owner != Owner::None) return false;
        owner = next; return true;
    }
    constexpr bool release(Owner current) {
        if (owner != current || current == Owner::None) return false;
        owner = Owner::None; return true;
    }
};
struct StreamState {
    bool capture = false, playback = false;
    uint32_t generation = 0;
    constexpr bool set(bool mic, bool speaker) {
        if (capture == mic && playback == speaker) return false;
        capture = mic; playback = speaker; ++generation; return true;
    }
    constexpr void revoke() { capture = playback = false; ++generation; }
    constexpr bool accepts(uint32_t epoch, bool input) const {
        return epoch == generation && (input ? capture : playback);
    }
};
constexpr bool opus20(const uint8_t* p, size_t n) {
    if (!p || n < 1 || n > 1275 || (p[0] & 4)) return false;
    const unsigned toc = p[0], code = toc & 3;
    if (code && n < 2) return false;
    const unsigned frames = code == 0 ? 1 : code == 3 ? (p[1] & 63) : 2;
    const unsigned mode = (toc >> 3) & 3;
    const unsigned samples = (toc & 128) ? (40U << mode) :
        ((toc & 96) == 96) ? ((toc & 8) ? 320U : 160U) : mode == 3 ? 960U : (160U << mode);
    return frames && samples * frames == 320;
}
}
