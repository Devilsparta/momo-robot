#pragma once
#include <memory>
#include <vector>
#include <cstdint>
#include <mutex>
namespace private_audio {
// A joined, exclusive HAL owner. No network callbacks run on the audio workers.
class AvatarAudio {
    struct Impl;
    std::unique_ptr<Impl> impl_;
    std::mutex access_;
public:
    AvatarAudio();
    ~AvatarAudio();
    bool set(bool capture, bool playback);
    void revoke();
    bool receive(const uint8_t* data, size_t size);
    std::vector<uint8_t> take();
};
}
