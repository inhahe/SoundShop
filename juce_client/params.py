from __future__ import annotations

from typing import Iterable, Tuple

from .protocol import send_cmd

try:
    from .daw.project import coalesce_param_changes
except Exception:
    def coalesce_param_changes(changes, epsilon: float = 0.0):
        return changes

class ParamAutomationAPI:
    """Parameter automation scheduling."""

    def scheduleparamchange(self, pluginId: int, parameterIndex: int, value: float, atSample: int):
        self.sendcmd(send_cmd.schedule_param_change)
        self.sendinfo("IIfQ", int(pluginId), int(parameterIndex), float(value), int(atSample))
        self.commands_pipe_handle_flush()

    def scheduleparamchanges(self, changes: Iterable[Tuple[int, int, float, int]], *, epsilon: float = 0.0):
        changes2 = coalesce_param_changes(list(changes), epsilon=epsilon)
        for pluginKey, parameterIndex, value, atSample in changes2:
            self.sendcmd(send_cmd.schedule_param_change)
            self.sendinfo("IIfQ", int(pluginKey), int(parameterIndex), float(value), int(atSample))
        self.commands_pipe_handle_flush()

    def clearparamschedule(self):
        self.sendcmd(send_cmd.clear_param_schedule)
        self.commands_pipe_handle_flush()
