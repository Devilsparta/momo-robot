"""Per-owner/device tool allowlist; no built-in device actions or external tools."""
import asyncio
import contextlib
from .device_store import Denied

class ToolPolicy:
    def __init__(self, store, timeout=2):
        self.store, self.timeout = store, timeout
        self.handlers, self.permissions = {}, set()

    def register(self, name, handler, validate):
        self.handlers[name] = (handler, validate)

    async def call(self, session, name, arguments):
        self.store.check_session(session)
        if (session[0],session[1],name) not in self.permissions or name not in self.handlers:
            raise Denied("tool not authorized")
        handler, validate = self.handlers[name]
        if not isinstance(arguments,dict) or validate(arguments) is not True:
            raise ValueError("invalid tool arguments")
        task = asyncio.create_task(handler(arguments))
        try:
            done, _ = await asyncio.wait({task}, timeout=self.timeout)
            if not done:
                task.cancel()
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                raise TimeoutError("tool deadline exceeded")
            result = task.result()
            self.store.check_session(session)
            if (session[0], session[1], name) not in self.permissions:
                raise Denied("tool permission revoked")
            return result
        except BaseException:
            if not task.done(): task.cancel()
            raise
