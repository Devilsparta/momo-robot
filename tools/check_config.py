"""Offline preparation config guard. No network, device or provider access."""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT_KEYS = {"schema_version", "mode", "hardware", "endpoints", "identity",
             "providers", "data_policy", "ota", "tools"}


def validate(config):
    if set(config) != ROOT_KEYS:
        raise ValueError("unknown or missing top-level keys")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("unsupported schema")
    if config["mode"] != "offline-preparation":
        raise ValueError("only offline-preparation is allowed")
    expected = {
        "hardware": {"sku", "firmware_version"},
        "endpoints": {"management_base", "voice_ota", "voice_ws"},
        "identity": {"strategy", "device_credential", "user_credential"},
        "providers": {"enabled", "name", "model", "credential"},
        "data_policy": {"audio_egress", "transcript_egress", "image_egress", "memory_egress"},
        "ota": {"enabled", "channel", "firmware_url"},
        "tools": {"default_allow", "allowlist"},
    }
    for section, keys in expected.items():
        if not isinstance(config[section], dict) or set(config[section]) != keys:
            raise ValueError("unknown or missing keys: " + section)
    for name, value in config["endpoints"].items():
        if not isinstance(value, str):
            raise ValueError("endpoint must be a string")
        uri = urlsplit(value)
        scheme = "wss" if name == "voice_ws" else "https"
        if (uri.scheme != scheme or not uri.hostname
                or not uri.hostname.endswith(".example.invalid")
                or uri.username is not None or uri.password is not None
                or uri.query or uri.fragment or uri.port is not None):
            raise ValueError("only credential-free reserved placeholder endpoints are allowed")
        expected_path = {"management_base": "", "voice_ota": "/xiaozhi/ota/",
                         "voice_ws": "/xiaozhi/v1/"}[name]
        if uri.path != expected_path:
            raise ValueError("unexpected endpoint path")
    for field in ("device_credential", "user_credential"):
        if config["identity"][field] != "PLACEHOLDER_" + field.upper():
            raise ValueError("credentials must remain placeholders")
    if config["identity"]["strategy"] != "TBD":
        raise ValueError("identity design is pending")
    if config["providers"] != {"enabled": False, "name": "TBD", "model": "TBD",
                               "credential": "PLACEHOLDER_PROVIDER_CREDENTIAL"}:
        raise ValueError("provider must remain disabled and unconfigured")
    if config["providers"]["enabled"] is not False:
        raise ValueError("provider flag must be boolean false")
    if any(value is not False for value in config["data_policy"].values()):
        raise ValueError("all data egress must remain disabled")
    if (config["ota"]["enabled"] is not False or config["ota"]["channel"] != "disabled"
            or config["ota"]["firmware_url"] != ""):
        raise ValueError("OTA must remain disabled")
    if config["tools"]["default_allow"] is not False or config["tools"]["allowlist"] != []:
        raise ValueError("tool execution must remain disabled")
    if config["hardware"] != {"sku": "K151", "firmware_version": "UNKNOWN"}:
        raise ValueError("hardware has not been verified")


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=no_duplicate_keys)


if __name__ == "__main__":
    try:
        validate(load_config(sys.argv[1]))
    except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
        print("FAIL:", error)
        sys.exit(1)
    print("PASS: offline placeholders only; provider, egress, OTA and tools disabled")
