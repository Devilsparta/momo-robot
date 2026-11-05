#include "avatar_audio.h"
#include "audio_owner.h"
#include <audio_codec.h>
#include <board.h>
#include <esp_opus_enc.h>
#include <esp_opus_dec.h>
#include <esp_ae_rate_cvt.h>
#include <esp_pthread.h>
#include <esp_log.h>
#include <condition_variable>
#include <deque>
#include <thread>
#include <chrono>

namespace private_audio {
namespace {
constexpr size_t QueueLimit = 6; // at most 120 ms per direction
struct Conversion {
    esp_ae_rate_cvt_handle_t handle = nullptr;
    bool open(int from, int to) {
        esp_ae_rate_cvt_cfg_t cfg{};
        cfg.src_rate=from; cfg.dest_rate=to; cfg.channel=1;
        cfg.bits_per_sample=ESP_AUDIO_BIT16; cfg.complexity=2;
        cfg.perf_type=ESP_AE_RATE_CVT_PERF_TYPE_SPEED;
        return esp_ae_rate_cvt_open(&cfg,&handle)==ESP_AE_ERR_OK && handle;
    }
    bool process(std::vector<int16_t>& pcm) {
        uint32_t count=0;
        if (esp_ae_rate_cvt_get_max_out_sample_num(handle,pcm.size(),&count)!=ESP_AE_ERR_OK || count>4096) return false;
        std::vector<int16_t> out(count);
        if (esp_ae_rate_cvt_process(handle,(esp_ae_sample_t)pcm.data(),pcm.size(),(esp_ae_sample_t)out.data(),&count)!=ESP_AE_ERR_OK) return false;
        out.resize(count); pcm=std::move(out); return true;
    }
    ~Conversion() { if(handle) esp_ae_rate_cvt_close(handle); }
};
}
struct AvatarAudio::Impl {
    Lease lease{Owner::Avatar};
    AudioCodec* codec = nullptr;
    void* encoder = nullptr; void* decoder = nullptr;
    Conversion input_rate, output_rate;
    int encoded_size = 0;
    bool ready=false, closing=false;
    std::mutex mutex;
    std::condition_variable changed;
    StreamState state;
    std::deque<std::vector<uint8_t>> incoming,outgoing;
    pthread_t capture_worker{},playback_worker{};
    bool capture_created=false,playback_created=false;

    Impl() {
        if (!lease) return;
        codec=Board::GetInstance().GetAudioCodec();
        if (!codec || codec->input_sample_rate()!=24000 || codec->output_sample_rate()!=24000 ||
            codec->input_channels()<1 || codec->input_channels()>2 || codec->output_channels()!=1) return;
        esp_opus_enc_config_t enc{};
        enc.sample_rate=16000; enc.channel=1; enc.bits_per_sample=16;
        enc.bitrate=24000; enc.frame_duration=ESP_OPUS_ENC_FRAME_DURATION_20_MS;
        enc.application_mode=ESP_OPUS_ENC_APPLICATION_VOIP; enc.complexity=0;
        enc.enable_fec=false; enc.enable_dtx=false; enc.enable_vbr=true;
        esp_opus_dec_cfg_t dec{16000,1,ESP_OPUS_DEC_FRAME_DURATION_20_MS,false};
        int input_size=0;
        if (esp_opus_enc_open(&enc,sizeof(enc),&encoder)!=ESP_AUDIO_ERR_OK ||
            esp_opus_enc_get_frame_size(encoder,&input_size,&encoded_size)!=ESP_AUDIO_ERR_OK ||
            input_size!=640 || encoded_size<=0 || encoded_size>1275 ||
            esp_opus_dec_open(&dec,sizeof(dec),&decoder)!=ESP_AUDIO_ERR_OK ||
            !input_rate.open(24000,16000) || !output_rate.open(16000,24000)) return;
        codec->Start();
        auto previous=esp_pthread_get_default_config();
        esp_pthread_get_cfg(&previous);
        auto cfg=previous; cfg.stack_size=8192; cfg.prio=4;
        if (esp_pthread_set_cfg(&cfg)!=ESP_OK) return;
        capture_created=pthread_create(&capture_worker,nullptr,[](void* p)->void* {static_cast<Impl*>(p)->capture();return nullptr;},this)==0;
        if(capture_created)
            playback_created=pthread_create(&playback_worker,nullptr,[](void* p)->void* {static_cast<Impl*>(p)->playback();return nullptr;},this)==0;
        esp_pthread_set_cfg(&previous);
        ready=capture_created && playback_created;
    }
    ~Impl() {
        { std::lock_guard<std::mutex> lock(mutex); closing=true; state.revoke(); incoming.clear(); outgoing.clear(); }
        changed.notify_all();
        // Never free codec/queues while workers can still access them.
        if(capture_created) pthread_join(capture_worker,nullptr);
        if(playback_created) pthread_join(playback_worker,nullptr);
        if(encoder) esp_opus_enc_close(encoder);
        if(decoder) esp_opus_dec_close(decoder);
    }
    void capture() {
        uint32_t epoch=UINT32_MAX;
        std::vector<int16_t> buffered;
        for (;;) {
            uint32_t ticket;
            {
                std::unique_lock<std::mutex> lock(mutex);
                if (!state.capture || closing) {
                    codec->EnableInput(false);
                    changed.wait(lock,[this]{return closing || state.capture;});
                }
                if(closing) break;
                ticket=state.generation;
                if(ticket!=epoch) {
                    esp_opus_enc_reset(encoder); esp_ae_rate_cvt_reset(input_rate.handle);
                    buffered.clear(); epoch=ticket;
                }
                codec->EnableInput(true);
            }
            std::vector<int16_t> data(480*codec->input_channels());
            if(!codec->InputData(data)) { std::this_thread::sleep_for(std::chrono::milliseconds(20)); continue; }
            std::vector<int16_t> mono(480);
            for(size_t i=0;i<480;++i) mono[i]=data[i*codec->input_channels()]; // reference is not microphone
            if(!input_rate.process(mono)) continue;
            buffered.insert(buffered.end(),mono.begin(),mono.end());
            if(buffered.size()>960) { buffered.clear(); continue; }
            while(buffered.size()>=320) {
                std::vector<uint8_t> packet(encoded_size);
                esp_audio_enc_in_frame_t in{}; in.buffer=(uint8_t*)buffered.data(); in.len=640;
                esp_audio_enc_out_frame_t out{}; out.buffer=packet.data(); out.len=packet.size();
                const auto result=esp_opus_enc_process(encoder,&in,&out);
                buffered.erase(buffered.begin(),buffered.begin()+320);
                if(result!=ESP_AUDIO_ERR_OK || out.encoded_bytes<=0 || out.encoded_bytes>packet.size()) continue;
                packet.resize(out.encoded_bytes);
                std::lock_guard<std::mutex> lock(mutex);
                if(state.accepts(ticket,true) && !closing) {
                    if(outgoing.size()>=QueueLimit) outgoing.pop_front();
                    outgoing.push_back(std::move(packet));
                }
            }
        }
        codec->EnableInput(false);
    }
    void playback() {
        uint32_t epoch=UINT32_MAX;
        for (;;) {
            std::unique_lock<std::mutex> lock(mutex);
            if(!state.playback || incoming.empty() || closing) {
                if(!state.playback || closing) codec->EnableOutput(false);
                changed.wait_for(lock,std::chrono::milliseconds(20));
                if (!closing && (!state.playback || incoming.empty())) continue;
            }
            if(closing) break;
            const auto ticket=state.generation;
            auto packet=std::move(incoming.front()); incoming.pop_front();
            if(ticket!=epoch) {
                esp_opus_dec_reset(decoder); esp_ae_rate_cvt_reset(output_rate.handle); epoch=ticket;
            }
            lock.unlock();
            std::vector<int16_t> pcm(320);
            esp_audio_dec_in_raw_t in{}; in.buffer=packet.data(); in.len=packet.size(); in.frame_recover=ESP_AUDIO_DEC_RECOVERY_NONE;
            esp_audio_dec_out_frame_t out{}; out.buffer=(uint8_t*)pcm.data(); out.len=640;
            esp_audio_dec_info_t info{};
            if(esp_opus_dec_decode(decoder,&in,&out,&info)!=ESP_AUDIO_ERR_OK || out.decoded_size!=640 ||
                in.consumed!=packet.size() || !output_rate.process(pcm)) continue;
            lock.lock();
            if(state.accepts(ticket,false) && !closing) {
                codec->EnableOutput(true);
                codec->OutputData(pcm); // stop waits for at most the in-flight hardware write
            }
        }
        codec->EnableOutput(false);
    }
};
AvatarAudio::AvatarAudio() = default;
AvatarAudio::~AvatarAudio() = default;
bool AvatarAudio::set(bool mic,bool speaker) {
    std::lock_guard<std::mutex> access(access_);
    if(!impl_ && (mic || speaker)) impl_=std::make_unique<Impl>();
    if(!impl_) return true;
    if(!impl_->ready) { impl_.reset(); return false; }
    { std::lock_guard<std::mutex> lock(impl_->mutex);
      if(impl_->state.set(mic,speaker)) { impl_->incoming.clear(); impl_->outgoing.clear(); } }
    impl_->changed.notify_all(); return true;
}
void AvatarAudio::revoke() {
    std::lock_guard<std::mutex> access(access_);
    if(!impl_) return;
    { std::lock_guard<std::mutex> lock(impl_->mutex); impl_->state.revoke(); impl_->incoming.clear(); impl_->outgoing.clear(); }
    impl_->changed.notify_all();
}
bool AvatarAudio::receive(const uint8_t* p,size_t n) {
    std::lock_guard<std::mutex> access(access_);
    if(!impl_ || !opus20(p,n)) return false;
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(!impl_->state.playback || impl_->incoming.size()>=QueueLimit) return false;
    impl_->incoming.emplace_back(p,p+n); impl_->changed.notify_all(); return true;
}
std::vector<uint8_t> AvatarAudio::take() {
    std::lock_guard<std::mutex> access(access_);
    if(!impl_) return {};
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(!impl_->state.capture || impl_->outgoing.empty()) return {};
    auto p=std::move(impl_->outgoing.front()); impl_->outgoing.pop_front(); return p;
}
}
