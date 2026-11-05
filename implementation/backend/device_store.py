"""Device ownership core. No HTTP listener, cloud access, or credential generation.
Caller identity MUST come from a trusted authenticator, never request JSON.
The proof verifier is injected; production provisioning is intentionally unresolved.
"""
import json
import re
import sqlite3
from dataclasses import dataclass


class Denied(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    kind: str


def mac(value):
    if not isinstance(value, str):
        raise ValueError("MAC must be text")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|(?:[0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2})", value):
        raise ValueError("invalid MAC")
    return value.replace(":", "").replace("-", "").lower()


class DeviceStore:
    def __init__(self, connection, verify_claim=None, clock=None):
        import time
        self.db = connection
        self.verify_claim = verify_claim
        self.clock = clock or time.time
        self.db.execute("""CREATE TABLE IF NOT EXISTS devices (
            mac TEXT PRIMARY KEY, owner TEXT, name TEXT NOT NULL DEFAULT '',
            epoch INTEGER NOT NULL DEFAULT 0, config TEXT NOT NULL DEFAULT '{}')""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS claims (
            proof_id TEXT PRIMARY KEY, used_at REAL NOT NULL)""")
        self.db.commit()

    def user(self, principal):
        if not isinstance(principal, Principal) or principal.kind != "user" or not principal.subject:
            raise Denied("authenticated user required")
        return principal.subject

    def bind(self, principal, address, proof):
        owner = self.user(principal)
        address = mac(address)
        if self.verify_claim is None:
            raise Denied("claim verifier not configured")
        # Verifier must prove physical possession AND bind proof to this owner and MAC.
        # Returns (unique proof id, expiration); never accepts a MAC alone.
        proof_id, expires = self.verify_claim(owner, address, proof)
        if not isinstance(proof_id, str) or not proof_id or expires <= self.clock():
            raise Denied("invalid or expired claim")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT owner FROM devices WHERE mac=?", (address,)).fetchone()
            if row and row[0] not in (None, owner):
                raise Denied("already owned")
            self.db.execute("INSERT INTO claims VALUES (?,?)", (proof_id, self.clock()))
            self.db.execute("INSERT OR IGNORE INTO devices(mac) VALUES (?)", (address,))
            self.db.execute("UPDATE devices SET owner=?, epoch=epoch+1 WHERE mac=?", (owner, address))
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            raise Denied("claim already consumed") from None
        except Exception:
            self.db.rollback()
            raise
        return True

    def owned(self, principal, address):
        owner, address = self.user(principal), mac(address)
        row = self.db.execute("SELECT owner,name,epoch,config FROM devices WHERE mac=?", (address,)).fetchone()
        if not row or row[0] != owner:
            raise Denied("device unavailable")
        return address, row

    def list_devices(self, principal):
        owner = self.user(principal)
        return [{"mac": r[0], "name": r[1]} for r in self.db.execute(
            "SELECT mac,name FROM devices WHERE owner=? ORDER BY mac", (owner,))]

    def rename(self, principal, address, name):
        address, _ = self.owned(principal, address)
        if not isinstance(name, str) or not 1 <= len(name) <= 64 or any(ord(c) < 32 for c in name):
            raise ValueError("invalid name")
        with self.db:
            changed = self.db.execute("UPDATE devices SET name=? WHERE mac=? AND owner=?", (name, address, principal.subject))
            if changed.rowcount != 1: raise Denied("ownership changed")

    def configure(self, principal, address, config):
        address, _ = self.owned(principal, address)
        # Preserve explicitly supported settings, reject unknown rather than silently dropping.
        if not isinstance(config, dict) or set(config) - {"volume", "brightness", "language"}:
            raise ValueError("unsupported setting")
        for field in ("volume", "brightness"):
            if field in config and (type(config[field]) is not int or not 0 <= config[field] <= 100):
                raise ValueError("invalid setting")
        if "language" in config and config["language"] not in ("zh-CN", "en-US", "ja-JP"):
            raise ValueError("unsupported language")
        with self.db:
            changed = self.db.execute("UPDATE devices SET config=? WHERE mac=? AND owner=?", (json.dumps(config), address, principal.subject))
            if changed.rowcount != 1: raise Denied("ownership changed")

    def get_config(self, principal, address):
        return json.loads(self.owned(principal, address)[1][3])

    def session(self, principal, address):
        address, row = self.owned(principal, address)
        return (principal.subject, address, row[2])

    def check_session(self, session):
        owner, address, epoch = session
        row = self.db.execute("SELECT owner,epoch FROM devices WHERE mac=?", (address,)).fetchone()
        if not row or row != (owner, epoch):
            raise Denied("session revoked")

    def unbind(self, principal, address):
        address, _ = self.owned(principal, address)
        with self.db:
            changed = self.db.execute("UPDATE devices SET owner=NULL,name='',epoch=epoch+1,config='{}' WHERE mac=? AND owner=?", (address, principal.subject))
            if changed.rowcount != 1: raise Denied("ownership changed")
        # All channels must call check_session for each command and terminate on revocation.
        return True
