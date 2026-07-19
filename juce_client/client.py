from __future__ import annotations

import os
import platform
import threading
import time
from typing import Callable, Optional, Sequence

from .protocol import pipe_name, recv_cmd
from .pipe_io import PipeIO, read_exact

class JuceAudioClient(PipeIO):
    def __init__(
        self,
        pipe_name: str = pipe_name,
        server_exe_path: Optional[str] = None,
        pluginDirectories: Optional[Sequence[str]] = None,
        badPluginPaths: Optional[Sequence[str]] = None,
    ):
        self.pipe_name = pipe_name
        self.commands_pipe_path = f"\\\\.\\pipe\\{pipe_name}_commands"
        self.notifications_pipe_path = f"\\\\.\\pipe\\{pipe_name}_notifications"

        self.commands_pipe_handle = None
        self.notifications_pipe_handle = None
        self.commands_connected = False
        self.notifications_connected = False

        self.server_process = None
        self.server_exe_path = server_exe_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "juce_gui_server.exe",
        )

        self.availablePlugins = None
        self.pluginDirectories = list(pluginDirectories) if pluginDirectories is not None else None
        self.badPluginPaths = list(badPluginPaths) if badPluginPaths is not None else []

        self._pending_server_error: Optional[str] = None

    def start_server(self) -> bool:
        """Launch the JUCE server process if not already running."""
        import subprocess

        if not os.path.exists(self.server_exe_path):
            print(f"Server executable not found at: {self.server_exe_path}")
            return False

        try:
            print(f"Launching server: {self.server_exe_path} {self.pipe_name}")
            self.server_process = subprocess.Popen(
                [self.server_exe_path, self.pipe_name],
                creationflags=subprocess.CREATE_NEW_CONSOLE if platform.system() == "Windows" else 0,
            )
            time.sleep(1.0)
            return True
        except Exception as e:
            print(f"Failed to launch server: {e}")
            return False

    def connect(self, auto_start: bool = True, max_retries: int = 5, retry_delay: float = 1.0) -> bool:
        """Connect to the JUCE server pipes, optionally auto-starting the server."""
        for attempt in range(max_retries):
            # Check if the server process died
            if self.server_process is not None:
                rc = self.server_process.poll()
                if rc is not None:
                    print(f"Server process exited with code {rc}")
                    self.server_process = None
                    if auto_start:
                        print("Restarting server...")
                        if not self.start_server():
                            print("Failed to restart server")
                            return False
                        time.sleep(retry_delay)
                        continue

            try:
                print(f"Connecting to {self.pipe_name}... (attempt {attempt + 1}/{max_retries})")
                self.commands_pipe_handle = open(self.commands_pipe_path, "w+b", buffering=0)
                self.notifications_pipe_handle = open(self.notifications_pipe_path, "rb", buffering=0)
                self.commands_connected = True
                self.notifications_connected = True
                print("Connected successfully!")
                return True
            except Exception as e:
                print(f"Connection attempt {attempt + 1} failed: {e}")

                if attempt == 0 and auto_start:
                    if self.start_server():
                        time.sleep(retry_delay)
                        continue
                    print("Failed to start server automatically")
                    return False

                time.sleep(retry_delay)

        print("Failed to connect to server after maximum retries")
        return False

    def disconnect(self, shutdown_server: bool = False) -> None:
        """Disconnect from server; optionally send shutdown command first."""
        if self.commands_connected and self.commands_pipe_handle:
            try:
                if shutdown_server:
                    try:
                        from .protocol import send_cmd
                        self.sendcmd(send_cmd.cmd_shutdown)
                        self.commands_pipe_handle_flush()
                        time.sleep(0.5)
                    except Exception:
                        pass
                self.commands_pipe_handle.close()
            except Exception:
                pass
            self.commands_connected = False

        if self.notifications_connected and self.notifications_pipe_handle:
            try:
                self.notifications_pipe_handle.close()
            except Exception:
                pass
            self.notifications_connected = False

        # Stop notification listener
        self._notification_running = False
        if hasattr(self, '_notification_thread') and self._notification_thread is not None:
            self._notification_thread.join(timeout=2)
            self._notification_thread = None

        if self.server_process:
            try:
                self.server_process.wait(timeout=3)
            except Exception:
                try:
                    self.server_process.terminate()
                except Exception:
                    pass
            self.server_process = None

    # ---- Notification Listener ----

    def start_notification_listener(self, callback: Optional[Callable] = None):
        """Start background thread to receive notifications from the server.

        The callback receives (cmd, data) where cmd is a recv_cmd value and data is a dict.
        If no callback is provided, notifications are stored in self.notification_log.
        """
        self._notification_callback = callback
        self.notification_log = []
        self._notification_running = True
        self._notification_thread = threading.Thread(
            target=self._notification_loop, daemon=True
        )
        self._notification_thread.start()

    def _read_notification_bytes(self, n):
        return read_exact(self.notifications_pipe_handle, n)

    def __del__(self):
        try:
            self.disconnect(shutdown_server=True)
        except Exception:
            pass

    def _notification_loop(self):
        import struct
        while self._notification_running and self.notifications_connected:
            try:
                cmd_byte = self._read_notification_bytes(1)
                if not cmd_byte:
                    break  # pipe closed
                cmd = struct.unpack("<B", cmd_byte)[0]
                data = self._parse_notification(cmd)
                # Store server errors so the next command raises
                if cmd == recv_cmd.server_error:
                    self._pending_server_error = data.get("message", "Unknown server error")
                if self._notification_callback:
                    try:
                        self._notification_callback(cmd, data)
                    except Exception:
                        pass
                else:
                    self.notification_log.append((cmd, data))
            except Exception:
                break

    def _parse_notification(self, cmd):
        """Parse a notification based on its command type. Returns a dict."""
        import struct

        def _read(fmt):
            size = struct.calcsize("<" + fmt)
            return struct.unpack("<" + fmt, self._read_notification_bytes(size))

        def _read_str():
            (length,) = _read("I")
            return self._read_notification_bytes(length).decode("utf-8", errors="ignore")

        if cmd == recv_cmd.param_changed:
            key, paramIdx, value = _read("IIf")
            (atSample,) = _read("Q")
            return {"pluginKey": key, "parameterIndex": paramIdx, "value": value, "atSample": atSample}

        elif cmd == recv_cmd.param_changes_end:
            return {}

        elif cmd == recv_cmd.stop_playback:
            return {}

        elif cmd == recv_cmd.midi_note_event:
            noteNumber, velocity, channel, isNoteOn, samplePosition = _read("IIIIQ")
            return {"noteNumber": noteNumber, "velocity": velocity, "channel": channel,
                    "isNoteOn": bool(isNoteOn), "samplePosition": samplePosition}

        elif cmd == recv_cmd.midi_cc_event:
            controller, value, channel = _read("III")
            (atSample,) = _read("Q")
            return {"controller": controller, "value": value, "channel": channel, "atSample": atSample}

        elif cmd == recv_cmd.virtual_keyboard_note_event:
            noteNumber, velocity, channel, isNoteOn, samplePosition = _read("IIIIQ")
            return {"noteNumber": noteNumber, "velocity": velocity, "channel": channel,
                    "isNoteOn": bool(isNoteOn), "samplePosition": samplePosition}

        elif cmd == recv_cmd.virtual_keyboard_cc_event:
            controller, value, channel = _read("III")
            (atSample,) = _read("Q")
            return {"controller": controller, "value": value, "channel": channel, "atSample": atSample}

        elif cmd == recv_cmd.midi_keyboard_routed:
            pluginId, samplePosition = _read("iQ")
            return {"pluginId": pluginId, "samplePosition": samplePosition}

        elif cmd == recv_cmd.virtual_keyboard_routed:
            pluginId, samplePosition = _read("iQ")
            return {"pluginId": pluginId, "samplePosition": samplePosition}

        elif cmd == recv_cmd.recording_started:
            filename = _read_str()
            (startSample,) = _read("Q")
            return {"filename": filename, "startSample": startSample}

        elif cmd == recv_cmd.recording_stopped:
            filename = _read_str()
            (endSample,) = _read("Q")
            return {"filename": filename, "endSample": endSample}

        elif cmd == recv_cmd.monitoring_changed:
            state, samplePosition = _read("IQ")
            return {"state": bool(state), "samplePosition": samplePosition}

        elif cmd == recv_cmd.audio_file_loaded:
            filename = _read_str()
            (playerId,) = _read("I")
            return {"filename": filename, "playerId": playerId}

        elif cmd == recv_cmd.audio_playback_started:
            playerId, samplePosition = _read("IQ")
            return {"playerId": playerId, "samplePosition": samplePosition}

        elif cmd == recv_cmd.audio_playback_stopped:
            playerId, samplePosition = _read("IQ")
            return {"playerId": playerId, "samplePosition": samplePosition}

        elif cmd == recv_cmd.ordered_note_triggered:
            orderNumber, currentIndex, totalCount = _read("III")
            return {"orderNumber": orderNumber, "currentIndex": currentIndex, "totalCount": totalCount}

        elif cmd == recv_cmd.ordered_playback_started:
            (samplePosition,) = _read("Q")
            return {"samplePosition": samplePosition}

        elif cmd == recv_cmd.ordered_playback_stopped:
            (samplePosition,) = _read("Q")
            return {"samplePosition": samplePosition}

        elif cmd == recv_cmd.server_error:
            message = _read_str()
            return {"message": message}

        return {"raw_cmd": cmd}
