"""Local opt-in notes only. Not automatic conversation retention."""
import secrets
from .device_store import Denied

class Memory:
    def __init__(self, store):
        self.store = store
        store.db.execute("CREATE TABLE IF NOT EXISTS memory_consent (owner TEXT, mac TEXT, enabled INTEGER, PRIMARY KEY(owner,mac))")
        store.db.execute("CREATE TABLE IF NOT EXISTS memory_notes (id TEXT PRIMARY KEY, owner TEXT, mac TEXT, text TEXT)")
        store.db.commit()

    def consent(self, user, address, enabled):
        address, _ = self.store.owned(user, address)
        if type(enabled) is not bool: raise ValueError("boolean consent required")
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO memory_consent VALUES (?,?,?)", (user.subject,address,int(enabled)))
            if not enabled: self.store.db.execute("DELETE FROM memory_notes WHERE owner=? AND mac=?", (user.subject,address))

    def allowed(self, user, address):
        address, _ = self.store.owned(user, address)
        row = self.store.db.execute("SELECT enabled FROM memory_consent WHERE owner=? AND mac=?", (user.subject,address)).fetchone()
        if not row or row[0] != 1: raise Denied("local memory requires explicit opt-in")
        return address

    def add(self, user, address, text):
        address = self.allowed(user,address)
        if not isinstance(text,str) or not 1 <= len(text.encode()) <= 4096: raise ValueError("invalid memory size")
        count = self.store.db.execute("SELECT COUNT(*) FROM memory_notes WHERE owner=? AND mac=?", (user.subject,address)).fetchone()[0]
        if count >= 100: raise ValueError("memory quota reached")
        ident = secrets.token_hex(16)
        with self.store.db: self.store.db.execute("INSERT INTO memory_notes VALUES (?,?,?,?)", (ident,user.subject,address,text))
        return ident

    def export(self, user, address):
        address = self.allowed(user,address)
        return [{"id":r[0],"text":r[1]} for r in self.store.db.execute("SELECT id,text FROM memory_notes WHERE owner=? AND mac=? ORDER BY id",(user.subject,address))]

    def delete(self, user, address, ident):
        address, _ = self.store.owned(user,address)
        with self.store.db:
            self.store.db.execute("DELETE FROM memory_notes WHERE id=? AND owner=? AND mac=?", (ident,user.subject,address))

    def purge(self, user, address):
        address, _ = self.store.owned(user,address)
        with self.store.db:
            self.store.db.execute("DELETE FROM memory_notes WHERE owner=? AND mac=?", (user.subject,address))
            self.store.db.execute("DELETE FROM memory_consent WHERE owner=? AND mac=?", (user.subject,address))
