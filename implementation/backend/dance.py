"""Owner-scoped choreography storage for the actual upstream v2 App requests.

No music downloads, file upload, shared library, or automatic sample creation.
"""
import json
import re
import sqlite3
from .device_store import Denied


class Dances:
    def __init__(self,store):
        self.store,self.db=store,store.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS private_dances (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, mac TEXT NOT NULL,
            name TEXT NOT NULL, frames TEXT NOT NULL, UNIQUE(owner,mac,name))""")
        self.db.commit()

    def validate(self,data):
        name=data.get("danceName")
        if not isinstance(name,str) or not 1<=len(name)<=64 or any(ord(c)<32 for c in name):raise ValueError("dance name")
        if data.get("musicUrl","") not in ("",None):raise Denied("external music disabled")
        frames=data.get("danceData")
        if not isinstance(frames,list) or not 1<=len(frames)<=256:raise ValueError("dance frames")
        features={"leftEye","rightEye","mouth","yawServo","pitchServo"}
        for frame in frames:
            if not isinstance(frame,dict) or set(frame)-features-{"durationMs","leftRgbColor","rightRgbColor"}:raise ValueError("dance fields")
            if not features.issubset(frame) or type(frame.get("durationMs")) is not int or not 1<=frame["durationMs"]<=60000:raise ValueError("dance duration")
            for feature in features:
                values=frame[feature]
                allowed={"angle","speed","rotate"} if feature.endswith("Servo") else {"x","y","rotation","weight","size"}
                if not isinstance(values,dict) or set(values)-allowed or any(type(v) is not int or abs(v)>100000 for v in values.values()):raise ValueError("dance feature")
            for color in ("leftRgbColor","rightRgbColor"):
                if color in frame and (not isinstance(frame[color],str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",frame[color])):raise ValueError("dance color")
        raw=json.dumps(frames,separators=(",",":"),allow_nan=False)
        if len(raw.encode())>60000:raise ValueError("dance too large")
        return name,raw

    @staticmethod
    def result(row):
        return {"id":row[0],"danceName":row[3],"danceData":json.loads(row[4]),"musicUrl":"","danceIndex":row[0]}

    def owned(self,user,ident):
        owner=self.store.user(user)
        if isinstance(ident,str) and ident.isascii() and ident.isdecimal():ident=int(ident)
        if type(ident) is not int or ident<=0:raise ValueError("dance id")
        row=self.db.execute("SELECT id,owner,mac,name,frames FROM private_dances WHERE id=? AND owner=?",(ident,owner)).fetchone()
        if row is None:raise Denied("dance unavailable")
        self.store.owned(user,row[2])
        return row

    def list(self,user,address):
        address,_=self.store.owned(user,address)
        return [self.result(row) for row in self.db.execute("SELECT id,owner,mac,name,frames FROM private_dances WHERE owner=? AND mac=? ORDER BY id",(user.subject,address))]

    def create(self,user,data):
        if set(data)-{"mac","danceName","danceData","musicUrl"}:raise ValueError("dance create fields")
        address,_=self.store.owned(user,data["mac"])
        name,frames=self.validate(data)
        if self.db.execute("SELECT COUNT(*) FROM private_dances WHERE owner=? AND mac=?",(user.subject,address)).fetchone()[0]>=32:raise Denied("dance capacity")
        try:
            with self.db:self.db.execute("INSERT INTO private_dances(owner,mac,name,frames) VALUES (?,?,?,?)",(user.subject,address,name,frames))
        except sqlite3.IntegrityError:raise Denied("dance name exists") from None
        return "Dance saved"

    def update(self,user,data):
        if set(data)-{"id","danceName","danceData","musicUrl"}:raise ValueError("dance update fields")
        row=self.owned(user,data["id"])
        name,frames=self.validate(data)
        try:
            with self.db:self.db.execute("UPDATE private_dances SET name=?,frames=? WHERE id=? AND owner=?",(name,frames,row[0],user.subject))
        except sqlite3.IntegrityError:raise Denied("dance name exists") from None
        return "Dance saved"

    def delete(self,user,ident):
        row=self.owned(user,ident)
        with self.db:self.db.execute("DELETE FROM private_dances WHERE id=? AND owner=?",(row[0],user.subject))
        return "Dance deleted"

    def purge(self,user,address):
        address,_=self.store.owned(user,address)
        with self.db:self.db.execute("DELETE FROM private_dances WHERE owner=? AND mac=?",(user.subject,address))
