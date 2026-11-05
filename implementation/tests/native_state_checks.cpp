// Compiled constexpr scenarios against the EXACT portable firmware policy.
// This is not FreeRTOS, flash or acoustic emulation.
#include "hal/utils/private_audio/audio_policy.h"
#include "hal/utils/private_ota/update_state.h"
using namespace private_audio;
using namespace private_ota;
constexpr bool ownership() {
    Ownership p;
    return p.acquire(Owner::Avatar) && !p.acquire(Owner::Ai) && !p.release(Owner::Ai) &&
        p.release(Owner::Avatar) && p.acquire(Owner::Ai) && !p.acquire(Owner::Diagnostics) && p.release(Owner::Ai);
}
constexpr bool reconnect() {
    StreamState s;
    if(!s.set(true,true)) return false;
    const auto old=s.generation;
    if(!s.accepts(old,true) || !s.accepts(old,false)) return false;
    s.revoke();
    if(s.accepts(old,true) || s.accepts(old,false)) return false;
    s.set(true,false);
    return !s.accepts(old,true) && s.accepts(s.generation,true) && !s.accepts(s.generation,false);
}
constexpr bool update_success() {
    UpdateState s;
    return s.verify(true,true,true,10,20) && s.begin() && s.append(3) && s.append(7) &&
        s.finish(true,true) && s.stage(true,true) && !s.confirm(true,false,true,true) &&
        !s.confirm(false,true,true,true) && !s.confirm(true,true,true,false) &&
        s.confirm(true,true,true,true) && s.phase==Phase::Confirmed;
}
constexpr bool rejects() {
    for(int bad=0;bad<5;++bad) {
        UpdateState s;
        if(s.verify(bad!=0,bad!=1,bad!=2,bad==3?0:10,bad==4?9:20)) return false;
        if(s.begin() || s.phase!=Phase::Failed) return false;
    }
    for(int bad=0;bad<4;++bad) {
        UpdateState s;
        s.verify(true,true,true,10,20);s.begin();s.append(bad==0?9:10);
        if(bad==3) {if(s.append(1)) return false;continue;}
        if(s.finish(bad!=1,bad!=2)) return false;
        if(s.stage(true,true)) return false;
    }
    return true;
}
constexpr bool interrupted_update() {
    for(int point=0;point<3;++point) {
        UpdateState s;s.verify(true,true,true,10,20);
        if(point>0) s.begin();
        if(point>1) s.append(3);
        s.cancel();
        if(s.append(7) || s.finish(true,true) || s.stage(true,true)) return false;
    }
    for(int failure=0;failure<2;++failure) {
        UpdateState s;s.verify(true,true,true,10,20);s.begin();s.append(10);s.finish(true,true);
        if(s.stage(failure!=0,failure!=1)) return false;
    }
    UpdateState staged;staged.verify(true,true,true,10,20);staged.begin();staged.append(10);
    staged.finish(true,true);staged.stage(true,true);staged.cancel();
    if(staged.phase!=Phase::Staged) return false;
    staged.rollback();return staged.phase==Phase::RolledBack && !staged.confirm(true,true,true,true);
}
static_assert(ownership(),"exclusive audio ownership/release");
static_assert(reconnect(),"late media invalidated after cancellation/reconnect");
static_assert(update_success(),"verified image/journal/health confirmation");
static_assert(rejects(),"signature/target/replay/size/hash/image failures");
static_assert(interrupted_update(),"cancel, write overrun, journal/boot failure, rollback");
static_assert(boot_action(0,2,1,1)==BootAction::None &&
    boot_action(2,2,1,2)==BootAction::AwaitReboot &&
    boot_action(2,2,2,2)==BootAction::VerifyCandidate &&
    boot_action(2,2,1,1)==BootAction::ClearRolledBack,"power interruption and bootloader rollback recovery");
constexpr uint8_t mono20[]={0x78,0};
constexpr uint8_t stereo20[]={0x7c,0};
constexpr uint8_t mono60[]={0x18,0};
constexpr uint8_t malformed[]={0x7b};
static_assert(opus20(mono20,2) && !opus20(stereo20,2) && !opus20(mono60,2) && !opus20(malformed,1));
