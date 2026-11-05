#include "private_pairing.h"
#include "../secret_logic/secret_logic.h"
#include <esp_http_client.h>
#include <esp_crt_bundle.h>
#include <esp_timer.h>
#include <cJSON.h>
#include <mutex>
#include <memory>
#include <cctype>
#include <algorithm>
#include <esp_mac.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace private_pairing {
namespace {
std::mutex mutex;
std::string bootstrap_token, bound_token, identity_key_id;
Signer identity_signer;
uint64_t identity_generation=0;
bool identity_worker_started=false;
std::mutex refresh_mutex;
int64_t bootstrap_until = 0, bound_until = 0;
int64_t now() { return esp_timer_get_time() / 1000000; }
bool valid_token(const std::string& token) {
    return !token.empty() && token.size() <= 512 &&
        std::all_of(token.begin(), token.end(), [](unsigned char c) { return c > 32 && c < 127; });
}
std::string bootstrap() {
    std::lock_guard<std::mutex> lock(mutex);
    return now() < bootstrap_until ? "Bearer " + bootstrap_token : "";
}
using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
Json request(esp_http_client_method_t method, const std::string& path,
             const std::string& body = "", bool proof_request = false) {
    const auto auth = proof_request ? "" : bootstrap();
    const auto origin = secret_logic::get_server_url();
    if ((!proof_request && auth.empty()) || origin.rfind("https://", 0) != 0) return Json(nullptr, cJSON_Delete);
    const auto url = origin + path;
    esp_http_client_config_t config{};
    config.url = url.c_str();
    config.method = method;
    config.timeout_ms = 5000;
    config.disable_auto_redirect = true;
#ifdef CONFIG_MBEDTLS_CERTIFICATE_BUNDLE
    config.crt_bundle_attach = esp_crt_bundle_attach;
#endif
    auto* client = esp_http_client_init(&config);
    if (!client) return Json(nullptr, cJSON_Delete);
    struct Cleanup { esp_http_client_handle_t c; ~Cleanup() { esp_http_client_cleanup(c); } } cleanup{client};
    if (!auth.empty()) esp_http_client_set_header(client, "Authorization", auth.c_str());
    esp_http_client_set_header(client, "Content-Type", "application/json");
    if (esp_http_client_open(client, static_cast<int>(body.size())) != ESP_OK) return Json(nullptr,cJSON_Delete);
    size_t sent = 0;
    while (sent < body.size()) {
        int count = esp_http_client_write(client, body.data()+sent, static_cast<int>(body.size()-sent));
        if (count <= 0) return Json(nullptr,cJSON_Delete);
        sent += count;
    }
    if (esp_http_client_fetch_headers(client) < 0 || esp_http_client_get_status_code(client) != 200)
        return Json(nullptr,cJSON_Delete);
    std::string response;
    char buffer[512];
    while (true) {
        int count = esp_http_client_read(client, buffer, sizeof(buffer));
        if (count < 0 || response.size()+static_cast<size_t>(std::max(count,0)) > 8192)
            return Json(nullptr,cJSON_Delete);
        if (count == 0) break;
        response.append(buffer, count);
    }
    Json root(cJSON_Parse(response.c_str()),cJSON_Delete);
    auto* code = root ? cJSON_GetObjectItemCaseSensitive(root.get(),"code") : nullptr;
    if (!cJSON_IsNumber(code) || code->valueint != 0) return Json(nullptr,cJSON_Delete);
    return root;
}
std::string string_field(cJSON* data,const char* name) {
    auto* value=cJSON_GetObjectItemCaseSensitive(data,name);
    return cJSON_IsString(value) && value->valuestring ? value->valuestring : "";
}
uint32_t lifetime(cJSON* data) {
    auto* value=cJSON_GetObjectItemCaseSensitive(data,"expires_in");
    return cJSON_IsNumber(value) && value->valuedouble > 0 && value->valuedouble <= 3600
        ? static_cast<uint32_t>(value->valuedouble) : 0;
}
bool valid_id(const std::string& id) {
    return !id.empty() && id.size()<=64 && std::all_of(id.begin(),id.end(),
        [](unsigned char c) { return std::isalnum(c) || c=='-' || c=='_'; });
}
}
bool install_bootstrap(const std::string& token,uint32_t ttl) {
    if (!valid_token(token) || ttl==0 || ttl>3600) return false;
    std::lock_guard<std::mutex> lock(mutex);
    bootstrap_token=token; bootstrap_until=now()+ttl;
    bound_token.clear(); bound_until=0;
    return true;
}
std::string authorization() {
    std::lock_guard<std::mutex> lock(mutex);
    if (bound_token.empty() || now()>=bound_until) return "";
    return "Bearer "+bound_token;
}
void clear() {
    std::lock_guard<std::mutex> lock(mutex);
    bootstrap_token.clear(); bound_token.clear(); bootstrap_until=bound_until=0;
    identity_signer=nullptr; identity_key_id.clear(); ++identity_generation;
}
bool install_identity_signer(const std::string& key_id, Signer signer) {
    if (!valid_id(key_id) || !signer) return false;
    std::lock_guard<std::mutex> lock(mutex);
    if (!identity_worker_started) {
        if (xTaskCreate([](void*) {
            while (true) {
                bool needed=false;
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    needed=identity_signer && ((bound_until>0 && bound_until-now()<45) ||
                        (bound_until==0 && bootstrap_until<=now()));
                }
                if (needed) refresh_identity();
                vTaskDelay(pdMS_TO_TICKS(30000));
            }
        }, "private_session", 8192, nullptr, 2, nullptr)!=pdPASS) return false;
        identity_worker_started=true;
    }
    identity_key_id=key_id; identity_signer=std::move(signer); ++identity_generation;
    bootstrap_token.clear(); bound_token.clear(); bootstrap_until=bound_until=0;
    return true;
}
bool refresh_identity() {
    std::lock_guard<std::mutex> serial(refresh_mutex);
    Signer signer; std::string key_id; uint64_t generation;
    {
        std::lock_guard<std::mutex> lock(mutex);
        signer=identity_signer; key_id=identity_key_id; generation=identity_generation;
    }
    if (!signer) return false;
    uint8_t address_bytes[6];
    if (esp_read_mac(address_bytes,ESP_MAC_WIFI_STA)!=ESP_OK) return false;
    char address[13];
    snprintf(address,sizeof(address),"%02x%02x%02x%02x%02x%02x",address_bytes[0],address_bytes[1],
        address_bytes[2],address_bytes[3],address_bytes[4],address_bytes[5]);
    const auto started=now();
    auto challenge=request(HTTP_METHOD_POST,"/private/v1/identity/challenge",
        std::string("{\"mac\":\"")+address+"\"}",true);
    auto* data=challenge ? cJSON_GetObjectItemCaseSensitive(challenge.get(),"data") : nullptr;
    const auto id=string_field(data,"challenge_id"), nonce=string_field(data,"nonce");
    auto hex=[](const std::string& value,size_t length) {
        return value.size()==length && std::all_of(value.begin(),value.end(),
            [](unsigned char c){return (c>='0'&&c<='9')||(c>='a'&&c<='f');});
    };
    if (string_field(data,"mac")!=address || string_field(data,"key_id")!=key_id ||
        !hex(id,32) || !hex(nonce,64) || now()-started>=60) return false;
    const std::string message=std::string("stackchan-device-session-v1\n")+address+"\n"+key_id+"\n"+id+"\n"+nonce;
    const auto proof=signer(message);
    if (proof.empty() || proof.size()>4096) return false;
    Json body(cJSON_CreateObject(),cJSON_Delete);
    cJSON_AddStringToObject(body.get(),"challenge_id",id.c_str());
    cJSON_AddStringToObject(body.get(),"proof",proof.c_str());
    char* raw=cJSON_PrintUnformatted(body.get());
    if (!raw) return false;
    std::string serialized(raw); cJSON_free(raw);
    auto response=request(HTTP_METHOD_POST,"/private/v1/identity/prove",serialized,true);
    data=response ? cJSON_GetObjectItemCaseSensitive(response.get(),"data") : nullptr;
    const auto token=string_field(data,"device_token"), state=string_field(data,"state");
    const auto ttl=lifetime(data);
    const auto elapsed=now()-started;
    if (!valid_token(token) || ttl==0 || elapsed>=ttl || (state!="bound"&&state!="unbound")) return false;
    std::lock_guard<std::mutex> lock(mutex);
    if (generation!=identity_generation) return false;
    if (state=="bound") { bound_token=token; bound_until=started+ttl; }
    else { bootstrap_token=token; bootstrap_until=started+ttl; bound_token.clear(); bound_until=0; }
    return true;
}
Result start() {
    bool renew=false;
    { std::lock_guard<std::mutex> lock(mutex); renew=bootstrap_until-now()<150 && bool(identity_signer); }
    if (renew) refresh_identity();
    Result result;
    auto root=request(HTTP_METHOD_POST,"/private/v1/claim","{}");
    if (!root) return result;
    auto* data=cJSON_GetObjectItemCaseSensitive(root.get(),"data");
    result.code=string_field(data,"code"); result.claim_id=string_field(data,"claim_id");
    result.expires_in=lifetime(data);
    result.ok=result.code.size()==6 && std::all_of(result.code.begin(),result.code.end(),
        [](char c) { return c>='0' && c<='9'; }) && valid_id(result.claim_id) && result.expires_in>0;
    return result;
}
Result poll(const std::string& id) {
    Result result;
    if (!valid_id(id)) return result;
    const auto authenticated=bootstrap();
    auto root=request(HTTP_METHOD_GET,"/private/v1/claim/status?claim_id="+id);
    if (!root) return result;
    auto* data=cJSON_GetObjectItemCaseSensitive(root.get(),"data");
    const auto state=string_field(data,"state");
    result.expires_in=lifetime(data);
    if (state=="waiting") { result.ok=result.expires_in>0; return result; }
    if (state!="bound") return result;
    const auto token=string_field(data,"device_token");
    if (!valid_token(token) || result.expires_in==0) return result;
    std::lock_guard<std::mutex> lock(mutex);
    if (authenticated != "Bearer "+bootstrap_token || now()>=bootstrap_until) return result;
    bound_token=token; bound_until=now()+result.expires_in;
    result.ok=result.bound=true;
    return result;
}
void cancel(const std::string& id) {
    if (valid_id(id)) request(HTTP_METHOD_DELETE,"/private/v1/claim","{\"claim_id\":\""+id+"\"}");
}
}
