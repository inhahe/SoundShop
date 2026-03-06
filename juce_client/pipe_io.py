from __future__ import annotations

import struct
from typing import Tuple

def read_exact(pipe_handle, n: int) -> bytes:
    """Read exactly n bytes, blocking until all are received."""
    data = b""
    while len(data) < n:
        chunk = pipe_handle.read(n - len(data))
        if not chunk:
            raise IOError("pipe closed before all data received. data: " + repr(data))
        data += chunk
    return data

class PipeIO:
    """Low-level binary/string helpers for the JUCE named pipe protocol."""

    commands_pipe_handle = None
    notifications_pipe_handle = None
    commands_connected: bool = False
    notifications_connected: bool = False

    # ---- flushing ----

    def commands_pipe_handle_flush(self) -> None:
        try:
            if self.commands_pipe_handle:
                self.commands_pipe_handle.flush()
        except Exception:
            pass

    # ---- packing/unpacking ----

    def sendinfo(self, pattern: str, *args) -> None:
        if not self.commands_connected:
            raise IOError("not connected")
        self.commands_pipe_handle.write(struct.pack("<" + pattern, *args))

    def readinfoc(self, pattern: str) -> Tuple:
        if not self.commands_connected:
            raise IOError("not connected")
        return struct.unpack("<" + pattern, read_exact(self.commands_pipe_handle, struct.calcsize("<" + pattern)))

    def readinfo1c(self, pattern: str):
        return self.readinfoc(pattern)[0]

    def readinfon(self, pattern: str) -> Tuple:
        if not self.notifications_connected:
            raise IOError("not connected")
        return struct.unpack("<" + pattern, read_exact(self.notifications_pipe_handle, struct.calcsize("<" + pattern)))

    def readinfo1n(self, pattern: str):
        return self.readinfon(pattern)[0]

    # ---- strings ----

    def readstr1(self) -> str:
        size = int(self.readinfo1c("I"))
        return read_exact(self.commands_pipe_handle, size).decode("utf-8", errors="ignore")

    def readstr(self) -> str:
        # compatibility alias: original file sometimes used readstr()
        return self.readstr1()

    def readstrs(self, num: int):
        return tuple(self.readstr1() for _ in range(num))

    def sendstr(self, s: str) -> None:
        if not self.commands_connected:
            raise IOError("not connected")
        b = s.encode("utf-8")
        self.commands_pipe_handle.write(struct.pack("<I", len(b)) + b)

    def sendstrs(self, ss) -> None:
        for s in ss:
            self.sendstr(str(s))

    # ---- command byte ----

    def sendcmd(self, command: int) -> None:
        self.sendinfo("B", int(command))
