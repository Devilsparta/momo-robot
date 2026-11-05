#include "private_ota.h"
#include "update_state.h"
#include <esp_http_client.h>
#include <esp_crt_bundle.h>
#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <mbedtls/sha256.h>
#include <mbedtls/pk.h>
#include <nvs.h>
#include <cJSON.h>
#include <esp_timer.h>
#include <hal/utils/private_pairing/private_pairing.h>
#include <hal/utils/secret_logic/secret_logic.h>
#include <array>
#include <atomic>
#include <mutex>
#include <cmath>
#include <cstring>
#include <algorithm>
#include <vector>

namespace private_ota {
namespace {
std::mutex mutex;
Trust trust;
std::atomic_bool interrupted{false};
struct Journal {
    uint32_t magic=0x4b313531;
    uint32_t address=0;
    uint64_t confirmed=0,pending=0,size=0;
    uint8_t digest[32]{};
};
struct Manifest {
    std::string url,version,canonical,signature;
    uint64_t sequence=0,size=0,expires=0;
    std::array<uint8_t,32> digest{};
    UpdateState state;
};
Manifest approved;
bool configured() { return trust.allow_install && trust.verify_signature && trust.trusted_unix_time && trust.health; }
bool journal_read(Journal& out) {
    nvs_handle_t h;
    const auto opened=nvs_open("private_ota",NVS_READONLY,&h);
    if(opened==ESP_ERR_NVS_NOT_FOUND) return true;
    if(opened!=ESP_OK) return false;
    size_t size=sizeof(out); auto error=nvs_get_blob(h,"journal",&out,&size); nvs_close(h);
    return error==ESP_ERR_NVS_NOT_FOUND || (error==ESP_OK && size==sizeof(out) && out.magic==0x4b313531 && out.confirmed<=9007199254740991ULL && out.pending<=9007199254740991ULL);
}
bool journal_write(const Journal& value) {
    nvs_handle_t h; if(nvs_open("private_ota",NVS_READWRITE,&h)!=ESP_OK) return false;
    bool ok=nvs_set_blob(h,"journal",&value,sizeof(value))==ESP_OK && nvs_commit(h)==ESP_OK;
    nvs_close(h); return ok;
}
bool ascii(const std::string& s,size_t max) {
    if(s.empty() || s.size()>max) return false;
    for(unsigned char c:s) if(c<=32 || c>=127 || c=='"' || c=='\\') return false;
    return true;
}
bool origin_valid(const std::string& origin) {
    if(!ascii(origin,256) || origin.rfind("https://",0)!=0) return false;
    const auto host=origin.substr(8);
    if(host.empty() || host.find_first_of("/@?#%:")!=std::string::npos) return false;
    for(unsigned char c:host) if(!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='.'||c=='-')) return false;
    return true;
}
bool number(cJSON* root,const char* key,uint64_t& out) {
    auto* p=cJSON_GetObjectItemCaseSensitive(root,key);
    if(!cJSON_IsNumber(p) || !std::isfinite(p->valuedouble) || p->valuedouble<0 ||
        p->valuedouble>9007199254740991.0 || std::floor(p->valuedouble)!=p->valuedouble) return false;
    out=static_cast<uint64_t>(p->valuedouble);return true;
}
std::string string(cJSON* root,const char* key) {
    auto* p=cJSON_GetObjectItemCaseSensitive(root,key); return cJSON_IsString(p)?p->valuestring:"";
}
bool parse(const std::string& json,const std::string& signature,Manifest& m) {
    if(json.size()>4096 || signature.empty() || signature.size()>4096) return false;
    cJSON* root=cJSON_ParseWithLength(json.c_str(),json.size()+1);
    if(!root) return false;
    bool fields=cJSON_IsObject(root) && cJSON_GetArraySize(root)==8;
    for(const char* key:{"chip","expires","model","sequence","sha256","size","url","version"}) {
        int count=0; for(auto* p=root->child;p;p=p->next) if(p->string && std::strcmp(p->string,key)==0) ++count;
        fields=fields && count==1;
    }
    const auto hash=string(root,"sha256"); m.url=string(root,"url"); m.version=string(root,"version");
    bool ok=fields && string(root,"chip")=="esp32s3" && string(root,"model")=="K151" &&
        number(root,"sequence",m.sequence) && number(root,"size",m.size) && number(root,"expires",m.expires);
    cJSON_Delete(root);
    if(!ok || hash.size()!=64 || !ascii(m.url,1024) || !ascii(m.version,64)) return false;
    for(char c:m.version) if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='.'||c=='_'||c=='+'||c=='-')) return false;
    if(m.url.rfind(trust.artifact_origin+"/",0)!=0 || m.url.find_first_of("?#")!=std::string::npos) return false;
    for(size_t i=0;i<32;++i) {
        unsigned v=0;
        for(size_t j=0;j<2;++j) {char c=hash[2*i+j]; if(!((c>='0'&&c<='9')||(c>='a'&&c<='f'))) return false; v=v*16+(c<='9'?c-'0':c-'a'+10);}
        m.digest[i]=v;
    }
    // Same sorted, compact ASCII canonical object as backend/ota.py.
    m.canonical="{\"chip\":\"esp32s3\",\"expires\":"+std::to_string(m.expires)+",\"model\":\"K151\",\"sequence\":"+
        std::to_string(m.sequence)+",\"sha256\":\""+hash+"\",\"size\":"+std::to_string(m.size)+",\"url\":\""+m.url+"\",\"version\":\""+m.version+"\"}";
    m.signature=signature; return true;
}
struct Download {
    esp_http_client_handle_t http=nullptr;
    esp_ota_handle_t ota=0;
    bool writing=false;
    mbedtls_sha256_context sha;
    Download() {mbedtls_sha256_init(&sha);}
    ~Download() {if(writing) esp_ota_abort(ota); if(http) esp_http_client_cleanup(http); mbedtls_sha256_free(&sha);}
};
bool hash_partition(const esp_partition_t* p,const Journal& journal) {
    if(!p || !journal.size || journal.size>p->size) return false;
    Download work; if(mbedtls_sha256_starts(&work.sha,0)!=0) return false;
    std::vector<uint8_t> buffer(4096); // HAL maintenance runs on a small task stack
    for(size_t offset=0;offset<journal.size;) {
        size_t n=std::min<size_t>(buffer.size(),journal.size-offset);
        if(esp_partition_read(p,offset,buffer.data(),n)!=ESP_OK || mbedtls_sha256_update(&work.sha,buffer.data(),n)!=0) return false;
        offset+=n;
    }
    uint8_t hash[32]; if(mbedtls_sha256_finish(&work.sha,hash)!=0) return false;
    return std::memcmp(hash,journal.digest,32)==0;
}
}
bool configure(Trust value) {
    std::lock_guard<std::mutex> lock(mutex);
    if(!value.allow_install || !value.verify_signature || !value.trusted_unix_time || !value.health || !origin_valid(value.artifact_origin)) return false;
    trust=std::move(value); approved=Manifest{}; return true;
}
bool verify_p256(const std::string& public_pem,const std::string& message,const std::string& signature_hex) {
    if(public_pem.empty() || public_pem.size()>2048 || signature_hex.empty() || signature_hex.size()>160 || signature_hex.size()%2) return false;
    std::array<uint8_t,80> signature{};
    for(size_t i=0;i<signature_hex.size();++i) {
        const char c=signature_hex[i];
        if(!((c>='0'&&c<='9')||(c>='a'&&c<='f'))) return false;
        signature[i/2]=signature[i/2]*16+(c<='9'?c-'0':c-'a'+10);
    }
    mbedtls_pk_context key; mbedtls_pk_init(&key);
    uint8_t digest[32];
    bool ok=mbedtls_pk_parse_public_key(&key,(const uint8_t*)public_pem.c_str(),public_pem.size()+1)==0 &&
        mbedtls_pk_can_do(&key,MBEDTLS_PK_ECDSA) && mbedtls_pk_get_bitlen(&key)==256 &&
        mbedtls_pk_ec(key)->MBEDTLS_PRIVATE(grp).id==MBEDTLS_ECP_DP_SECP256R1 &&
        mbedtls_sha256((const uint8_t*)message.data(),message.size(),digest,0)==0 &&
        mbedtls_pk_verify(&key,MBEDTLS_MD_SHA256,digest,sizeof(digest),signature.data(),signature_hex.size()/2)==0;
    mbedtls_pk_free(&key); return ok;
}
bool offer(const std::string& json,const std::string& signature,std::string& url,std::string& version) {
    std::lock_guard<std::mutex> lock(mutex); approved=Manifest{};
    if(!configured()) return false;
    Manifest candidate; Journal journal; const auto* target=esp_ota_get_next_update_partition(nullptr);
    const uint64_t now=trust.trusted_unix_time();
    if(!now || !target || !journal_read(journal) || journal.pending || !parse(json,signature,candidate) ||
        !candidate.state.verify(trust.verify_signature(candidate.canonical,signature),true,
            candidate.sequence>std::max(journal.confirmed,trust.minimum_sequence) && candidate.expires>now,candidate.size,target->size)) return false;
    url=candidate.url; version=candidate.version; approved=std::move(candidate); return true;
}
bool install(const std::string& url,std::function<void(int,size_t)> progress) {
    std::lock_guard<std::mutex> lock(mutex);
    Manifest m=std::move(approved); approved=Manifest{}; // consume the offer on every attempt
    const auto* target=esp_ota_get_next_update_partition(nullptr); Journal journal;
    if(!configured() || !target || url!=m.url || m.state.phase!=Phase::Verified || !journal_read(journal) || journal.pending ||
        m.sequence<=std::max(journal.confirmed,trust.minimum_sequence) || !trust.trusted_unix_time() || m.expires<=trust.trusted_unix_time() ||
        !trust.verify_signature(m.canonical,m.signature)) return false;
    interrupted=false; Download work;
    esp_http_client_config_t cfg{};
    cfg.url=m.url.c_str(); cfg.crt_bundle_attach=esp_crt_bundle_attach;
    cfg.disable_auto_redirect=true; cfg.timeout_ms=5000; cfg.buffer_size=4096;
    work.http=esp_http_client_init(&cfg);
    if(work.http && trust.artifact_origin==secret_logic::get_server_url()) {
        const auto authorization=private_pairing::authorization();
        if(authorization.empty()) return false;
        esp_http_client_set_header(work.http,"Authorization",authorization.c_str());
    }
    if(!work.http || esp_http_client_open(work.http,0)!=ESP_OK) return false;
    const auto length=esp_http_client_fetch_headers(work.http);
    if(esp_http_client_get_status_code(work.http)!=200 || length!=static_cast<int64_t>(m.size) || interrupted) return false;
    if(esp_ota_begin(target,m.size,&work.ota)!=ESP_OK) return false;
    work.writing=true;
    if(!m.state.begin() || mbedtls_sha256_starts(&work.sha,0)!=0) return false;
    std::vector<uint8_t> buffer(4096);
    const int64_t started=esp_timer_get_time();
    while(m.state.received<m.size) {
        if(interrupted) {m.state.cancel();return false;}
        const int n=esp_http_client_read(work.http,(char*)buffer.data(),std::min<uint64_t>(buffer.size(),m.size-m.state.received));
        if(n<=0 || !m.state.append(n) || mbedtls_sha256_update(&work.sha,buffer.data(),n)!=0 || esp_ota_write(work.ota,buffer.data(),n)!=ESP_OK) return false;
        if(progress) progress(static_cast<int>(m.state.received*100/m.size),m.state.received*1000000/std::max<int64_t>(1,esp_timer_get_time()-started));
    }
    uint8_t hash[32];
    if(interrupted || !trust.trusted_unix_time() || m.expires<=trust.trusted_unix_time() ||
        mbedtls_sha256_finish(&work.sha,hash)!=0 || std::memcmp(hash,m.digest.data(),32)!=0) return false;
    const auto ended=esp_ota_end(work.ota); work.writing=false;
    if(!m.state.finish(true,ended==ESP_OK) || interrupted) return false;
    journal.pending=m.sequence; journal.address=target->address; journal.size=m.size;
    std::memcpy(journal.digest,m.digest.data(),32);
    if(!journal_write(journal)) return false;
    if(interrupted || esp_ota_set_boot_partition(target)!=ESP_OK) {
        journal.pending=0; journal.address=0; journal.size=0; journal_write(journal);return false;
    }
    return m.state.stage(true,true); // caller may reboot; no reboot here
}
void cancel() {interrupted=true;}
void reconcile_boot() {
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
    if(!lock.owns_lock() || !configured()) return;
    static int64_t last_check=0;
    static bool rejected=false;
    const int64_t now=esp_timer_get_time();
    if(rejected || now-last_check<1000000) return;
    last_check=now; // no NVS/hash work at the HAL display tick rate
    Journal journal; if(!journal_read(journal) || !journal.pending) return;
    const auto* running=esp_ota_get_running_partition(); const auto* boot=esp_ota_get_boot_partition();
    if(!running || !boot) return;
    UpdateState state; state.phase=Phase::Staged;
    const auto action=boot_action(journal.pending,journal.address,running->address,boot->address);
    if(action!=BootAction::VerifyCandidate) {
        if(action==BootAction::ClearRolledBack) { // never lower the confirmed floor
            state.rollback(); journal.pending=0; journal.address=0; journal.size=0; journal_write(journal);
        }
        return;
    }
    esp_ota_img_states_t image;
    if(esp_ota_get_state_partition(running,&image)!=ESP_OK || (image!=ESP_OTA_IMG_PENDING_VERIFY && image!=ESP_OTA_IMG_VALID)) return;
    const auto health=trust.health();
    if(health==Health::Pending) return;
    if(health!=Health::Healthy && health!=Health::Failed) return;
    if(health==Health::Failed || !hash_partition(running,journal)) {
        // Only a configured, explicit failed health verdict (or failed image
        // verification) rejects a pending candidate. No default timer guesses.
        rejected=true; // a failed rollback API must not spin/rewrite on every tick
        if(image==ESP_OTA_IMG_PENDING_VERIFY) esp_ota_mark_app_invalid_rollback_and_reboot();
        return;
    }
    if(image==ESP_OTA_IMG_PENDING_VERIFY && esp_ota_mark_app_valid_cancel_rollback()!=ESP_OK) return;
    journal.confirmed=std::max(journal.confirmed,journal.pending);
    journal.pending=0; journal.address=0; journal.size=0;
    state.confirm(true,true,true,journal_write(journal));
}
}
