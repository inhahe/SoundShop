from __future__ import annotations

from typing import List, Dict, Any, Optional, Sequence

from .protocol import send_cmd
from .types import Processor

class PluginAPI:
    """Plugin discovery + metadata + UI. Mixed into JuceAudioClient."""

    def loadplugin(self, path: str, key: int):
        self.sendcmd(send_cmd.load_plugin)
        self.sendstr(path)
        self.sendinfo("I", int(key))
        self.commands_pipe_handle_flush()

        success = int(self.readinfo1c("I"))
        name = self.readstr1()
        uid = int(self.readinfo1c("I"))
        errmsg = self.readstr1()
        return success, name, uid, errmsg

    def loadpluginbyuid(self, uid: int, key: int) -> Processor:
        self.sendcmd(send_cmd.load_plugin_by_uid)
        self.sendinfo("II", int(uid), int(key))
        self.commands_pipe_handle_flush()

        success = int(self.readinfo1c("I"))
        self.readstr1()  # name (unused)
        errmsg = self.readstr1()
        if not success:
            raise ValueError("loading plugin failed. errmsg: " + errmsg)
        return Processor(uid=int(uid), key=int(key))

    def scanplugins(self, directories: Optional[Sequence[str]] = None, badpaths: Optional[Sequence[str]] = None) -> int:
        directories = list(directories) if directories is not None else (self.pluginDirectories or [])
        badpaths = list(badpaths) if badpaths is not None else (self.badPluginPaths or [])
        if not directories:
            raise ValueError("directories must be provided (or set client.pluginDirectories)")
        self.sendcmd(send_cmd.scan_plugins)
        self.sendinfo("I", len(directories))
        self.sendstrs(directories)
        self.sendinfo("I", len(badpaths))
        self.sendstrs(badpaths)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def listplugins(self):
        self.sendcmd(send_cmd.list_plugins)
        self.commands_pipe_handle_flush()
        size = int(self.readinfo1c("I"))
        plugins = []
        for _ in range(size):
            p: Dict[str, Any] = {}
            p["isInstrument"], p["pluginId"], p["numInputChannels"], p["numOutputChannels"] = self.readinfoc("IIII")
            (
                p["name"], p["descriptiveName"], p["pluginFormatName"], p["category"], p["manufacturerName"],
                p["version"], p["fileOrIdentifier"], p["lastFileModTime"], p["path"]
            ) = self.readstrs(9)
            plugins.append(p)
        self.availablePlugins = plugins
        return plugins

    def listbadpaths(self):
        self.sendcmd(send_cmd.list_bad_paths)
        self.commands_pipe_handle_flush()
        size = int(self.readinfo1c("I"))
        return [self.readstr1() for _ in range(size)]

    def getPluginInfo(self, pluginId: int):
        self.sendcmd(send_cmd.get_plugin_info)
        self.sendinfo("I", int(pluginId))
        self.commands_pipe_handle_flush()
        p: Dict[str, Any] = {}
        p["isInstrument"], p["pluginId"], p["numInputChannels"], p["numOutputChannels"] = self.readinfoc("IIII")
        (
            p["name"], p["descriptiveName"], p["pluginFormatName"], p["category"], p["manufacturerName"],
            p["version"], p["fileOrIdentifier"], p["lastFileModTime"], p["path"]
        ) = self.readstrs(9)
        return p

    def getParamsInfo(self, pluginKey: int) -> List[Dict[str, Any]]:
        self.sendcmd(send_cmd.get_params_info)
        self.sendinfo("I", int(pluginKey))
        self.commands_pipe_handle_flush()

        success, num_params = self.readinfoc("II")
        errmsg = self.readstr1()
        if not int(success):
            raise RuntimeError(f"get_params_info failed: {errmsg}")

        params: List[Dict[str, Any]] = []
        for _ in range(int(num_params)):
            (originalIndex,) = self.readinfoc("I")
            name = self.readstr1()
            minValue, maxValue, interval, defaultValue, skewFactor, value = self.readinfoc("ffffff")
            numSteps, isDiscrete, isBoolean, isOrientationInverted, isAutomatable, isMetaParameter = self.readinfoc("IIIIII")
            params.append({
                "originalIndex": int(originalIndex),
                "name": name,
                "minValue": float(minValue),
                "maxValue": float(maxValue),
                "interval": float(interval),
                "defaultValue": float(defaultValue),
                "skewFactor": float(skewFactor),
                "value": float(value),
                "numSteps": int(numSteps),
                "isDiscrete": int(isDiscrete),
                "isBoolean": int(isBoolean),
                "isOrientationInverted": int(isOrientationInverted),
                "isAutomatable": int(isAutomatable),
                "isMetaParameter": int(isMetaParameter),
            })
        return params

    def getChannelsInfo(self, pluginKey: int) -> Dict[str, Any]:
        self.sendcmd(send_cmd.get_channels_info)
        self.sendinfo("I", int(pluginKey))
        self.commands_pipe_handle_flush()

        success, acceptsMidi, producesMidi = self.readinfoc("III")
        in_buses: List[Dict[str, Any]] = []
        out_buses: List[Dict[str, Any]] = []

        (num_input_buses,) = self.readinfoc("I")
        for _ in range(int(num_input_buses)):
            numChannels, numChannelTypes = self.readinfoc("II")
            channelTypes = [self.readstr1() for _ in range(int(numChannelTypes))]
            (isEnabled,) = self.readinfoc("I")
            mainBusLayout = self.readstr1()
            in_buses.append({
                "numChannels": int(numChannels),
                "channelTypes": channelTypes,
                "isEnabled": int(isEnabled),
                "mainBusLayout": mainBusLayout,
            })

        (num_output_buses,) = self.readinfoc("I")
        for _ in range(int(num_output_buses)):
            numChannels, numChannelTypes = self.readinfoc("II")
            channelTypes = [self.readstr1() for _ in range(int(numChannelTypes))]
            (isEnabled,) = self.readinfoc("I")
            mainBusLayout = self.readstr1()
            out_buses.append({
                "numChannels": int(numChannels),
                "channelTypes": channelTypes,
                "isEnabled": int(isEnabled),
                "mainBusLayout": mainBusLayout,
            })

        errmsg = self.readstr1()
        return {
            "success": int(success),
            "acceptsMidi": int(acceptsMidi),
            "producesMidi": int(producesMidi),
            "inputBuses": in_buses,
            "outputBuses": out_buses,
            "errmsg": errmsg,
        }

    def showpluginui(self, pluginId: int):
        self.sendcmd(send_cmd.show_plugin_ui)
        self.sendinfo("I", int(pluginId))
        self.commands_pipe_handle_flush()
        success = int(self.readinfo1c("I"))
        errmsg = self.readstr1()
        return success, errmsg

    def hidepluginui(self, pluginId: int):
        self.sendcmd(send_cmd.hide_plugin_ui)
        self.sendinfo("I", int(pluginId))
        self.commands_pipe_handle_flush()
        success = int(self.readinfo1c("I"))
        return success, ""

    def setparameter(self, pluginKey: int, parameterIndex: int, value: float):
        self.sendcmd(send_cmd.set_parameter)
        self.sendinfo("IIf", int(pluginKey), int(parameterIndex), float(value))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def getparameter(self, pluginKey: int, parameterIndex: int) -> float:
        self.sendcmd(send_cmd.get_parameter)
        self.sendinfo("II", int(pluginKey), int(parameterIndex))
        self.commands_pipe_handle_flush()
        success = int(self.readinfo1c("I"))
        value = float(self.readinfo1c("f"))
        if not success:
            raise RuntimeError(f"get_parameter failed for key={pluginKey} param={parameterIndex}")
        return value

    def removeplugin(self, pluginKey: int):
        self.sendcmd(send_cmd.remove_plugin)
        self.sendinfo("I", int(pluginKey))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def clearallplugins(self):
        self.sendcmd(send_cmd.clear_all_plugins)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))
