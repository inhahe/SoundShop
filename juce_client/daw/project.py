from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Union, Tuple, Set, List, Any, Iterable
from collections import defaultdict

import yaml

from math import ceil

from ..music_theory import Note, Notes, NotesNode, NotesFilter, NotesGraph, merge_notes, make_tables, build_table, get_keys, build_notes, get_notes, change_key, shift_semitones, shift_octaves
from ..signals.core import Signal, EvalContext, ControlCache
TrackId = int
Port = str

@dataclass(frozen=True)
class Connection:
    id: int
    src: TrackId
    src_port: Port
    dst: TrackId
    dst_port: Port
    auto: bool = False

class Project:
    def __init__(self):
        self.tracks: Dict[int, Any] = {}
        self.connections: Dict[int, Connection] = {}
        self.track_order: List[int] = []
        self._out_index: Dict[Tuple[int, str], Set[int]] = {}
        self._in_index: Dict[Tuple[int, str], Set[int]] = {}
        self._next_conn_id: int = 0

    def disconnect(
        self,
        *,
        conn_id: Optional[int] = None,
        src: Optional[Union[Track, TrackId]] = None,
        src_port: Optional[Port] = None,
        dst: Optional[Union[Track, TrackId]] = None,
        dst_port: Optional[Port] = None,
        auto_only: bool = False,
        all_matches: bool = True,
    ) -> int:
        """
        Disconnect by:
          - conn_id, OR
          - endpoint match (src/src_port/dst/dst_port). Any None is treated as a wildcard.

        auto_only=True removes only edges created automatically (auto=True).
        all_matches=True removes all matching edges; otherwise removes at most one.
        Returns number removed.
        """
        if conn_id is not None:
            return 1 if self._remove_conn(conn_id, auto_only=auto_only) else 0

        src_id = src.id if isinstance(src, Track) else src
        dst_id = dst.id if isinstance(dst, Track) else dst

        # Candidate set from indices if possible
        candidates: Optional[Set[int]] = None
        if src_id is not None and src_port is not None:
            candidates = set(self._out_index.get((src_id, str(src_port)), set()))
        if dst_id is not None and dst_port is not None:
            in_set = set(self._in_index.get((dst_id, str(dst_port)), set()))
            candidates = in_set if candidates is None else (candidates & in_set)
        if candidates is None:
            candidates = set(self.connections.keys())

        removed = 0
        for cid in list(candidates):
            c = self.connections.get(cid)
            if c is None:
                continue
            if auto_only and not c.auto:
                continue
            if src_id is not None and c.src != src_id:
                continue
            if src_port is not None and c.src_port != str(src_port):
                continue
            if dst_id is not None and c.dst != dst_id:
                continue
            if dst_port is not None and c.dst_port != str(dst_port):
                continue

            self._remove_conn(cid, auto_only=False)
            removed += 1
            if not all_matches:
                break

        return removed

    def _check_port_exists(
            self,
            track_id: int,
            port: str,
            *,
            is_output: bool,
    ) -> None:
        g = self.tracks[track_id].graph
        ports = g.output_ports() if is_output else g.input_ports()
        if port not in ports:
            direction = "output" if is_output else "input"
            tname = self.tracks[track_id].name
            raise PortValidationError(
                f"Track '{tname}' (id={track_id}) has no declared {direction} port '{port}'. "
                f"Either declare it in the track graph or enable auto_declare_ports."
            )

    def _remove_conn(self, conn_id: int, *, auto_only: bool) -> bool:
        c = self.connections.get(conn_id)
        if c is None:
            return False
        if auto_only and not c.auto:
            return False

        del self.connections[conn_id]

        out_key = (c.src, c.src_port)
        in_key = (c.dst, c.dst_port)

        s = self._out_index.get(out_key)
        if s is not None:
            s.discard(conn_id)
            if not s:
                del self._out_index[out_key]

        s = self._in_index.get(in_key)
        if s is not None:
            s.discard(conn_id)
            if not s:
                del self._in_index[in_key]

        return True

    # ---------- “automatic port exposure” ----------

    def exposed_ports(self, track: Union[Track, TrackId]) -> Dict[str, Set[str]]:
        """
        Returns ports that are currently “used” by connections:
          outputs: any src_port used by outgoing edges
          inputs : any dst_port used by incoming edges

        This matches your “don’t predeclare ports; anything referenced is exposed” preference.
        """
        tid = track.id if isinstance(track, Track) else track
        outs: Set[str] = set()
        ins: Set[str] = set()

        for (src_id, src_port), ids in self._out_index.items():
            if src_id == tid and ids:
                outs.add(src_port)

        for (dst_id, dst_port), ids in self._in_index.items():
            if dst_id == tid and ids:
                ins.add(dst_port)

        return {"outputs": outs, "inputs": ins}

class AudioGraphCycleError(ValueError):
    pass

@dataclass
class CompiledAudioOrder:
    order: List[int]  # track ids in render order
    incoming_by_track: Dict[int, List[int]]  # dst_track -> list of src_track ids
    outgoing_by_track: Dict[int, List[int]]  # src_track -> list of dst_track ids

@dataclass
class EmptyPortGraph:
    _ins: Set[str] = field(default_factory=set)
    _outs: Set[str] = field(default_factory=set)

    def input_ports(self) -> Set[str]:
        return set(self._ins)

    def output_ports(self) -> Set[str]:
        return set(self._outs)

    def ensure_input_port(self, name: str) -> None:
        self._ins.add(str(name))

    def ensure_output_port(self, name: str) -> None:
        self._outs.add(str(name))

def compile_audio_graph(project: Project) -> CompiledAudioOrder:
    """
    Build track dependency graph from project.connections and return a topological render order.

    Rules:
      - Any connection src -> dst creates dependency: dst depends on src.
      - Self-edge is an immediate cycle.
      - Cycles raise AudioGraphCycleError with a readable path.
    """
    # Build adjacency + indegree for tracks
    outgoing: Dict[int, Set[int]] = {tid: set() for tid in project.tracks.keys()}
    incoming: Dict[int, Set[int]] = {tid: set() for tid in project.tracks.keys()}

    # Add edges
    for c in project.connections.values():
        # If you later add midi/control edges, filter by kind here.
        src = c.src
        dst = c.dst
        if src == dst:
            raise AudioGraphCycleError(f"Self-cycle on track {project.tracks[src].name} (id={src})")
        outgoing[src].add(dst)
        incoming[dst].add(src)

    indegree: Dict[int, int] = {tid: len(incoming[tid]) for tid in project.tracks.keys()}

    # Kahn's algorithm with deterministic tie-break:
    # prefer project.track_order if provided, otherwise by id.
    order_hint = {tid: i for i, tid in enumerate(project.track_order)}

    ready: List[int] = [tid for tid, deg in indegree.items() if deg == 0]
    ready.sort(key=lambda tid: order_hint.get(tid, 10 ** 9))

    topo: List[int] = []
    while ready:
        n = ready.pop(0)
        topo.append(n)

        for m in sorted(outgoing[n], key=lambda tid: order_hint.get(tid, 10 ** 9)):
            indegree[m] -= 1
            if indegree[m] == 0:
                # insert while keeping order_hint sort; for speed use heapq if you care
                ready.append(m)
                ready.sort(key=lambda tid: order_hint.get(tid, 10 ** 9))

    if len(topo) != len(project.tracks):
        # There is a cycle. Produce a readable cycle path.
        cycle = _find_cycle_path(project, outgoing)
        raise AudioGraphCycleError(
            "Audio routing cycle detected: " + " -> ".join(
                f"{project.tracks[tid].name}(id={tid})" for tid in cycle
            )
        )

    # Convert sets to stable lists
    incoming_by_track = {tid: sorted(list(srcs), key=lambda t: order_hint.get(t, 10 ** 9))
                         for tid, srcs in incoming.items()}
    outgoing_by_track = {tid: sorted(list(dsts), key=lambda t: order_hint.get(t, 10 ** 9))
                         for tid, dsts in outgoing.items()}

    return CompiledAudioOrder(
        order=topo,
        incoming_by_track=incoming_by_track,
        outgoing_by_track=outgoing_by_track,
    )

def _find_cycle_path(project: Project, outgoing: Dict[int, Set[int]]) -> List[int]:
    """
    DFS to extract one cycle path (track ids). Returns something like [a, b, c, a].
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[int, int] = {tid: WHITE for tid in project.tracks.keys()}
    parent: Dict[int, Optional[int]] = {tid: None for tid in project.tracks.keys()}

    def dfs(u: int) -> Optional[List[int]]:
        color[u] = GRAY
        for v in outgoing[u]:
            if color[v] == WHITE:
                parent[v] = u
                res = dfs(v)
                if res is not None:
                    return res
            elif color[v] == GRAY:
                # Found a back-edge u -> v, extract cycle v..u..v
                return _extract_cycle(parent, start=v, end=u)
        color[u] = BLACK
        return None

    for tid in project.tracks.keys():
        if color[tid] == WHITE:
            res = dfs(tid)
            if res is not None:
                return res

    # Fallback (shouldn't happen if called only when a cycle exists)
    return []

def _extract_cycle(parent: Dict[int, Optional[int]], start: int, end: int) -> List[int]:
    """
    Given a back-edge end -> start where start is in the current stack,
    reconstruct cycle [start, ..., end, start].
    """
    path = [start]
    cur = end
    while cur != start and cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.append(start)
    path.reverse()
    return path

class PortValidationError(ValueError):
    pass

@dataclass
class PortValidationReport:
    missing_inputs: Dict[int, List[str]]
    missing_outputs: Dict[int, List[str]]
    created_inputs: Dict[int, List[str]]
    created_outputs: Dict[int, List[str]]

def validate_ports(project: Project, *, strict: bool = True) -> PortValidationReport:
    """
    Checks that every connection references existing ports on src/dst tracks.

    strict=True  -> raise PortValidationError if anything is missing.
    strict=False -> auto-create missing ports by calling ensure_input_port/ensure_output_port.
                   (This does NOT guarantee the track's DSP graph actually produces meaningful audio;
                    it just ensures the wiring is consistent.)
    """
    missing_in: Dict[int, List[str]] = {}
    missing_out: Dict[int, List[str]] = {}
    created_in: Dict[int, List[str]] = {}
    created_out: Dict[int, List[str]] = {}

    def ensure_graph(tid: int) -> PortGraph:
        t = project.tracks[tid]
        if t.graph is None:
            # attach a placeholder graph so we have somewhere to create ports
            t.graph = EmptyPortGraph()
        return t.graph  # type: ignore[return-value]

    # First pass: collect missing ports
    for c in project.connections.values():
        src = c.src
        dst = c.dst
        src_port = c.src_port
        dst_port = c.dst_port

        sg = ensure_graph(src)
        dg = ensure_graph(dst)

        outs = sg.output_ports()
        ins = dg.input_ports()

        if src_port not in outs:
            missing_out.setdefault(src, []).append(src_port)
        if dst_port not in ins:
            missing_in.setdefault(dst, []).append(dst_port)

    # Deduplicate lists while preserving stable order
    def dedupe(d: Dict[int, List[str]]) -> Dict[int, List[str]]:
        out: Dict[int, List[str]] = {}
        for tid, ports in d.items():
            seen = set()
            uniq = []
            for p in ports:
                if p not in seen:
                    seen.add(p)
                    uniq.append(p)
            out[tid] = uniq
        return out

    missing_out = dedupe(missing_out)
    missing_in = dedupe(missing_in)

    if strict:
        if missing_in or missing_out:
            lines: List[str] = ["Port validation failed. Missing ports referenced by connections:"]
            if missing_out:
                lines.append("  Missing OUTPUT ports:")
                for tid, ports in sorted(missing_out.items()):
                    tname = project.tracks[tid].name
                    lines.append(f"    - {tname}(id={tid}): {ports}")
            if missing_in:
                lines.append("  Missing INPUT ports:")
                for tid, ports in sorted(missing_in.items()):
                    tname = project.tracks[tid].name
                    lines.append(f"    - {tname}(id={tid}): {ports}")

            lines.append("Fix options:")
            lines.append("  - Add those ports to the track's internal graph, OR")
            lines.append("  - Change the connections, OR")
            lines.append("  - Run validate_ports(strict=False) to auto-create placeholder ports.")
            raise PortValidationError("\n".join(lines))

# Non-strict: auto-create missing ports
    for tid, ports in missing_out.items():
        g = ensure_graph(tid)
        for p in ports:
            g.ensure_output_port(p)
        created_out[tid] = list(ports)

    for tid, ports in missing_in.items():
        g = ensure_graph(tid)
        for p in ports:
            g.ensure_input_port(p)
        created_in[tid] = list(ports)

    return PortValidationReport(
        missing_inputs=missing_in,
        missing_outputs=missing_out,
        created_inputs=created_in,
        created_outputs=created_out,
    )

@dataclass
class CompiledProject:
    audio: Optional[CompiledAudioOrder] = None
    port_report: Optional[PortValidationReport] = None
    ports_frozen: bool = False

@dataclass
class TrackIR:
    node_uids: list[int]
    plugin_specs: dict[int, PluginSpec]          # node_uid -> plugin descriptor
    internal_audio_edges: list[tuple[int, int, int, int]]
    # (src_plugin_key, src_channel, dst_plugin_key, dst_channel)         # edges already resolved to node_uid+channel
    published_in: dict[str, list[Endpoint]]      # allow multi-channel bundles
    published_out: dict[str, list[Endpoint]]

"""
Example: if the track’s output port "out" corresponds to the last effect’s "audio.out:0", then:

published_out["out"] = (last_fx_node_id, "audio.out:0")
published_in["in"] = (first_fx_node_id, "audio.in:0")
"""

@dataclass(frozen=True)
class Endpoint:
    node_uid: int              # stable node UID you assign (not JUCE NodeID)
    kind: str                  # "audio" or "midi"
    channel: int = 0           # audio channel index; ignored for midi

@dataclass(frozen=True)
class PluginSpec:
    uid: int        # VST3 UID or your identifier
    key: int        # instance key (unique per instance)
    kind: str       # "instrument" | "effect" | "utility_gain" | "utility_mixer"

@dataclass(frozen=True)
class AudioEdge:
    src: Endpoint
    dst: Endpoint
    gain_db: float | None

def sample_to_block(sample: int, block_size: int) -> int:
    return sample // block_size  # floor

UID_GAIN = 0x4741494E  # "GAIN" placeholder; pick your real builtin uid

@dataclass(frozen=True)
class PluginSpec:
    uid: int
    key: int
    kind: str  # "instrument" | "effect" | "utility_gain" | ...

@dataclass
class ServerCommandPlan:
    plugins: Dict[int, PluginSpec]  # plugin_key -> spec
    audio_connections: List[Tuple[int, int, int, int]]         # (srcKey,srcCh,dstKey,dstCh)
    param_changes: List[Tuple[int, int, float, int]]           # (pluginKey,paramIndex,value,atSample)
    midi_notes: List[Tuple[int, int, int, int, int, int]]      # (midiIndex,note,vel,start,dur,ch)
    midi_ccs: List[Tuple[int, int, int, int, int]]             # (pluginKey,cc,value,time,ch)

# TrackIR expected shape:
#   ir.plugin_specs: dict[plugin_key, PluginSpec]
#   ir.internal_audio_edges: list[(src_key,src_ch,dst_key,dst_ch)]
#   ir.published_out: dict[str, list[(plugin_key,ch)]]
#   ir.published_in:  dict[str, list[(plugin_key,ch)]]

def compile_to_server_plan(project, track_irs, *, UID_GAIN: int, remap_gain_channels: bool = True,
                           sample_rate: int = 44100, block_size: int = 64, total_samples: int = 0) -> ServerCommandPlan:
    plugins: Dict[int, PluginSpec] = {}
    final_edges: List[Tuple[int, int, int, int]] = []
    gain_param_inits: List[Tuple[int, int, float, int]] = []

    # A) collect per-track plugin specs + internal wiring
    for tid, ir in track_irs.items():
        plugins.update(ir.plugin_specs)
        final_edges.extend(ir.internal_audio_edges)  # add ONCE

    # B) resolve project connections to per-channel edges WITH conn_id
    # project_audio_edges: list[(conn_id, src_key, src_ch, dst_key, dst_ch, gain_db)]
    project_audio_edges: List[Tuple[int, int, int, int, int, Optional[float]]] = []

    for conn_id, conn in project.connections.items():  # use dict key as conn_id if conn has no id
        src_bundle = track_irs[conn.src].published_out[conn.src_port]
        dst_bundle = track_irs[conn.dst].published_in[conn.dst_port]
        gain_db = getattr(conn, "gain_db", None)

        if len(src_bundle) != len(dst_bundle):
            raise ValueError(
                f"Bundle size mismatch: "
                f"{project.tracks[conn.src].name}.{conn.src_port} has {len(src_bundle)} chans, "
                f"{project.tracks[conn.dst].name}.{conn.dst_port} has {len(dst_bundle)} chans"
            )

        for (src_key, src_ch), (dst_key, dst_ch) in zip(src_bundle, dst_bundle):
            project_audio_edges.append((conn_id, src_key, src_ch, dst_key, dst_ch, gain_db))

    # C) group by conn_id and insert ONE gain node per connection if needed
    by_conn: Dict[int, List[Tuple[int, int, int, int, Optional[float]]]] = defaultdict(list)
    for (conn_id, src_key, src_ch, dst_key, dst_ch, gain_db) in project_audio_edges:
        by_conn[conn_id].append((src_key, src_ch, dst_key, dst_ch, gain_db))

    for conn_id, edges in by_conn.items():
        gain_db = edges[0][4]
        if any(e[4] != gain_db for e in edges):
            raise ValueError(f"Inconsistent gain_db within connection {conn_id}")

        if gain_db is None:
            # direct wires
            for (src_key, src_ch, dst_key, dst_ch, _) in edges:
                final_edges.append((src_key, src_ch, dst_key, dst_ch))
            continue

        # Create one gain node
        gain_key = project.allocate_plugin_key()
        plugins[gain_key] = PluginSpec(uid=UID_GAIN, key=gain_key, kind="utility_gain")

        if remap_gain_channels:
            # map whatever src channels are used onto 0..N-1 on the gain node
            channels = sorted({src_ch for (_, src_ch, _, _, _) in edges})
            ch_map = {ch: i for i, ch in enumerate(channels)}
        else:
            ch_map = None

        for (src_key, src_ch, dst_key, dst_ch, _) in edges:
            gch = ch_map[src_ch] if ch_map is not None else src_ch
            final_edges.append((src_key, src_ch, gain_key, gch))
            final_edges.append((gain_key, gch, dst_key, dst_ch))

        linear = 10 ** (float(gain_db) / 20.0)
        gain_param_inits.append((gain_key, 0, linear, 0))  # param 0 at block 0

    # D) schedule the rest of events elsewhere and merge with gain init params
    midi_notes, midi_ccs, param_changes = schedule_everything_else(
        project, track_irs,
        sample_rate=sample_rate,
        block_size=block_size,
        total_samples=total_samples,
    )
    param_changes = gain_param_inits + param_changes

    return ServerCommandPlan(
        plugins=plugins,
        audio_connections=final_edges,
        param_changes=param_changes,
        midi_notes=midi_notes,
        midi_ccs=midi_ccs,
    )

def send_plan_to_server(server, plan):
    """Send a ServerCommandPlan to the JUCE server.

    Args:
        server: JuceAudioClient instance
        plan: ServerCommandPlan with plugins, audio_connections, midi_notes, midi_ccs, param_changes
    """
    plugins = plan.plugins
    final_edges = plan.audio_connections
    midi_notes = plan.midi_notes
    midi_ccs = plan.midi_ccs
    param_changes = plan.param_changes

    server.clearallplugins()

    # load plugins, capture plugin_key -> pluginId (server)
    key_to_id = {}
    for key in sorted(plugins.keys()):
        spec = plugins[key]
        proc = server.loadpluginbyuid(spec.uid, spec.key)
        key_to_id[key] = proc.key

    # connect audio
    for (src_key, src_ch, dst_key, dst_ch) in final_edges:
        server.connectaudio(key_to_id[src_key], src_ch, key_to_id[dst_key], dst_ch)

    # clear schedules
    server.clearmidischedule()
    server.clearmidiccschedule()
    server.clearparamschedule()

    # params (sample-based)
    for (plugin_key, param_idx, value, at_sample) in param_changes:
        server.scheduleparamchange(key_to_id[plugin_key], param_idx, value, at_sample)

    # midi cc
    for (plugin_key, cc, value, t, ch) in midi_ccs:
        server.schedulemidicc(key_to_id[plugin_key], cc, value, t, ch)

    # midi notes
    server.schedulemidinotes(midi_notes)

# todo:
#    load audio files into player nodes
#    schedule regions

def insert_gain_processors(project, UID_GAIN):
    """
    Returns:
        processors: list[Processor]
        connections: list[ProcessorConnection]
        gain_param_inits: list[(processorKey, paramIndex, value, atSample)]
    """

    processors = set()  # collect all processors involved
    for conn in project.processor_connections:
        processors.add(conn.processor1)
        processors.add(conn.processor2)

    # Group connections by group_id
    by_group = defaultdict(list)
    for conn in project.processor_connections:
        by_group[conn.group_id].append(conn)

    new_connections = []
    gain_param_inits = []

    for group_id, conns in by_group.items():

        gain_db = conns[0].gain_db
        if any(c.gain_db != gain_db for c in conns):
            raise ValueError(f"Inconsistent gain_db inside connection group {group_id}")

        if gain_db is None:
            # direct connections
            new_connections.extend(conns)
            continue

        # Create ONE gain processor
        gain_key = project.allocate_plugin_key()
        gain_proc = Processor(uid=UID_GAIN, key=gain_key, name="Gain")
        processors.add(gain_proc)

        # Optional: remap channel numbers to 0..N-1
        src_channels = sorted({c.channel1 for c in conns})
        ch_map = {ch: i for i, ch in enumerate(src_channels)}

        for c in conns:
            gch = ch_map[c.channel1]

            # src -> gain
            new_connections.append(
                ProcessorConnection(
                    c.processor1,
                    c.channel1,
                    gain_proc,
                    gch,
                    gain_db=None,
                    group_id=None
                )
            )

            # gain -> dst
            new_connections.append(
                ProcessorConnection(
                    gain_proc,
                    gch,
                    c.processor2,
                    c.channel2,
                    gain_db=None,
                    group_id=None
                )
            )

        linear = 10 ** (float(gain_db) / 20.0)
        gain_param_inits.append((gain_key, 0, linear, 0))  # param 0 at block 0

    return list(processors), new_connections, gain_param_inits

def insert_mixers_for_fanin(project, connections, processors, *, UID_MIXER: int):
    """
    Insert mixer nodes where multiple connections feed the same (dst_processor, dst_channel).

    Args:
        connections: list[ProcessorConnection] (already has gain nodes inserted if you want)
        processors:  set[Processor] (all processors currently in graph)
        UID_MIXER:   builtin uid for mixer processor

    Returns:
        new_processors: set[Processor] (including new mixers)
        new_connections: list[ProcessorConnection] (rewritten with mixers)
    """

    # Group incoming edges by destination endpoint
    incoming = defaultdict(list)  # (dst_proc_key, dst_ch) -> [ProcessorConnection,...]
    # connections we won't touch (fan-in==1 or non-audio types later)

    for c in connections:
        incoming[(c.processor2.key, c.channel2)].append(c)

    new_connections = []
    new_processors = set(processors)

    for (dst_key, dst_ch), conns in incoming.items():
        if len(conns) <= 1:
            # No fan-in; keep as-is
            new_connections.extend(conns)
            continue

        # Fan-in detected: create a mixer node for this destination input channel
        mix_key = project.allocate_plugin_key()
        mix_proc = Processor(uid=UID_MIXER, key=mix_key, name=f"Mixer_to_{dst_key}_ch{dst_ch}")
        new_processors.add(mix_proc)

        # All sources now go into mixer input channel 0 (or dst_ch; your choice)
        # We'll use channel 0 on mixer for simplicity
        mix_in_ch = 0
        mix_out_ch = 0

        # Connect each source -> mixer
        for c in conns:
            new_connections.append(
                ProcessorConnection(
                    c.processor1, c.channel1,
                    mix_proc, mix_in_ch,
                    gain_db=None,
                    group_id=None
                )
            )

        # Connect mixer -> original destination
        # Use first conn's dst processor object (all conns share same destination)
        dst_proc = conns[0].processor2
        new_connections.append(
            ProcessorConnection(
                mix_proc, mix_out_ch,
                dst_proc, dst_ch,
                gain_db=None,
                group_id=None
            )
        )

    return new_processors, new_connections

def compile_graph(project, UID_GAIN: int, UID_MIXER: int):
    # Step 1: gain insertion
    processors, conns_after_gain, gain_param_inits = insert_gain_processors(project, UID_GAIN)

    # Step 2: fan-in mixer insertion
    processors2, conns_after_mix = insert_mixers_for_fanin(
        project,
        conns_after_gain,
        set(processors),
        UID_MIXER=UID_MIXER
    )

    return processors2, conns_after_mix, gain_param_inits

def coalesce_param_changes(changes: list[tuple[int,int,float,int]], *, epsilon: float = 0.0) -> list[tuple[int,int,float,int]]:
  """
  Coalesce param changes for efficient sending:
    - If multiple changes target the same (pluginKey,paramIndex) at the same atSample, keep only the last.
    - If consecutive changes for the same (pluginKey,paramIndex) have ~the same value (within epsilon), drop repeats.
  Expects/returns tuples: (pluginKey, paramIndex, value_float, atSample).
  """
  if not changes:
    return []
  changes_sorted = sorted(changes, key=lambda x: (x[3], x[0], x[1]))
  last_by_key_sample: dict[tuple[int,int,int], tuple[int,int,float,int]] = {}
  for c in changes_sorted:
    last_by_key_sample[(c[0], c[1], c[3])] = c
  uniq = sorted(last_by_key_sample.values(), key=lambda x: (x[3], x[0], x[1]))
  out: list[tuple[int,int,float,int]] = []
  last_val: dict[tuple[int,int], float] = {}
  for pluginKey, paramIndex, value, atSample in uniq:
    key = (pluginKey, paramIndex)
    prev = last_val.get(key)
    if prev is not None and abs(float(value) - prev) <= epsilon:
      continue
    last_val[key] = float(value)
    out.append((int(pluginKey), int(paramIndex), float(value), int(atSample)))
  return out


# ---- convenience type aliases ----
MidiNote = Tuple[int, int, int, int, int, int]   # (midiIndex, note, vel, start_sample, dur_samples, channel)
MidiCC   = Tuple[int, int, int, int, int]        # (pluginKey, controller, value, time_sample, channel)
ParamChange = Tuple[int, int, float, int]        # (pluginKey, paramIndex, value_float, atSample)

# ---- helper: sample a Signal into control-block param changes ----
def sample_signal_to_param_changes(
    *,
    plugin_key: int,
    param_index: int,
    signal,                 # Signal instance with .at(sample, ctx, cache)
    sample_rate: int,
    block_size: int,
    total_samples: int,
    eval_ctx,               # EvalContext(sample_rate, block_size, beat_at_sample maybe)
    cache=None,
    epsilon: float = 0.0,   # minimum delta to emit change (0.0 => emit every block when changed)
) -> List[ParamChange]:
    """
    Sample `signal` at control blocks and return list of (pluginKey, paramIndex, value, atSample).
    - uses block midpoint for sampling (b*block + block//2)
    - total_samples controls how many blocks (ceil)
    - epsilon can reduce spam by only emitting when the value changed by >= epsilon
    """
    if total_samples <= 0:
        return []

    num_blocks = int(ceil(total_samples / block_size))
    out: List[ParamChange] = []

    last_val: Optional[float] = None

    for b in range(num_blocks):
        sample_mid = b * block_size + (block_size // 2)
        if sample_mid >= total_samples:
            # clamp to last valid sample if last partial block extends beyond end
            sample_mid = max(0, total_samples - 1)

        v = float(signal.at(sample_mid, eval_ctx, cache))

        if last_val is None:
            out.append((plugin_key, param_index, v, b * block_size))
            last_val = v
            continue

        if epsilon <= 0.0:
            # exact change detection
            if v != last_val:
                out.append((plugin_key, param_index, v, b * block_size))
                last_val = v
        else:
            if abs(v - last_val) >= epsilon:
                out.append((plugin_key, param_index, v, b * block_size))
                last_val = v

    return out

# ---- the main function ----

def iter_project_midi_note_events(project, track_irs) -> Iterable[Tuple[int,int,int,int,int,int]]:
    """
    Yield (midiIndex, note, vel, start_sample, dur_samples, channel)
    from every Track's midiSchedules.

    Each MidiSchedule is a list of (pluginId, note, velocity, offset, duration, channel) tuples.
    """
    tracks = project.tracks
    if isinstance(tracks, dict):
        track_iter = tracks.values()
    else:
        track_iter = tracks

    for track in track_iter:
        if not getattr(track, 'midiSchedules', None):
            continue
        for sched in track.midiSchedules:
            for ev in sched:
                # ev is (pluginId, note, velocity, sampleOffset, duration, channel)
                yield ev

def iter_project_midi_cc_events(project, track_irs) -> Iterable[Tuple[int,int,int,int,int]]:
    """
    Yield (pluginKey, controller, value, time_sample, channel)
    from every Track's midiCcSchedules.
    """
    tracks = project.tracks
    if isinstance(tracks, dict):
        track_iter = tracks.values()
    else:
        track_iter = tracks

    for track in track_iter:
        if not getattr(track, 'midiCcSchedules', None):
            continue
        for sched in track.midiCcSchedules:
            for ev in sched:
                # ev is (pluginKey, controller, value, time_sample, channel)
                yield ev

def iter_project_param_signal_lanes(project, track_irs) -> Iterable[Tuple[int,int,Any,float]]:
    """
    Yield (plugin_key, param_index, signal, epsilon)
    from every Track's paramSignalLanes.

    Each ParamSignalLane has plugin_key, param_index, signal (.at(sample, ctx, cache)), epsilon.
    """
    tracks = project.tracks
    if isinstance(tracks, dict):
        track_iter = tracks.values()
    else:
        track_iter = tracks

    for track in track_iter:
        if not getattr(track, 'paramSignalLanes', None):
            continue
        for lane in track.paramSignalLanes:
            yield (lane.plugin_key, lane.param_index, lane.signal, lane.epsilon)

def schedule_everything_else(
    project,
    track_irs,
    *,
    sample_rate: int,
    block_size: int,
    total_samples: int,
    eval_ctx_factory=None,
    cache_factory=None,
) -> Tuple[List[MidiNote], List[MidiCC], List[ParamChange]]:
    """
    Convert project/track IR into:
      (midi_notes, midi_ccs, param_changes)

    - midi notes and midi CCs are taken from project via adapter iterators (see below).
    - param_changes are produced by sampling Signal objects at control-block rate.
    - This function DOES NOT turn MIDI CCs into param changes — those mappings are left for
      your realtime MIDI->param mapper using MidiToParamConnection.

    Required adapters (you must provide in your project or pass in globals):
      - iter_project_midi_note_events(project, track_irs) -> Iterable[MidiNote]
      - iter_project_midi_cc_events(project, track_irs) -> Iterable[MidiCC]
      - iter_project_param_signal_lanes(project, track_irs) -> Iterable[(plugin_key, param_index, signal, epsilon)]
        where `signal` implements `.at(sample, ctx, cache)`

    Parameters:
      - sample_rate, block_size, total_samples: scheduling/render parameters
      - eval_ctx_factory(optional): callable(sample_rate, block_size) -> EvalContext
      - cache_factory(optional): callable() -> ControlCache

    Returns:
      (midi_notes, midi_ccs, param_changes)
    """

    # ---- defaults for eval/context/caching ----
    if eval_ctx_factory is None:
        def eval_ctx_factory(sr, bs):
            # project may provide beat_at_sample; if so, pass it into ctx
            beat_fn = getattr(project, "beat_at_sample", None)
            return EvalContext(sample_rate=sr, block_size=bs, beat_at_sample=beat_fn)

    if cache_factory is None:
        def cache_factory():
            return ControlCache()

    eval_ctx = eval_ctx_factory(sample_rate, block_size)
    cache = cache_factory()

    # ---- 1) MIDI notes ----
    # Adapter: project must expose an iterator yielding MidiNote tuples
    midi_notes: List[MidiNote] = []
    for ev in iter_project_midi_note_events(project, track_irs):
        # Expect the iterator to yield (midiIndex, note, vel, start_sample, dur_samples, channel)
        midi_notes.append(ev)

    # ---- 2) MIDI CCs ----
    midi_ccs: List[MidiCC] = []
    for ev in iter_project_midi_cc_events(project, track_irs):
        # Expect (pluginKey, controller, value, time_sample, channel)
        midi_ccs.append(ev)

    # ---- 3) Parameter automation: sample signals per block ----
    param_changes: List[ParamChange] = []

    # Adapter: should yield tuples (plugin_key, param_index, signal, epsilon)
    # where epsilon is optional and controls change threshold (float), default 0.0
    for plugin_key, param_index, signal, *maybe_epsilon in iter_project_param_signal_lanes(project, track_irs):
        epsilon = float(maybe_epsilon[0]) if maybe_epsilon else 0.0
        lanes = sample_signal_to_param_changes(
            plugin_key=plugin_key,
            param_index=param_index,
            signal=signal,
            sample_rate=sample_rate,
            block_size=block_size,
            total_samples=total_samples,
            eval_ctx=eval_ctx,
            cache=cache,
            epsilon=epsilon,
        )
        param_changes.extend(lanes)

    # Sort outputs deterministically (optional but useful)
    midi_notes.sort(key=lambda x: (x[3], x[0], x[1]))   # start_sample, midiIndex, pitch
    midi_ccs.sort(key=lambda x: (x[3], x[0], x[1]))     # time_sample, pluginKey, controller
    param_changes.sort(key=lambda x: (x[3], x[0], x[1]))# atSample, pluginKey, paramIndex

    return midi_notes, midi_ccs, param_changes

class Track: # input node = "input", output node = "output", that way we can put tracks into a graph
             # if there are no effects, the notes graph ends with "output", because the output of the track is the notes.
             # if there are effects, the notes graph ends with "input", because that's the first effect in the effect graph.
             # but what if we want another track to output notes to a track's notes graph? then that won't work.
             # also, some tracks will have effects and no notes or instruments..
             # how about "notes in", "audio in", "notes out" and "audio out"?
  def __init__(self, id, name, parent_id=None, children=None, graph=None, audioTracks=None, midiSchedules=None, notes=None, notesGraph=None, processorGraph=None, midiCcSchedules=None,
               paramSchedules=None, paramSignalLanes=None, sampleOffset=None, timeOffset=None, beatOffset=None):
    assert ((sampleOffset is not None) + (timeOffset is not None) + (beatOffset is not None)) <= 1
    self.id = id
    self.name = name
    self.parent_id = parent_id
    self.children = children or []
    self.graph = graph or EmptyPortGraph()
    self.audioTracks = audioTracks
    self.midiSchedules = midiSchedules
    self.notes = notes
    self.notesGraph=notesGraph
    self.midiCcSchedules = midiCcSchedules
    self.paramSchedules = paramSchedules
    self.paramSignalLanes: List[ParamSignalLane] = paramSignalLanes or []
    self.processorGraph = processorGraph
    self.timeOffset = timeOffset
    self.beatOffset = beatOffset
    self.sampleOffset = sampleOffset
  def add(self, item):
    if type(item) is ParamSchedule:
      self.paramSchedule.extend(item)
    elif type(item) is MidiSchedule:
      self.midiSchedule.extend(item)
    elif type(item) is MidiCcSchedule:
      self.midiCcSchedule.extend(item)
    elif type(item) is Notes:
      self.notes.extend(item)
    elif type(item) is AudioTrack:
      self.audioTracks.append(item)
    elif type(item) is Processors:
      self.processors.extend(item)
    elif type(item) is Processor:
      self.processors.append(item)
  def remove(self, item):
    if type(item) is ParamSchedule:
      for item2 in item:
        self.paramSchedule.remove(item2)
    elif type(item) is MidiSchedule:
      for item2 in item:
        self.midiSchedule.remove(item2)
    elif type(item) is MidiCcSchedule:
      for item2 in item:
        self.midiCcSchedule.remove(item2)
    elif type(item) is Notes:
      for item2 in item:
        self.notes.remove(item2)
    elif type(item) is AudioTrack:
      self.audioTracks.remove(item)
    elif type(item) is Processors:
      for item2 in item:
        self.processors.remove(item2)
    elif type(item) is Processor:
      self.processors.remove(item)
  def automate_param(self, plugin_key, param_index, signal, *, epsilon=0.0):
    """Attach a Signal to a plugin parameter for automation.

    Usage:
      lfo = TimeFn(lambda t: 0.5 + 0.5 * math.sin(2 * math.pi * t))
      track.automate_param(synth.key, 3, lfo, epsilon=0.001)
    """
    self.paramSignalLanes.append(
      ParamSignalLane(plugin_key=plugin_key, param_index=param_index,
                      signal=signal, epsilon=epsilon)
    )

class MidiSchedule(list):
  def __init__(self, midischedule=None, pluginId=None, channel=None, *, sample_rate=None, bpm=None, tempo_map=None):
    self.pluginId = pluginId
    self.channel = channel
    if isinstance(midischedule, Notes):
      assert self.pluginId is not None and self.channel is not None

      # beat→sample conversion: prefer tempo_map, fall back to constant bpm
      if tempo_map is not None:
        def _beat_to_sample(beat):
          return tempo_map.beat_to_sample(beat)
      elif bpm is not None and sample_rate is not None:
        def _beat_to_sample(beat):
          return int(sample_rate * 60 * beat / bpm)
      else:
        _beat_to_sample = None

      if midischedule.sampleOffset is not None:
        sampleOffset = int(midischedule.sampleOffset)
      elif midischedule.timeOffset is not None and sample_rate is not None:
        sampleOffset = int(midischedule.timeOffset * sample_rate)
      elif midischedule.beatNumber is not None and _beat_to_sample is not None:
        sampleOffset = _beat_to_sample(midischedule.beatNumber)
      else:
        sampleOffset = 0

      loffset = sampleOffset
      for note in midischedule:
        if note.sampleOffset:
          noteoffset = int(note.sampleOffset) + sampleOffset
        elif note.timeOffset and sample_rate is not None:
          noteoffset = int(note.timeOffset * sample_rate) + sampleOffset
        elif note.beatInterval is not None and _beat_to_sample is not None:
          noteoffset = loffset + _beat_to_sample(note.beatInterval)
        elif note.beatNumber is not None and _beat_to_sample is not None:
          noteoffset = _beat_to_sample(note.beatNumber) + sampleOffset
        elif note.sampleInterval:
          noteoffset = loffset + int(note.sampleInterval)
        elif note.timeInterval and sample_rate is not None:
          noteoffset = loffset + int(note.timeInterval * sample_rate)
        else:
          noteoffset = loffset

        # beat duration → sample duration
        if note.beatDuration is not None and _beat_to_sample is not None:
          dur = _beat_to_sample(note.beatDuration)
        elif note.sampleDuration is not None:
          dur = int(note.sampleDuration)
        elif note.timeDuration is not None and sample_rate is not None:
          dur = int(note.timeDuration * sample_rate)
        else:
          dur = _beat_to_sample(note.beatDuration) if (note.beatDuration is not None and _beat_to_sample) else 0

        self.append((self.pluginId, note.midi, note.velocity, noteoffset, dur, self.channel))
        loffset = noteoffset
    elif isinstance(midischedule, list):
      super().__init__(midischedule)

class Processor:
  def __init__(self, uid=None, key=None, pluginInfo=None, name=None):
    self.pluginInfo = pluginInfo
    self.uid = uid
    self.key = key
    self.name = name

class ProcessorConnection:
  def __init__(self, p1, ch1, p2, ch2, *, gain_db=None, group_id=None):
    self.processor1 = p1
    self.channel1 = ch1
    self.processor2 = p2
    self.channel2 = ch2
    self.gain_db = gain_db
    self.group_id = group_id

class ProcessorConnections(list): #should we do it this way, or sohuld each processor have a list of things it's connected to?
  def add(self, processorConnection):
    self.append(processorConnection)
  def remove(self, processorConnection):
    try: 
      self.remove(processorConnection)
    except ValueError:
      for x in self[:]:
        if x.processor1==processorConnection.processor1 and x.channel1==processorConnection.channel1 and x.processor2==processorConnection.processor2 and x.channel2==processorConnection.channel2:
          super().remove(x)

class MidiToParamConnection:
  def __init__(self, controller, channel, processorKey, param):
    self.controller = controller
    self.channel = channel
    self.processorKey = processorKey
    self.param = param

class MidiToParamConnections(list):
  def add(self, item):
    self.append(item)
  def remove(self, item):
    try:
      self.remove(item)
    except ValueError:
      for x in self[:]:
        if x.controller == item.controller and x.channel == item.channel and x.pluginKey == item.pluginKey and x.param == item.param:
          super().remove(x)

class Processors(dict):
  def __init__(self, *args):
    super().__init__(*args)

class MidiCcSchedule(list):
  def __init__(self, midischedule=None):
    super().__init__(midischedule)

@dataclass
class ParamSignalLane:
  """Attach a Signal to a plugin parameter for automation."""
  plugin_key: int
  param_index: int
  signal: Any  # Signal instance with .at(sample, ctx, cache)
  epsilon: float = 0.0  # minimum delta to emit change

# todo: detect cyclic graphs
# todo: support comping/slip editing - soundshop_misc\comp lanes.txt
# todo: apparently, we need midi graphs for manipulating notes, not just in-place changes. https://chatgpt.com/share/69a44037-0cd4-8011-a96a-39e082f55e1a
class Song:
  def __init__(self, filePath=None, client=None, tracks=None, auto_declare_ports: bool = True):
    if tracks is None:
      tracks = []
    self.filePath = filePath
    self.client = client
    self.tracks = tracks
    self.trackConnections = []
    self.auto_declare_ports = auto_declare_ports
    self._next_track_id = 1
    self._next_conn_id = 1
    self.tracks: Dict[TrackId, Track] = {}
    self.track_order: List[TrackId] = []
    self.connections: Dict[int, Connection] = {}
    self._out_index: Dict[Tuple[TrackId, Port], Set[int]] = {}
    self._in_index: Dict[Tuple[TrackId, Port], Set[int]] = {}

  def __init__(self, *, auto_declare_ports: bool = True):
    self.auto_declare_ports = auto_declare_ports

    self._next_track_id = 1
    self._next_conn_id = 1

    self.tracks: Dict[TrackId, Track] = {}
    self.track_order: List[TrackId] = []

    self.connections: Dict[int, Connection] = {}
    self._out_index: Dict[Tuple[TrackId, Port], Set[int]] = {}
    self._in_index: Dict[Tuple[TrackId, Port], Set[int]] = {}

  def compile(
          self,
          *,
          strict_ports: bool = True,
          detect_cycles: bool = True,
          freeze_ports: bool = True,
          auto_create_missing_ports: bool = False,
  ) -> CompiledProject:
    """
    Compile project routing for rendering.

    Args:
      strict_ports:
          If True, missing ports referenced by connections raise PortValidationError.
      detect_cycles:
          If True, detect audio routing cycles and raise AudioGraphCycleError.
      freeze_ports:
          If True, disable auto port declaration before compiling (recommended).
      auto_create_missing_ports:
          If True, missing ports will be created during compile (equivalent to strict_ports=False
          for port creation), and port_report will record what got created.

    Typical safe usage:
      compile(strict_ports=True, freeze_ports=True, detect_cycles=True)

    Typical prototyping usage:
      compile(strict_ports=False, freeze_ports=False, auto_create_missing_ports=True)
    """
    was_auto = self.auto_declare_ports

    ports_frozen = False
    if freeze_ports:
      self.auto_declare_ports = False
      ports_frozen = True

    # 1) Validate ports (and optionally auto-create them)
    if auto_create_missing_ports:
      # This explicitly creates missing ports regardless of auto_declare_ports.
      port_report = validate_ports(self, strict=False)
      if strict_ports:
        # If they asked strict_ports but also auto_create_missing_ports, treat
        # strict_ports as "after creation, ensure nothing missing".
        validate_ports(self, strict=True)
        # if strict=True passes, it won't raise; we keep the created report
      else:
        # Non-strict: we're done
        pass
    else:
      # No auto-creation: just validate according to strict_ports
      port_report = validate_ports(self, strict=strict_ports)

    # 2) Compile audio routing (topo order + optional cycle error)
    from ..tempo.tempo_map import compile_project_to_render_plan
    audio = compile_project_to_render_plan(self)

    # NOTE: if you want to compile MIDI/control graphs too, add them here.

    # Leave ports frozen if freeze_ports=True (intended)
    # Restore if not freezing
    if not freeze_ports:
      self.auto_declare_ports = was_auto

    return CompiledProject(audio=audio, port_report=port_report, ports_frozen=ports_frozen)

  def add_track(
          self,
          name: str,
          *,
          parent: Optional[Union[Track, TrackId]] = None,
          auto_sum: bool = True,
          child_out_port: str = "out",
          parent_in_port: str = "in",
  ) -> Track:
    tid = self._next_track_id
    self._next_track_id += 1

    parent_id = parent.id if isinstance(parent, Track) else parent
    t = Track(id=tid, name=name, parent_id=parent_id)

    self.tracks[tid] = t
    self.track_order.append(tid)

    if parent_id is not None:
      if parent_id not in self.tracks:
        raise ValueError(f"Unknown parent track id {parent_id}")
      self.tracks[parent_id].children.append(tid)

      if auto_sum:
        # This will obey auto_declare_ports
        self.connect(tid, child_out_port, parent_id, parent_in_port, auto=True)

    return t

  def addtrackconnection(self, track1, track2):
    self.trackConnections.append((track1, track2))
  def send(self):
    self.client.clearparamschedule()
    for paramschedule in self.paramSchedules:
      self.client.scheduleparamchanges(paramschedule)
    self.client.clearmidiccschedule()
    for midiccschedule in self.midiCcSchedules:
      self.client.schedulemidiccs(self.midiccschedule)
    self.client.clearmidischedule()
    for midischedule in self.midiSchedules:
      self.client.schedulemidinotes(midischedule)
    self.client.clearorderednotes()
    self.client.scheduleorderednotes(self.orderednotesschedule)
    self.client.clearallplugins()
    if self.availablePlugins is None:
      self.scanplugins()
    for processor in self.processors:
      for index, availablePlugin in enumerate(self.client.availablePlugins):
        if availablePlugin.pluginInfo.uid==processor.pluginInfo.uid:
          self.client.loadpluginbyindex(index)
  def routecctoparam(self, pluginId, param_index, cc_controller, midi_channel=-1):
    """Map a MIDI CC controller to a plugin parameter

    Args:
      pluginId: Plugin ID
      param_index: Parameter index
      cc_controller: MIDI CC controller number (0-127)
      midi_channel: MIDI channel (1-16) or -1 for any channel
    """
    self.sendcmd(send_cmd.route_cc_to_param)
    self.sendinfo("IIIi", pluginId, param_index, cc_controller, midi_channel)
    self.commands_pipe_handle.flush()
    return self.readinfo1c("I")
  def unroutecctoparam(self, pluginId, param_index, cc_controller):
    """Remove a MIDI CC to parameter mapping"""
    self.sendcmd(send_cmd.unroute_cc_to_param)
    self.sendinfo("III", pluginId, param_index, cc_controller)
    self.commands_pipe_handle.flush()
    return self.readinfo1c("I")
  def save(self, filePath=None):
    filePath = filePath or self.filePath or "song"
    if os.path.exists(filePath):
      x = 0
      while 1:
        x += 1
        filePath2 = filePath+str(x)
        if not os.path.exists(filePath2):
          filePath = filePath2
          break
    open(filePath).write(yaml.dump(self))

  def connect_audio(self, p1, ch1, p2, ch2, *, gain_db=None, group_id=None):
    if group_id is None:
      group_id = self.allocate_connection_group_id()

    self.connections.append(
      ProcessorConnection(
        p1, ch1,
        p2, ch2,
        gain_db=gain_db,
        group_id=group_id
      )
    )
    return group_id

  def connect_stereo(
          self,
          proc1: Processor,
          proc2: Processor,
          *,
          gain_db=None
  ):
    gid = self.allocate_connection_group_id()

    for ch in (0, 1):
      self.connections.append(
        ProcessorConnection(
          proc1, ch,
          proc2, ch,
          gain_db=gain_db,
          group_id=gid
        )
      )

    return gid

  def disconnect_audio(self, group_id: int) -> int:
        """
        Remove all ProcessorConnections with this group_id.
        Returns the number removed.
        """
        removed = 0
        kept = []
        for c in self.connections:
          if c.group_id == group_id:
            removed += 1
          else:
            kept.append(c)
        self.connections[:] = kept
        return removed

# should adding and removing to a song automatically update the info on the server side? i think just have a send() method to update on the server side.
# remember to call MidiSchedule(notes) for all notes in notesSchedules and send them when updating the song on the server side
# todo: make lists of audiotracks
# todo: add support for multiple tracks in one song. according to chatgpt, each DAW track has its own set of effects and connections between them, but you can also route each track to whatever other effects..
# maybe instead of midSchedules, etc. we should have one schedule for each thing per track

def load_song(path):
  return yaml.load(open(path))

def main():
  if len(sys.argv)>1:
    pipename = sys.argv[1]
    print(f"using pipe name: {pipename}")
    client = JuceAudioClient(sys.argv[1])
  else:
    client = JuceAudioClient()

  # Connect will auto-start the server if needed
  if not client.connect(auto_start=True, max_retries=5, retry_delay=1.0):
    print("Failed to connect to server")
    print("Make sure juce_gui_server.exe is in the same directory or specify path")
    return
  print("Connected!")
  print("scanning directories")
  plugindirs = defaultDirs + (r"d:\music creation\free vsts", r"(C:\Users\inhah\AppData\Local\Orchestral Tools\SINE Player\Content")
  #plugindirs = (r"d:\music creation\free vsts",)
  jf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "badpaths.json")
  badpaths = json.loads(open(jf).read()) if os.path.exists(jf) else []
  numfound = client.scanplugins(plugindirs, badpaths)
  print(f"numfound: {numfound}")
  badpathsfound = client.listbadpaths()
  print(f"badpaths found: {len(badpathsfound)}")
  badpaths += badpathsfound
  badpaths = list(set(badpaths))
  open(jf, "w").write(json.dumps(badpaths))
  
  running = True
  def read_notifications_thread():
    global running
    while True:
      try:
        cmd = client.readinfo1n("B")
        if cmd==recv_cmd.param_changed:
          pluginId, parameterIndex, value = client.readinfon("IIf")
          atSample = client.readinfo1n("Q")
          print(f"parameter change: {pluginId=}, {parameterIndex=}, {value=}, atSample={atSample}")
          p_c = dummy()
          p_c.pluginId = pluginId
          p_c.parameterIndex = parameterIndex
          p_c.value = value
          p_c.atSample = atSample
          param_changes.append(p_c)
        elif cmd==recv_cmd.midi_note_event:
          noteNumber, velocity, channel, isNoteOn, samplePosition = client.readinfon("IIIIQ")
          event_type = "NOTE ON" if isNoteOn else "NOTE OFF"
          print(f"MIDI {event_type}: note={noteNumber}, velocity={velocity}, channel={channel}, sample={samplePosition}")
        elif cmd==recv_cmd.midi_cc_event:
          controller, value, channel, atSample = client.readinfon("IIIQ")
          print(f"MIDI CC: controller={controller}, value={value}, channel={channel}, atSample={atSample}")
        elif cmd==recv_cmd.virtual_keyboard_note_event:
          noteNumber, velocity, channel, isNoteOn, samplePosition = client.readinfon("IIIIQ")
          event_type = "NOTE ON" if isNoteOn else "NOTE OFF"
          print(f"VIRTUAL KEYBOARD {event_type}: note={noteNumber}, velocity={velocity}, channel={channel}, sample={samplePosition}")
        elif cmd==recv_cmd.virtual_keyboard_cc_event:
          controller, value, channel, atSample = client.readinfon("IIIQ")
          print(f"VIRTUAL KEYBOARD CC: controller={controller}, value={value}, channel={channel}, atSample={atSample}")
        elif cmd==recv_cmd.midi_keyboard_routed:
          pluginId, samplePosition = client.readinfon("iQ")
          if pluginId == -3: #todo: check this value
            print(f"MIDI keyboard unrouted at sample {samplePosition}")
          else:
            print(f"MIDI keyboard routed to plugin {pluginId} at sample {samplePosition}")
        elif cmd==recv_cmd.virtual_keyboard_routed:
          pluginId, samplePosition = client.readinfon("iQ")
          if pluginId == -3: #todo: check this value
            print(f"Virtual keyboard unrouted at sample {samplePosition}")
          else:
            print(f"Virtual keyboard routed to plugin {pluginId} at sample {samplePosition}")
        elif cmd==recv_cmd.stop_playback:
          pass
        elif cmd==recv_cmd.param_changeds_end:
          pass
        elif cmd==recv_cmd.cmd_shutdown:
          running = False
          break
      except Exception as e:
        print(f"Notification thread error: {e}")
        break

  t = threading.Thread(target=read_notifications_thread)
  t.start()

  print("loading and showing 5 plugins")

  for x in range(10):
    print(f"plugin #{x}")
    client.loadpluginbyindex(x)
    client.showpluginui(x)
    p = client.getPluginInfo(x)
    print(f"{p.name=} {p.isInstrument=}")
    success, numParams, params, errmsg = client.getParamsInfo(x)
    for p in params:
      print()
      for a in "name originalIndex minValue maxValue interval skewFactor value numSteps isDiscrete isBoolean isOrientationInverted isAutomatable isMetaParameter".split():
        print(f"{x}: {a}: {getattr(p, a)}")

  processors, connections, gain_inits = insert_gain_processors(project, UID_GAIN)
  # todo: send processors, connections, gain_units to server

  client.startplayback(15*sampleRate, False, "")
  x = input()
  client.commands_pipe_handle.flush()  
  client.disconnect()  

if __name__ == "__main__":
  main()

# ---- helpers for stable ids ----

def _stable_hash(obj: Any) -> str:
    """
    Deterministic hash of JSON-serializable obj (dict/list/str/float/int/bool/None).
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

# for saving signal graphs to file
@dataclass
class SignalGraphDump:
    nodes: Dict[str, Dict[str, Any]]      # node_id -> node_spec
    roots: Dict[str, str]                  # name -> node_id
    wavetables: Dict[str, list[float]]     # table_id -> samples

class SignalGraphSerializer:
    """
    Serializes Signal DAGs with sharing preserved.
    """

    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.memo_by_obj: Dict[int, str] = {}           # python object identity -> node_id
        self.wavetables: Dict[str, list[float]] = {}    # table_id -> samples

    def dump_named_roots(self, roots: Dict[str, "Signal"]) -> SignalGraphDump:
        out_roots: Dict[str, str] = {}
        for name, sig in roots.items():
            out_roots[name] = self._emit(sig)
        return SignalGraphDump(nodes=self.nodes, roots=out_roots, wavetables=self.wavetables)

    def _emit(self, sig: "Signal") -> str:
        # preserve sharing by python identity
        oid = id(sig)
        if oid in self.memo_by_obj:
            return self.memo_by_obj[oid]

        spec = self._node_spec(sig)               # may recurse for children
        node_id = "n" + _stable_hash(spec)        # content-based id => stable across runs if structure same

        # In the rare case of collision, deconflict
        if node_id in self.nodes and self.nodes[node_id] != spec:
            # extremely unlikely; append a salt
            salt = 1
            while True:
                node_id2 = node_id + "_" + str(salt)
                if node_id2 not in self.nodes:
                    node_id = node_id2
                    break
                salt += 1

        self.memo_by_obj[oid] = node_id
        self.nodes[node_id] = spec
        return node_id

    def _table_ref(self, table: list[float]) -> str:
        # store wavetable once; refer by a stable hash of samples
        tid = "wt" + _stable_hash([float(x) for x in table])
        if tid not in self.wavetables:
            self.wavetables[tid] = [float(x) for x in table]
        return tid

    def _node_spec(self, sig: "Signal") -> Dict[str, Any]:
        # Const
        if isinstance(sig, Const):
            return {"type": "const", "value": float(sig.value)}

        # Binary ops
        if isinstance(sig, Add):
            return {"type": "add", "a": self._emit(sig.a), "b": self._emit(sig.b)}
        if isinstance(sig, Sub):
            return {"type": "sub", "a": self._emit(sig.a), "b": self._emit(sig.b)}
        if isinstance(sig, Mul):
            return {"type": "mul", "a": self._emit(sig.a), "b": self._emit(sig.b)}
        if isinstance(sig, Neg):
            return {"type": "neg", "x": self._emit(sig.x)}

        # Wavetables
        if isinstance(sig, WavetableOverTime):
            return {
                "type": "wavetable_over_time",
                "table_ref": self._table_ref(sig.table),
                "duration_seconds": float(sig.duration),
                "phase0": float(sig.phase0),
                "interp": sig.interp,
                "loop": bool(sig.loop),
            }

        if isinstance(sig, WavetableOsc):
            return {
                "type": "wavetable_osc",
                "table_ref": self._table_ref(sig.table),
                "freq": self._emit(sig.freq),
                "phase0": float(sig.phase0),
                "interp": sig.interp,
                "loop": bool(sig.loop),
                "cache_every_blocks": int(sig._cache_every_blocks),
            }

        # BeatFn/TimeFn/lambdas are addressed below (see lambda section)
        if isinstance(sig, TimeFn):
            raise ValueError("TimeFn with lambda is not serializable as-is (see lambda strategies below).")
        if isinstance(sig, BeatFn):
            raise ValueError("BeatFn with lambda is not serializable as-is (see lambda strategies below).")

        raise TypeError(f"Unsupported Signal type for serialization: {type(sig).__name__}")

class SignalGraphDeserializer:
  def __init__(self, nodes: Dict[str, Dict[str, Any]], wavetables: Dict[str, list[float]]):
    self.nodes = nodes
    self.wavetables = wavetables
    self.memo: Dict[str, Signal] = {}  # node_id -> Signal instance

  def build(self, root_id: str) -> "Signal":
    if root_id in self.memo:
      return self.memo[root_id]

    spec = self.nodes[root_id]
    t = spec["type"]

    if t == "const":
      sig = Const(spec["value"])

    elif t == "add":
      sig = Add(self.build(spec["a"]), self.build(spec["b"]))
    elif t == "sub":
      sig = Sub(self.build(spec["a"]), self.build(spec["b"]))
    elif t == "mul":
      sig = Mul(self.build(spec["a"]), self.build(spec["b"]))
    elif t == "neg":
      sig = Neg(self.build(spec["x"]))

    elif t == "wavetable_over_time":
      table = self.wavetables[spec["table_ref"]]
      sig = WavetableOverTime(
        table,
        duration_seconds=float(spec["duration_seconds"]),
        phase0=float(spec["phase0"]),
        interp=spec["interp"],
        loop=bool(spec["loop"]),
      )

    elif t == "wavetable_osc":
      table = self.wavetables[spec["table_ref"]]
      freq = self.build(spec["freq"])
      sig = WavetableOsc(
        table,
        freq_hz=freq,
        phase0=float(spec["phase0"]),
        interp=spec["interp"],
        loop=bool(spec["loop"]),
        cache_every_blocks=int(spec["cache_every_blocks"]),
      )

    else:
      raise ValueError(f"Unknown node type: {t}")

    self.memo[root_id] = sig
    return sig

def resolve_callable(path: str):
    mod_name, func_name = path.split(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, func_name)

@dataclass(frozen=True)
class AudioFileRef:
    id: str                  # stable id in song (uuid or user label)
    path: str                # file path (or uri)
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    length_samples: Optional[int] = None
    # (optional) hash for caching / change detection

@dataclass(frozen=True)
class Fade:
    in_samples: int = 0
    out_samples: int = 0
    curve: str = "linear"     # later: "exp", "log", etc.

@dataclass(frozen=True)
class AudioClip:
    """
    A clip is a region of an audio file placed on the timeline.
    Everything is non-destructive: you never change the file; you only change metadata.
    """
    id: str
    file_id: str             # AudioFileRef.id
    start_sample: int        # where this clip sits on the track timeline
    length_samples: int      # duration on the timeline
    file_offset_samples: int = 0  # “slip” offset into the source file
    gain_db: float = 0.0
    mute: bool = False
    fade: Fade = Fade()
    # (optional) stretch/warp info could go here later


@dataclass(frozen=True)
class AudioFileRef:
  id: str  # stable id in song (uuid or user label)
  path: str  # file path (or uri)
  sample_rate: Optional[int] = None
  channels: Optional[int] = None
  length_samples: Optional[int] = None
  # (optional) hash for caching / change detection


@dataclass(frozen=True)
class Fade:
  in_samples: int = 0
  out_samples: int = 0
  curve: str = "linear"  # later: "exp", "log", etc.


@dataclass(frozen=True)
class AudioClip:
  """
  A clip is a region of an audio file placed on the timeline.
  Everything is non-destructive: you never change the file; you only change metadata.
  """
  id: str
  file_id: str  # AudioFileRef.id
  start_sample: int  # where this clip sits on the track timeline
  length_samples: int  # duration on the timeline
  file_offset_samples: int = 0  # “slip” offset into the source file
  gain_db: float = 0.0
  mute: bool = False
  fade: Fade = Fade()
  # (optional) stretch/warp info could go here later

dataclass
class AudioLane:
    """
    A lane holds clips that must not overlap (or you define overlap rules).
    """
    id: str
    clips: List[AudioClip] = field(default_factory=list)

@dataclass(frozen=True)
class CompSegment:
    """
    Select audio from a specific lane for a time range on the track timeline.
    """
    start_sample: int
    end_sample: int          # exclusive
    take_lane_id: str
    # optional crossfade between adjacent segments:
    xfade_samples: int = 0

@dataclass
class Comp:
    """
    The comp is the authoritative “what you hear” result.
    Segments should be non-overlapping and cover only the parts you want audible.
    """
    segments: List[CompSegment] = field(default_factory=list)
    active: bool = True

@dataclass
class AudioTrack:
    id: str
    name: str
    lanes: List[AudioLane] = field(default_factory=list)

# If comp.active is true: render only what the comp selects (plus maybe non-take lanes depending on your policy)
# If no comp: render all “regular lanes” (and optionally take lanes if you want)

@dataclass
class AudioLane:
    id: str
    clips: List[AudioClip] = field(default_factory=list)
    time_offset_samples: int = 0  # positive = later, negative = earlier

# =========================
# 1) Audio file + clip model
# =========================

@dataclass(frozen=True)
class AudioFileRef:
    """
    A stable reference to an audio file used in the project.

    - id: stable project-local identifier (e.g. "vox_take1")
    - path: file path (you can later allow URIs)
    - Optional metadata fields are for convenience/caching/validation.
    """
    id: str
    path: str
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    length_samples: Optional[int] = None


@dataclass(frozen=True)
class Fade:
    """
    Fade lengths in samples. Curves can be extended later.
    """
    in_samples: int = 0
    out_samples: int = 0
    curve: str = "linear"


@dataclass(frozen=True)
class AudioClip:
    """
    A region of an audio file placed on the timeline (non-destructive).

    start_sample: where clip starts on the track timeline
    length_samples: duration on timeline
    file_offset_samples: where reading begins in the source file ("slip")
    gain_db: per-clip gain (optional)
    mute: if True, contributes no audio
    fade: fade-in/out applied to this clip
    """
    id: str
    file_id: str
    start_sample: int
    length_samples: int
    file_offset_samples: int = 0
    gain_db: float = 0.0
    mute: bool = False
    fade: Fade = Fade()

    @property
    def end_sample(self) -> int:
        return self.start_sample + self.length_samples


@dataclass
class AudioLane:
    """
    A lane is a list of clips. You can decide overlap policy; this model allows overlap,
    but validate() can forbid it if you want.

    time_offset_samples: shifts the whole lane earlier/later on the timeline.
      - positive -> the lane plays later
      - negative -> the lane plays earlier
    """
    id: str
    clips: List[AudioClip] = field(default_factory=list)
    time_offset_samples: int = 0


# =========================
# 2) Comping model (takes + comp segments)
# =========================

@dataclass(frozen=True)
class CompSegment:
    """
    Select timeline region [start_sample, end_sample) from a specific take lane.
    xfade_samples requests a crossfade at this segment boundary (to the next segment).
    """
    start_sample: int
    end_sample: int
    take_lane_id: str
    xfade_samples: int = 0


@dataclass
class Comp:
    """
    A comp is the "what you hear" selection across take lanes.

    segments should be:
      - non-overlapping
      - sorted by start_sample
      - typically contiguous (but you can allow gaps)
    """
    segments: List[CompSegment] = field(default_factory=list)
    active: bool = True


# =========================
# Render plan output (trackless)
# =========================

@dataclass(frozen=True)
class RenderAudioRegion:
    """
    Trackless render primitive: "play this file slice at this time".
    This is what your scheduler can hand to the renderer layer.

    timeline_start: when it starts on the project timeline
    length_samples: how long on the timeline
    file_offset: where to start reading from the file
    gain_db + fades: applied at render/mix time
    """
    file_id: str
    timeline_start: int
    length_samples: int
    file_offset: int
    gain_db: float = 0.0
    fade_in: int = 0
    fade_out: int = 0

    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.length_samples


# =========================
# 3) AudioTrack + Song container
# =========================

@dataclass
class AudioTrack:
    id: str
    name: str

    # "takes" are lanes used for comping
    take_lanes: Dict[str, AudioLane] = field(default_factory=dict)

    # optional regular lanes (non-take audio, imports, FX bounces, etc.)
    lanes: Dict[str, AudioLane] = field(default_factory=dict)

    comp: Optional[Comp] = None

    # -------------------------
    # lane/take management API
    # -------------------------

    def ensure_take_lane(self, lane_id: str) -> AudioLane:
        if lane_id not in self.take_lanes:
            self.take_lanes[lane_id] = AudioLane(id=lane_id)
        return self.take_lanes[lane_id]

    def ensure_lane(self, lane_id: str) -> AudioLane:
        if lane_id not in self.lanes:
            self.lanes[lane_id] = AudioLane(id=lane_id)
        return self.lanes[lane_id]

    def add_take_clip(self, take_lane_id: str, clip: AudioClip) -> None:
        lane = self.ensure_take_lane(take_lane_id)
        lane.clips.append(clip)

    def set_take_offset(self, take_lane_id: str, offset_samples: int) -> None:
        lane = self.ensure_take_lane(take_lane_id)
        lane.time_offset_samples = int(offset_samples)

    def set_comp(self, segments: List[CompSegment], *, active: bool = True) -> None:
        self.comp = Comp(segments=list(segments), active=active)

    # -------------------------
    # validation
    # -------------------------

    def validate(self) -> None:
        # Validate all clips in all lanes
        for lane in list(self.take_lanes.values()) + list(self.lanes.values()):
            for c in lane.clips:
                _validate_clip(c)

        # Validate comp structure
        if self.comp and self.comp.active:
            _validate_comp(self.comp, self.take_lanes)

    # -------------------------
    # compilation: comp -> regions
    # -------------------------

    def compile_to_regions(self, *, include_non_take_lanes: bool = True) -> List[RenderAudioRegion]:
        """
        Returns a list of RenderAudioRegion for this track, with comp applied if active.

        - If comp is active: emits regions corresponding to comp selection from take lanes.
        - If comp is not active or absent: emits all clips from non-take lanes (and optionally take lanes).
        - include_non_take_lanes: if comp is active, you probably still want extra imported clips to play;
          set False if you want "comp only".
        """
        self.validate()

        regions: List[RenderAudioRegion] = []

        if self.comp and self.comp.active:
            # 1) comp-derived regions
            regions.extend(_compile_comp_regions(self.comp, self.take_lanes))

            # 2) plus regular lanes, if desired
            if include_non_take_lanes:
                for lane in self.lanes.values():
                    regions.extend(_lane_clips_to_regions(lane))

        else:
            # no active comp: just emit regular lanes
            for lane in self.lanes.values():
                regions.extend(_lane_clips_to_regions(lane))
            # optionally emit take lanes too if you want "all takes audible"
            # (default: not doing it; you can add a flag if desired)

        # determinism
        regions.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
        return regions


@dataclass
class Song:
    """
    Top-level project container (trackless renderer downstream).
    """
    sample_rate: int
    block_size: int = 128
    duration_samples: int = 0

    audio_files: Dict[str, AudioFileRef] = field(default_factory=dict)
    audio_tracks: Dict[str, AudioTrack] = field(default_factory=dict)

    def add_audio_file(self, file_id: str, path: str, **meta) -> AudioFileRef:
        if file_id in self.audio_files:
            raise ValueError(f"Audio file id already exists: {file_id}")
        ref = AudioFileRef(id=file_id, path=path, **meta)
        self.audio_files[file_id] = ref
        return ref

    def add_audio_track(self, track_id: str, name: Optional[str] = None) -> AudioTrack:
        if track_id in self.audio_tracks:
            raise ValueError(f"Audio track id already exists: {track_id}")
        t = AudioTrack(id=track_id, name=name or track_id)
        self.audio_tracks[track_id] = t
        return t

    def compile_all_audio_regions(self) -> List[RenderAudioRegion]:
        """
        Compile all audio tracks into a unified list of RenderAudioRegion.
        This is trackless data the renderer can consume.

        NOTE: you may later attach a destination bus/plugin input per region,
        but this keeps it simple and purely timeline-based.
        """
        out: List[RenderAudioRegion] = []
        for tr in self.audio_tracks.values():
            out.extend(tr.compile_to_regions(include_non_take_lanes=True))
        out.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
        return out


# =========================
# Helpers: validation
# =========================

def _validate_clip(c: AudioClip) -> None:
    if c.start_sample < 0:
        raise ValueError(f"Clip {c.id}: start_sample must be >= 0")
    if c.length_samples <= 0:
        raise ValueError(f"Clip {c.id}: length_samples must be > 0")
    if c.file_offset_samples < 0:
        raise ValueError(f"Clip {c.id}: file_offset_samples must be >= 0")
    if c.fade.in_samples < 0 or c.fade.out_samples < 0:
        raise ValueError(f"Clip {c.id}: fades must be >= 0")


def _validate_comp(comp: Comp, take_lanes: Dict[str, AudioLane]) -> None:
    segs = list(comp.segments)
    if not segs:
        return

    # sort check and monotonic check
    segs_sorted = sorted(segs, key=lambda s: (s.start_sample, s.end_sample))
    if segs_sorted != segs:
        raise ValueError("Comp.segments must be sorted by start_sample (and stable)")

    prev_end = None
    for s in segs:
        if s.start_sample < 0:
            raise ValueError("CompSegment.start_sample must be >= 0")
        if s.end_sample <= s.start_sample:
            raise ValueError("CompSegment must have end_sample > start_sample")
        if s.take_lane_id not in take_lanes:
            raise ValueError(f"CompSegment references missing take lane: {s.take_lane_id}")
        if s.xfade_samples < 0:
            raise ValueError("CompSegment.xfade_samples must be >= 0")

        if prev_end is not None and s.start_sample < prev_end:
            raise ValueError("Comp.segments must not overlap")
        prev_end = s.end_sample


# =========================
# Helpers: lane -> regions
# =========================

def _lane_clips_to_regions(lane: AudioLane) -> List[RenderAudioRegion]:
    """
    Convert lane clips to render regions, applying lane time offset.
    """
    out: List[RenderAudioRegion] = []
    off = int(lane.time_offset_samples)

    for c in lane.clips:
        if c.mute:
            continue

        # clip timeline is shifted by lane offset
        timeline_start = c.start_sample + off
        if timeline_start < 0:
            # allow but clamp? for now: forbid
            raise ValueError(f"Lane {lane.id}: clip {c.id} shifts before t=0 due to lane offset")

        out.append(
            RenderAudioRegion(
                file_id=c.file_id,
                timeline_start=timeline_start,
                length_samples=c.length_samples,
                file_offset=c.file_offset_samples,
                gain_db=c.gain_db,
                fade_in=c.fade.in_samples,
                fade_out=c.fade.out_samples,
            )
        )

    return out


# =========================
# 4) Comp compilation: selection + lane offset + crossfades
# =========================

def _compile_comp_regions(comp: Comp, take_lanes: Dict[str, AudioLane]) -> List[RenderAudioRegion]:
    """
    Convert comp segments into concrete RenderAudioRegion(s) by slicing take-lane clips.

    Crossfades:
      For boundary between seg[i] and seg[i+1], we create an overlap of xfade_samples
      (limited by the adjacent segment lengths), and apply:
        - fade_out on the outgoing region in the overlap
        - fade_in  on the incoming region in the overlap

      This outputs *overlapping* regions. Your downstream mixing graph (fan-in) sums them,
      which is exactly how crossfades work.
    """
    segs = comp.segments
    out: List[RenderAudioRegion] = []

    if not segs:
        return out

    # Precompute per-segment effective fade overlap with the next segment
    # We'll compute for each boundary: overlap = min(requested, len(seg_i), len(seg_{i+1}))
    overlaps: List[int] = [0] * (len(segs) - 1)
    for i in range(len(segs) - 1):
        a = segs[i]
        b = segs[i + 1]
        req = int(a.xfade_samples)
        if req <= 0:
            overlaps[i] = 0
            continue
        len_a = a.end_sample - a.start_sample
        len_b = b.end_sample - b.start_sample
        overlaps[i] = max(0, min(req, len_a, len_b))

    # We'll emit regions per segment, but split around overlaps so we can attach fades.
    for i, seg in enumerate(segs):
        lane = take_lanes[seg.take_lane_id]
        lane_off = int(lane.time_offset_samples)

        # segment on timeline is [ts, te)
        ts = seg.start_sample
        te = seg.end_sample

        # Apply crossfade overlap logic:
        # - if there's an overlap with previous boundary, extend this segment backward by overlap
        # - if there's an overlap with next boundary, extend this segment forward by overlap
        # BUT: we do this by splitting actual emitted regions:
        #
        # Outgoing segment i has a tail overlap with i+1: [te-overlap, te)
        # Incoming segment i has a head overlap with i-1: [ts, ts+overlap_prev)
        #
        overlap_prev = overlaps[i - 1] if i > 0 else 0
        overlap_next = overlaps[i] if i < len(segs) - 1 else 0

        # 1) main non-overlapped body: [ts + overlap_prev, te - overlap_next)
        body_start = ts + overlap_prev
        body_end = te - overlap_next
        if body_end > body_start:
            out.extend(
                _slice_take_lane_into_regions(
                    lane=lane,
                    lane_offset=lane_off,
                    timeline_start=body_start,
                    timeline_end=body_end,
                    fade_in=0,
                    fade_out=0,
                )
            )

        # 2) head overlap (incoming) region: [ts, ts+overlap_prev) with fade-in
        if overlap_prev > 0:
            out.extend(
                _slice_take_lane_into_regions(
                    lane=lane,
                    lane_offset=lane_off,
                    timeline_start=ts,
                    timeline_end=ts + overlap_prev,
                    fade_in=overlap_prev,
                    fade_out=0,
                )
            )

        # 3) tail overlap (outgoing) region: [te-overlap_next, te) with fade-out
        if overlap_next > 0:
            out.extend(
                _slice_take_lane_into_regions(
                    lane=lane,
                    lane_offset=lane_off,
                    timeline_start=te - overlap_next,
                    timeline_end=te,
                    fade_in=0,
                    fade_out=overlap_next,
                )
            )

    # deterministic ordering helps downstream
    out.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
    return out


def _slice_take_lane_into_regions(
    *,
    lane: AudioLane,
    lane_offset: int,
    timeline_start: int,
    timeline_end: int,
    fade_in: int,
    fade_out: int,
) -> List[RenderAudioRegion]:
    """
    Slices the take lane's clips to cover [timeline_start, timeline_end) on the project timeline.

    IMPORTANT: lane_offset shifts clips on the timeline.
      A clip with c.start_sample appears at (c.start_sample + lane_offset).

    This function finds clip overlaps and emits RenderAudioRegion slices, adjusting file_offset accordingly.
    """
    if timeline_end <= timeline_start:
        return []

    out: List[RenderAudioRegion] = []

    # Find overlaps across all clips in the lane.
    # In many projects, each take lane has one long clip; this will be fast.
    for c in lane.clips:
        if c.mute:
            continue

        clip_t0 = c.start_sample + lane_offset
        clip_t1 = c.end_sample + lane_offset

        # overlap of [timeline_start,timeline_end) with [clip_t0,clip_t1)
        ov0 = max(timeline_start, clip_t0)
        ov1 = min(timeline_end, clip_t1)
        if ov1 <= ov0:
            continue

        # The slice begins ov0 on timeline; in the file it begins at:
        # c.file_offset_samples + (ov0 - clip_t0)
        file_off = c.file_offset_samples + (ov0 - clip_t0)

        out.append(
            RenderAudioRegion(
                file_id=c.file_id,
                timeline_start=ov0,
                length_samples=(ov1 - ov0),
                file_offset=file_off,
                gain_db=c.gain_db,
                fade_in=min(int(fade_in), ov1 - ov0),
                fade_out=min(int(fade_out), ov1 - ov0),
            )
        )

    # If nothing overlapped, this segment is silent; that's allowed.
    out.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
    return out