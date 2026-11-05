#pragma once
#include <cstdint>
namespace private_ota {
enum class Phase { Idle, Verified, Receiving, Ready, Staged, Confirmed, RolledBack, Failed, Cancelled };
enum class BootAction { None, AwaitReboot, VerifyCandidate, ClearRolledBack };
constexpr BootAction boot_action(uint64_t pending,uint32_t candidate,uint32_t running,uint32_t boot) {
    if(!pending) return BootAction::None;
    if(running==candidate) return BootAction::VerifyCandidate;
    if(boot==candidate) return BootAction::AwaitReboot;
    return BootAction::ClearRolledBack;
}
struct UpdateState {
    Phase phase=Phase::Idle;
    uint64_t size=0,received=0;
    constexpr bool verify(bool signature,bool target,bool fresh,uint64_t bytes,uint64_t capacity) {
        if(phase!=Phase::Idle || !signature || !target || !fresh || !bytes || bytes>capacity) {phase=Phase::Failed; return false;}
        size=bytes; phase=Phase::Verified; return true;
    }
    constexpr bool begin() { if(phase!=Phase::Verified) return false; phase=Phase::Receiving; return true; }
    constexpr bool append(uint64_t bytes) {
        if(phase!=Phase::Receiving || !bytes || bytes>size-received) {phase=Phase::Failed; return false;}
        received+=bytes; return true;
    }
    constexpr bool finish(bool digest,bool image) {
        if(phase!=Phase::Receiving || received!=size || !digest || !image) {phase=Phase::Failed;return false;}
        phase=Phase::Ready;return true;
    }
    constexpr bool stage(bool journal,bool boot) {
        if(phase!=Phase::Ready || !journal || !boot) {phase=Phase::Failed;return false;}
        phase=Phase::Staged;return true;
    }
    constexpr bool confirm(bool running_candidate,bool healthy,bool marked_valid,bool persisted) {
        if(phase!=Phase::Staged || !running_candidate || !healthy || !marked_valid || !persisted) return false;
        phase=Phase::Confirmed;return true;
    }
    constexpr void cancel() { if(phase!=Phase::Staged && phase!=Phase::Confirmed) phase=Phase::Cancelled; }
    constexpr void rollback() { phase=Phase::RolledBack; }
};
}
