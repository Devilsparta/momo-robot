#pragma once
#include <string>
#include <cstdint>
#include <functional>

namespace private_pairing {
// Trusted provisioning integration installs only short-lived credentials in RAM.
// No factory secret, NVS, eFuse or compile-time token fallback.
// Trusted boot integration supplies a signer backed by an approved provisioner.
// This API creates/stores no key; a missing signer leaves authentication disabled.
using Signer = std::function<std::string(const std::string&)>;
bool install_identity_signer(const std::string& key_id, Signer signer);
bool refresh_identity();
bool install_bootstrap(const std::string& token, uint32_t ttl_seconds);
std::string authorization();
void clear();
struct Result {
    bool ok = false;
    bool bound = false;
    std::string code;
    std::string claim_id;
    uint32_t expires_in = 0;
};
Result start();
Result poll(const std::string& claim_id);
void cancel(const std::string& claim_id);
}
