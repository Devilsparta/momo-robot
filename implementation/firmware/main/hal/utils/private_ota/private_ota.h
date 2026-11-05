#pragma once
#include <functional>
#include <string>
#include <cstdint>
namespace private_ota {
enum class Health { Pending, Healthy, Failed };
struct Trust {
    // Installed only by trusted boot integration. No HTTP/NVS key enrolment.
    std::function<bool(const std::string&,const std::string&)> verify_signature;
    std::function<uint64_t()> trusted_unix_time;
    std::function<Health()> health;
    std::string artifact_origin; // Exact HTTPS origin; no credentials/query/path.
    uint64_t minimum_sequence=0;
    bool allow_install=false;
};
bool configure(Trust trust);
// Optional concrete verifier: SHA-256, P-256 public PEM, DER signature in hex.
// No key generation, persistence or default trust root.
bool verify_p256(const std::string& public_pem,const std::string& message,const std::string& signature_hex);
bool offer(const std::string& manifest_json,const std::string& signature,std::string& url,std::string& version);
bool install(const std::string& exact_url,std::function<void(int,size_t)> progress);
void cancel();
// Called from the existing HAL maintenance loop. No trust => no confirmation.
void reconcile_boot();
}
