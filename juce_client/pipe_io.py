from __future__ import annotations

import struct
from typing import Tuple


class ServerError(RuntimeError):
    """Raised when the JUCE server reports an error during command processing."""
    pass


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

    def _raw_flush(self) -> None:
        """Flush the write buffer without reading a response status."""
        try:
            if self.commands_pipe_handle:
                self.commands_pipe_handle.flush()
        except Exception:
            pass

    def commands_pipe_handle_flush(self) -> None:
        """Flush writes and read the server's response status byte."""
        self._raw_flush()
        self._check_response_status()

    def _check_response_status(self) -> None:
        """Read the 1-byte response status. If 0xFF, read error string and raise."""
        if not self.commands_connected or not self.commands_pipe_handle:
            return
        # Check for async server errors (e.g. audio callback crash)
        pending = getattr(self, '_pending_server_error', None)
        if pending is not None:
            self._pending_server_error = None
            raise ServerError(pending)
        status_byte = read_exact(self.commands_pipe_handle, 1)
        if status_byte == b'\xff':
            # Error frame: read length-prefixed error message
            length_bytes = read_exact(self.commands_pipe_handle, 4)
            length = struct.unpack("<I", length_bytes)[0]
            msg = read_exact(self.commands_pipe_handle, length).decode("utf-8", errors="ignore") if length > 0 else "Unknown server error"
            raise ServerError(msg)
        # 0x00 = success, continue reading normal response data

    def _flush_and_check_n(self, n: int) -> None:
        """Flush writes and read N response status bytes (for bulk commands)."""
        self._raw_flush()
        for _ in range(n):
            self._check_response_status()

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

    # ---- raw bytes (binary-safe, length-prefixed; matches C++
    # write2c_string / readFromPipe<std::string>) ----

    def sendbytes(self, b: bytes) -> None:
        if not self.commands_connected:
            raise IOError("not connected")
        self.commands_pipe_handle.write(struct.pack("<I", len(b)) + bytes(b))

    def readbytes1(self) -> bytes:
        size = int(self.readinfo1c("I"))
        if size == 0:
            return b""
        return read_exact(self.commands_pipe_handle, size)

    # ---- command byte ----

    def sendcmd(self, command: int) -> None:
        self.sendinfo("B", int(command))
