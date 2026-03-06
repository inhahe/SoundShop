from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Small shared models used by multiple modules.

@dataclass
class Processor:
    uid: int
    key: int

@dataclass
class ParamSchedule(list):
    pass

@dataclass
class AudioTrack:
    filePath: Optional[str] = None
    processor: Optional[Processor] = None

# --- Render-plan-ish types (used by the "render context" section) ---

@dataclass(frozen=True)
class MidiNoteEvent:
    sample: int
    on: bool
    channel: int
    pitch: int
    velocity: int
    note_id: int

@dataclass(frozen=True)
class MidiCCEvent:
    sample: int
    channel: int
    cc: int
    value: int

@dataclass(frozen=True)
class ParamEvent:
    sample: int
    node_id: int
    param_id: int
    value: float

@dataclass(frozen=True)
class PluginNode:
    node_id: int
    vst3_uid: str
    name: str
    kind: str

@dataclass(frozen=True)
class PluginEdge:
    src_node: int
    src_port: str
    dst_node: int
    dst_port: str
    kind: str
    gain: float = 1.0

@dataclass
class RenderPlan:
    nodes: list[PluginNode] = field(default_factory=list)
    edges: list[PluginEdge] = field(default_factory=list)
    notes: list[MidiNoteEvent] = field(default_factory=list)
    ccs: list[MidiCCEvent] = field(default_factory=list)
    params: list[ParamEvent] = field(default_factory=list)
