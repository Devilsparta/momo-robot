#pragma once
#include "audio_policy.h"
#include <mutex>
namespace private_audio {
inline std::mutex owner_mutex;
inline Ownership ownership;
inline bool acquire(Owner owner) {
    std::lock_guard<std::mutex> lock(owner_mutex); return ownership.acquire(owner);
}
inline void release(Owner owner) {
    std::lock_guard<std::mutex> lock(owner_mutex); ownership.release(owner);
}
inline Owner current_owner() {
    std::lock_guard<std::mutex> lock(owner_mutex); return ownership.owner;
}
class Lease {
    Owner owner_; bool acquired_;
public:
    explicit Lease(Owner owner): owner_(owner), acquired_(acquire(owner)) {}
    ~Lease() { if (acquired_) release(owner_); }
    explicit operator bool() const { return acquired_; }
    Lease(const Lease&) = delete;
    Lease& operator=(const Lease&) = delete;
};
}
