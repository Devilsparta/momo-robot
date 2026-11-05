"""Preparation checks only; does not import or execute upstream code."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from check_config import load_config, no_duplicate_keys, validate


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config/development.example.json")

    def test_valid_example(self):
        validate(self.config)

    def test_real_endpoints_rejected(self):
        for url in ("https://api.example.com", "http://localhost:12800",
                    "https://management.example.invalid.evil.com",
                    "https://user:password@management.example.invalid",
                    "https://management.example.invalid?key=value"):
            with self.subTest(url=url):
                c = copy.deepcopy(self.config)
                c["endpoints"]["management_base"] = url
                with self.assertRaises(ValueError):
                    validate(c)

    def test_real_credential_rejected(self):
        self.config["identity"]["device_credential"] = "not-a-placeholder"
        with self.assertRaises(ValueError):
            validate(self.config)

    def test_egress_flags_rejected_including_truthy_strings(self):
        for key in self.config["data_policy"]:
            for value in (True, "false", 0, None):
                with self.subTest(key=key, value=value):
                    c = copy.deepcopy(self.config)
                    c["data_policy"][key] = value
                    with self.assertRaises(ValueError):
                        validate(c)

    def test_provider_ota_tools_cannot_be_enabled(self):
        for section, key, value in (("providers", "enabled", True),
                                    ("ota", "enabled", True),
                                    ("ota", "firmware_url", "https://firmware.example.invalid/fw.bin"),
                                    ("tools", "allowlist", ["motor.move"])):
            c = copy.deepcopy(self.config)
            c[section][key] = value
            with self.subTest(section=section, key=key):
                with self.assertRaises(ValueError):
                    validate(c)

    def test_unknown_field_rejected(self):
        self.config["providers"]["api_key"] = "unexpected"
        with self.assertRaises(ValueError):
            validate(self.config)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"enabled": false, "enabled": true}', object_pairs_hook=no_duplicate_keys)


