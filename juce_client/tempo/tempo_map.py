from __future__ import annotations

import bisect, math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Dict, Optional, Union, Tuple, Set, List, Protocol

from ..signals.core import (
    Signal, EvalContext, ControlCache, detect_signal_cycle, signal_depends_on_beat,
    Const, TimeFn, BeatFn, RefSignal, Add, Sub, Mul, Neg,
    Clamp, Smooth, Rectify, Power, MapRange, Mix,
    WavetableOsc, WavetableOverTime, GlobalCanonicalizer,
    _wrap01, _clamp01, _wavetable_lookup,
)
# ----------------------------
# Note types
# ----------------------------

@dataclass(frozen=True)
class BeatNote:
    start: Fraction
    dur: Fraction
    pitch: int
    vel: int
    origin_id: int

@dataclass(frozen=True)
class ScheduledNote:
    start_sample: int
    dur_samples: int
    pitch: int
    vel: int
    origin_id: int

BeatArrangement = List[BeatNote]
ScheduledArrangement = List[ScheduledNote]


# ----------------------------
# Rounding / quantization
# ----------------------------

class RoundMode:
    CEIL = "ceil"
    FLOOR = "floor"
    NEAREST = "nearest"

def round_float_to_int(x: float, mode: str) -> int:
    if mode == RoundMode.CEIL:
        return int(math.ceil(x))
    if mode == RoundMode.FLOOR:
        return int(math.floor(x))
    if mode == RoundMode.NEAREST:
        return int(round(x))
    raise ValueError(f"Unknown round mode: {mode}")

def quantize_beat_float_to_fraction(
    beat: float,
    grid: Fraction,
    mode: str = RoundMode.NEAREST,
) -> Fraction:
    g = float(grid)
    if g <= 0:
        raise ValueError("grid must be > 0")
    q = beat / g
    if mode == RoundMode.CEIL:
        qi = math.ceil(q)
    elif mode == RoundMode.FLOOR:
        qi = math.floor(q)
    elif mode == RoundMode.NEAREST:
        qi = int(round(q))
    else:
        raise ValueError(f"Unknown round mode: {mode}")
    return grid * qi


# ----------------------------
# Anchor cache: beat <-> sample
# ----------------------------

@dataclass
class AnchorCache:
    beats: List[float] = field(default_factory=list)
    samples: List[int] = field(default_factory=list)

    def add_anchor(self, beat: float, sample: int) -> None:
        if self.beats:
            if beat <= self.beats[-1] or sample <= self.samples[-1]:
                raise ValueError("Anchors must be strictly increasing")
        self.beats.append(beat)
        self.samples.append(sample)

    def find_anchor_leq_beat(self, beat: float) -> Tuple[float, int]:
        if not self.beats:
            raise ValueError("No anchors")
        i = bisect.bisect_right(self.beats, beat) - 1
        i = max(i, 0)
        return self.beats[i], self.samples[i]

    def find_anchor_leq_sample(self, sample: int) -> Tuple[float, int]:
        if not self.samples:
            raise ValueError("No anchors")
        i = bisect.bisect_right(self.samples, sample) - 1
        i = max(i, 0)
        return self.beats[i], self.samples[i]


# ----------------------------
# Tempo segments
# ----------------------------

class TempoSegment(Protocol):
    b0: float
    b1: float
    t0: float
    def bpm(self, beat: float) -> float: ...
    def beat_to_time_seconds(self, beat: float) -> float: ...
    def time_seconds_to_beat(self, t: float) -> float: ...

@dataclass
class ConstBpmByBeat:
    b0: float
    b1: float
    bpm_value: float
    t0: float

    def bpm(self, beat: float) -> float:
        return self.bpm_value

    def beat_to_time_seconds(self, beat: float) -> float:
        spb = 60.0 / self.bpm_value
        return self.t0 + (beat - self.b0) * spb

    def time_seconds_to_beat(self, t: float) -> float:
        spb = 60.0 / self.bpm_value
        return self.b0 + (t - self.t0) / spb

@dataclass
class LinearBpmByBeat:
    """
    BPM(b) = m*b + c on [b0, b1)
    """
    b0: float
    b1: float
    m: float
    c: float
    t0: float

    def bpm(self, beat: float) -> float:
        return self.m * beat + self.c

    def beat_to_time_seconds(self, beat: float) -> float:
        if abs(self.m) < 1e-15:
            bpm0 = self.bpm(self.b0)
            spb = 60.0 / bpm0
            return self.t0 + (beat - self.b0) * spb

        num = self.m * beat + self.c
        den = self.m * self.b0 + self.c
        if num <= 0 or den <= 0:
            raise ValueError("BPM must stay > 0 throughout segment")
        return self.t0 + (60.0 / self.m) * math.log(num / den)

    def time_seconds_to_beat(self, t: float) -> float:
        if abs(self.m) < 1e-15:
            bpm0 = self.bpm(self.b0)
            spb = 60.0 / bpm0
            return self.b0 + (t - self.t0) / spb

        den = self.m * self.b0 + self.c
        if den <= 0:
            raise ValueError("BPM must stay > 0")
        factor = math.exp((self.m / 60.0) * (t - self.t0))
        return (den * factor - self.c) / self.m

@dataclass
class LambdaBpmByBeat:
    """
    Arbitrary BPM(b) supplied by user. beat_to_time/time_to_beat are handled by TempoMap numerically.
    """
    b0: float
    b1: float
    bpm_fn: Callable[[float], float]
    t0: float

    def bpm(self, beat: float) -> float:
        bpm = float(self.bpm_fn(beat))
        if bpm <= 0:
            raise ValueError("BPM must be > 0")
        return bpm

    def beat_to_time_seconds(self, beat: float) -> float:
        raise NotImplementedError("Use TempoMap cached methods")

    def time_seconds_to_beat(self, t: float) -> float:
        raise NotImplementedError("Use TempoMap cached methods")


# ----------------------------
# TempoMap (with inverse refinement)
# ----------------------------

@dataclass
class TempoMap:
    segments: List[TempoSegment]
    sample_rate: int
    anchors: AnchorCache = field(default_factory=AnchorCache)
    bpm_mod_signal: Optional[Signal] = None
    block_samples: int = 128
    anchor_every_samples: int = 8192
    round_mode: str = RoundMode.CEIL

    def __post_init__(self):
        self.segments.sort(key=lambda s: s.b0)

        if not self.segments:
            raise ValueError("TempoMap needs at least one segment")

        if self.bpm_mod_signal is not None:
            detect_signal_cycle(self.bpm_mod_signal)
            if signal_depends_on_beat(self.bpm_mod_signal):
                raise ValueError("Tempo modulation signal must not depend on beat (cycle risk).")

        self.anchors.add_anchor(self.segments[0].b0, 0)

    def _find_segment_for_beat(self, beat: float) -> TempoSegment:
        b0s = [s.b0 for s in self.segments]
        i = bisect.bisect_right(b0s, beat) - 1
        if i < 0:
            raise ValueError("beat before first segment")
        seg = self.segments[i]
        if not (seg.b0 <= beat < seg.b1):
            raise ValueError("beat not covered by any segment")
        return seg

    def _bpm_effective(self, beat_est: float, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        bpm = self._bpm_base(beat_est)
        if self.bpm_mod_signal is not None:
            bpm += float(self.bpm_mod_signal.at(sample, ctx, cache))
        if bpm <= 0 or not math.isfinite(bpm):
            raise ValueError("Effective BPM must be finite and > 0")
        return bpm

    def set_bpm_modulation(self, signal: Optional[Signal]) -> None:
        if signal is not None:
            detect_signal_cycle(signal)
            if signal_depends_on_beat(signal):
                raise ValueError(
                    "Tempo modulation signal must not depend on beat "
                    "(would create a dependency cycle). Use a time/sample-based signal instead."
                )
        self.bpm_mod_signal = signal
        # ---- Beat -> Sample ----

    def beat_to_sample(self, beat: Union[Fraction, float], *, ctx: Optional[EvalContext]=None, cache: Optional[ControlCache]=None) -> int:
        b = float(beat)

        # If no modulation, keep the existing fast path
        if self.bpm_mod_signal is None:
            seg = self._find_segment_for_beat(b)
            if not isinstance(seg, LambdaBpmByBeat):
                t = seg.beat_to_time_seconds(b)
                return round_float_to_int(t * self.sample_rate, self.round_mode)
            return self._beat_to_sample_lambda_cached(b, seg)

        # With modulation: must have ctx/cache for signal evaluation
        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if self.bpm_mod_signal.requires_beat:
            raise ValueError("Tempo modulation must not depend on beat (cycle).")
        if cache is None:
            cache = ControlCache()

        return self._beat_to_sample_with_modulation(b, ctx, cache)

    def _bpm_base(self, beat: float) -> float:
        seg = self._find_segment_for_beat(beat)
        return seg.bpm(beat)

    def _beat_to_sample_lambda_cached(self, b_target: float, seg: LambdaBpmByBeat) -> int:
        b_anchor, s_anchor = self.anchors.find_anchor_leq_beat(b_target)
        if b_anchor < seg.b0:
            raise ValueError("Missing anchor at lambda segment boundary (build boundary anchors)")

        cur_b = b_anchor
        cur_s = s_anchor

        while cur_b < b_target:
            bpm_now = seg.bpm(cur_b)
            beats_per_sec = bpm_now / 60.0
            remaining_beats = b_target - cur_b
            max_block = int(math.ceil(remaining_beats * self.sample_rate / max(beats_per_sec, 1e-12)))
            block = max(1, min(self.block_samples, max_block))

            dt = block / self.sample_rate

            # midpoint
            db_euler = beats_per_sec * dt
            mid_b = cur_b + 0.5 * db_euler
            bpm_mid = seg.bpm(mid_b)
            db = (bpm_mid / 60.0) * dt

            cur_b += db
            cur_s += block

            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                if cur_b <= self.anchors.beats[-1]:
                    raise ValueError("Non-monotonic beat progress during integration")
                self.anchors.add_anchor(cur_b, cur_s)

        return cur_s

    # ---- Sample -> Beat (fast + optional refinement) ----

    def sample_to_beat(
            self,
            sample: int,
            *,
            refine: bool = True,
            refine_iters: int = 20,
            ctx: Optional[EvalContext] = None,
            cache: Optional[ControlCache] = None,
    ) -> float:
        if sample < 0:
            raise ValueError("sample must be >= 0")

        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if cache is None:
            cache = ControlCache()

        # If modulation exists, prevent beat-dependent modulation (cycle) defensively
        if self.bpm_mod_signal is not None and self.bpm_mod_signal.requires_beat:
            raise ValueError("Tempo modulation must not depend on beat (cycle).")

        b_anchor, s_anchor = self.anchors.find_anchor_leq_sample(sample)
        cur_b = b_anchor
        cur_s = s_anchor

        b_low = cur_b
        s_low = cur_s

        while cur_s < sample:
            remaining = sample - cur_s
            block = min(self.block_samples, remaining)
            dt = block / self.sample_rate

            # --- Modulation-aware midpoint integration in SAMPLE domain ---
            # evaluate bpm at current point (use block midpoint sample for control-rate modulation)
            s_mid = cur_s + (block // 2)

            bpm_now = self._bpm_effective(cur_b, s_mid, ctx, cache)
            beats_per_sec = bpm_now / 60.0

            db_euler = beats_per_sec * dt
            mid_b = cur_b + 0.5 * db_euler

            bpm_mid = self._bpm_effective(mid_b, s_mid, ctx, cache)
            db = (bpm_mid / 60.0) * dt
            # -------------------------------------------------------------

            b_low, s_low = cur_b, cur_s
            cur_b += db
            cur_s += block

            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                if cur_b <= self.anchors.beats[-1]:
                    raise ValueError("Non-monotonic beat progress during integration")
                self.anchors.add_anchor(cur_b, cur_s)

        b_high = cur_b
        s_high = cur_s

        if not refine:
            return b_high

        # Refinement: binary search in beat space.
        # IMPORTANT: for modulation, beat_to_sample() must also be called with ctx/cache.
        lo, hi = b_low, b_high
        if s_low == sample:
            return lo
        if s_high == sample:
            return hi

        s_lo = self.beat_to_sample(lo, ctx=ctx, cache=cache)
        s_hi = self.beat_to_sample(hi, ctx=ctx, cache=cache)

        if not (s_lo <= sample <= s_hi):
            hi = hi + 1e-6
            s_hi = self.beat_to_sample(hi, ctx=ctx, cache=cache)
            if not (s_lo <= sample <= s_hi):
                return b_high

        for _ in range(refine_iters):
            mid = 0.5 * (lo + hi)
            s_mid2 = self.beat_to_sample(mid, ctx=ctx, cache=cache)
            if s_mid2 < sample:
                lo = mid
            else:
                hi = mid

        return hi

        #In your _beat_to_sample_with_modulation() you used the same s_mid for both bpm and bpm_mid. That’s fine (control-rate assumption). The patch above does the same: it samples modulation at s_mid and treats it constant across that block.
        #If later you want slightly better accuracy, you can sample modulation at cur_s + block//4 and cur_s + 3*block//4 (or similar), but not necessary now.

    def _beat_to_sample_with_modulation(self, b_target: float, ctx: EvalContext, cache: ControlCache) -> int:
        # Use your existing beat->sample anchors as starting point

        #"In _beat_to_sample_with_modulation() you compute bpm_mid using the same s_mid as the start bpm. Ideally midpoint integration should sample modulation at the midpoint sample too (or just accept “control-rate” tempo modulation). If you’re treating modulation as piecewise-constant per block, using the same s_mid is fine and consistent—just be aware it’s a deliberate approximation."

        b_anchor, s_anchor = self.anchors.find_anchor_leq_beat(b_target)
        cur_b = b_anchor
        cur_s = s_anchor

        # integrate in blocks until cur_b >= b_target
        while cur_b < b_target:
            block = ctx.block_size

            # sample modulation at block midpoint (or start)
            s_mid = cur_s + (block // 2)
            bpm_mod = float(self.bpm_mod_signal.at(s_mid, ctx, cache))
            bpm = self._bpm_base(cur_b) + bpm_mod
            if bpm <= 0 or not math.isfinite(bpm):
                raise ValueError("Effective BPM must be finite and > 0")

            dt = block / self.sample_rate

            # midpoint integration in beat-space
            db_euler = (bpm / 60.0) * dt
            mid_b = cur_b + 0.5 * db_euler
            bpm_mid = self._bpm_base(mid_b) + float(self.bpm_mod_signal.at(s_mid, ctx, cache))
            if bpm_mid <= 0 or not math.isfinite(bpm_mid):
                raise ValueError("Effective BPM must be finite and > 0")

            cur_b += (bpm_mid / 60.0) * dt
            cur_s += block

            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                self.anchors.add_anchor(cur_b, cur_s)

        return cur_s

    def ensure_boundary_anchors(self, ctx: Optional[EvalContext] = None, cache: Optional[ControlCache] = None) -> None:
        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if cache is None:
            cache = ControlCache()
        for seg in self.segments[1:]:
            _ = self.beat_to_sample(float(seg.b0), ctx=ctx, cache=cache)

# ----------------------------
# TempoMapBuilder (computes t0, validates segments, seeds anchors)
# ----------------------------

@dataclass
class TempoMapBuilder:
    """
    Build a beat-parameterized tempo map with guaranteed continuity.

    You add segments in increasing beat order. Builder computes each segment's t0
    so beat->time mapping is continuous.

    For analytic segments, segment end time is exact.
    For lambda segments, builder estimates end time by numeric integration in beat space,
    which is sufficient for continuity and scheduling.
    """
    sample_rate: int
    _segments: List[TempoSegment] = field(default_factory=list)
    _current_time: float = 0.0

    # numeric integration controls for lambda segment end-time estimation
    lambda_step_beats: float = 1.0 / 256  # smaller => more accurate

    def add_const(self, b0: float, b1: float, bpm: float) -> TempoMapBuilder:
        self._validate_new_segment_bounds(b0, b1)
        if bpm <= 0:
            raise ValueError("bpm must be > 0")
        seg = ConstBpmByBeat(b0=b0, b1=b1, bpm_value=bpm, t0=self._current_time)
        # advance current_time to end of segment exactly
        self._current_time = seg.beat_to_time_seconds(b1)
        self._segments.append(seg)
        return self

    def add_linear_by_beat(self, b0: float, b1: float, bpm0: float, bpm1: float) -> TempoMapBuilder:
        self._validate_new_segment_bounds(b0, b1)
        if bpm0 <= 0 or bpm1 <= 0:
            raise ValueError("BPM endpoints must be > 0")
        # BPM(b) = m*b + c. Solve for m,c from endpoints.
        m = (bpm1 - bpm0) / (b1 - b0)
        c = bpm0 - m * b0
        # Validate positivity across segment: linear -> min occurs at an endpoint
        if min(bpm0, bpm1) <= 0:
            raise ValueError("BPM must stay > 0 across segment")

        seg = LinearBpmByBeat(b0=b0, b1=b1, m=m, c=c, t0=self._current_time)
        self._current_time = seg.beat_to_time_seconds(b1)
        self._segments.append(seg)
        return self

    def add_lambda_by_beat(self, b0: float, b1: float, bpm_fn: Callable[[float], float]) -> TempoMapBuilder:
        self._validate_new_segment_bounds(b0, b1)
        seg = LambdaBpmByBeat(b0=b0, b1=b1, bpm_fn=bpm_fn, t0=self._current_time)

        # Validate bpm_fn > 0 across segment by sampling
        self._validate_lambda_positivity(seg)

        # Estimate end time by numeric integration of dt/db = 60/BPM(b)
        t_end = self._current_time + self._integrate_seconds_over_beats(seg, b0, b1)
        self._current_time = t_end
        self._segments.append(seg)
        return self

    def build(self, *, block_samples: int = 128, anchor_every_samples: int = 8192) -> TempoMap:
        tempo = TempoMap(
            segments=list(self._segments),
            sample_rate=self.sample_rate,
            block_samples=block_samples,
            anchor_every_samples=anchor_every_samples,
        )
        # Seed anchors at every segment boundary (important for lambda segments)
        tempo.ensure_boundary_anchors()
        return tempo

    # ---- internal helpers ----

    def _validate_new_segment_bounds(self, b0: float, b1: float) -> None:
        if b1 <= b0:
            raise ValueError("segment requires b1 > b0")
        if self._segments and b0 != self._segments[-1].b1:
            raise ValueError("segments must be contiguous (b0 must equal previous b1)")

    def _validate_lambda_positivity(self, seg: LambdaBpmByBeat) -> None:
        # sample a modest grid; you can make this stricter if you want
        steps = max(8, int((seg.b1 - seg.b0) / (self.lambda_step_beats * 16)))
        for i in range(steps + 1):
            b = seg.b0 + (seg.b1 - seg.b0) * (i / steps)
            bpm = float(seg.bpm_fn(b))
            if bpm <= 0 or not math.isfinite(bpm):
                raise ValueError("lambda tempo must be finite and > 0 across segment")

    def _integrate_seconds_over_beats(self, seg: LambdaBpmByBeat, b0: float, b1: float) -> float:
        # Simple midpoint integration in beat-domain:
        # seconds = ∫ 60/BPM(b) db
        h = self.lambda_step_beats
        total = 0.0
        b = b0
        while b < b1:
            step = min(h, b1 - b)
            mid = b + 0.5 * step
            bpm_mid = float(seg.bpm_fn(mid))
            total += (60.0 / bpm_mid) * step
            b += step
        return total


# ----------------------------
# Scheduling and retiming
# ----------------------------

def schedule_beats_to_samples(
    notes: BeatArrangement,
    tempo: TempoMap,
    *,
    round_mode: str = RoundMode.CEIL,
) -> ScheduledArrangement:
    tempo.round_mode = round_mode
    notes_sorted = sorted(notes, key=lambda n: (float(n.start), n.origin_id))
    out: List[ScheduledNote] = []
    for n in notes_sorted:
        s0 = tempo.beat_to_sample(n.start)
        s1 = tempo.beat_to_sample(n.start + n.dur)
        dur = max(0, s1 - s0)
        if dur == 0:
            continue
        out.append(ScheduledNote(s0, dur, n.pitch, n.vel, n.origin_id))
    return out


def retime_samples_to_beats(
    notes: ScheduledArrangement,
    tempo: TempoMap,
    *,
    grid: Fraction = Fraction(1, 16),
    quantize_mode: str = RoundMode.NEAREST,
    refine_inverse: bool = True,
    min_dur: Optional[Fraction] = Fraction(1, 256),
) -> BeatArrangement:
    out: List[BeatNote] = []
    notes_sorted = sorted(notes, key=lambda n: (n.start_sample, n.origin_id))

    # Reuse context/cache across all calls (important if bpm_mod_signal exists)
    ctx = EvalContext(sample_rate=tempo.sample_rate, block_size=tempo.block_samples)
    cache = ControlCache()

    for n in notes_sorted:
        b0 = tempo.sample_to_beat(n.start_sample, refine=refine_inverse, ctx=ctx, cache=cache)
        b1 = tempo.sample_to_beat(n.start_sample + n.dur_samples, refine=refine_inverse, ctx=ctx, cache=cache)

        qb0 = quantize_beat_float_to_fraction(b0, grid, quantize_mode)
        qb1 = quantize_beat_float_to_fraction(b1, grid, quantize_mode)

        dur = qb1 - qb0
        if dur <= 0:
            if min_dur is None or min_dur <= 0:
                continue
            dur = min_dur

        out.append(BeatNote(qb0, dur, n.pitch, n.vel, n.origin_id))

    out.sort(key=lambda n: (float(n.start), n.origin_id))
    return out

class SignalGraphError(ValueError):
    pass

@dataclass
class CompiledSignalGraph:
    """
    Immutable-ish compiled graph: topo order of all nodes reachable from roots.
    """
    roots: Dict[str, "Signal"]
    topo: List["Signal"]          # children come before parents
    nodes: Dict[int, "Signal"]    # id -> node

def compile_project_to_render_plan(
    project,
    *,
    sample_rate: int = 44100,
    block_size: int = 64,
    total_samples: int = 0,
    tempo_map: Optional["TempoMap"] = None,
):
    """
    Compile a project (Song) into a full render plan:

    1. Build TrackIR for each track (plugins + port mapping + event sources)
    2. Resolve project connect() edges by translating track ports -> plugin ports
    3. Insert gain/mixer nodes for fan-in
    4. Toposort plugin nodes for the renderer
    5. Run scheduler: gather notes/cc/param signals, convert to sample times, emit event lists

    Returns a ServerCommandPlan (from ..daw.project).
    """
    from ..daw.project import (
        compile_to_server_plan,
        TrackIR, PluginSpec,
    )

    # Build TrackIR per track.
    # Each track with processors gets compiled to a TrackIR describing its
    # internal plugin graph and published ports.
    track_irs: Dict[int, TrackIR] = {}

    tracks = project.tracks
    if isinstance(tracks, dict):
        track_items = list(tracks.items())
    else:
        track_items = [(i, t) for i, t in enumerate(tracks)]

    for tid, track in track_items:
        # Build a minimal TrackIR from the track's processor graph
        plugin_specs: Dict[int, PluginSpec] = {}
        internal_edges: List[tuple] = []
        published_in: Dict[str, List[tuple]] = {}
        published_out: Dict[str, List[tuple]] = {}

        # Collect processors
        proc_graph = getattr(track, 'processorGraph', None)
        if proc_graph is not None:
            if hasattr(proc_graph, 'processors'):
                for proc in proc_graph.processors:
                    plugin_specs[proc.key] = PluginSpec(
                        uid=proc.uid, key=proc.key,
                        kind="instrument" if getattr(proc, 'isInstrument', False) else "effect"
                    )
            if hasattr(proc_graph, 'connections'):
                for conn in proc_graph.connections:
                    internal_edges.append((
                        conn.processor1.key, conn.channel1,
                        conn.processor2.key, conn.channel2,
                    ))

        # If no processor graph, check for a simple processors list
        processors = getattr(track, 'processors', None)
        if processors and not plugin_specs:
            if isinstance(processors, dict):
                for key, proc in processors.items():
                    plugin_specs[proc.key] = PluginSpec(uid=proc.uid, key=proc.key, kind="effect")
            elif isinstance(processors, (list, set)):
                for proc in processors:
                    plugin_specs[proc.key] = PluginSpec(uid=proc.uid, key=proc.key, kind="effect")

        # Default published ports: first and last processors
        if plugin_specs:
            keys_sorted = sorted(plugin_specs.keys())
            first_key = keys_sorted[0]
            last_key = keys_sorted[-1]
            # stereo in/out by default
            published_in["in"] = [(first_key, 0), (first_key, 1)]
            published_out["out"] = [(last_key, 0), (last_key, 1)]

        track_irs[tid] = TrackIR(
            node_uids=list(plugin_specs.keys()),
            plugin_specs=plugin_specs,
            internal_audio_edges=internal_edges,
            published_in=published_in,
            published_out=published_out,
        )

    # Ensure project has beat_at_sample so EvalContext can use it
    if tempo_map is not None and not hasattr(project, 'beat_at_sample'):
        project.beat_at_sample = tempo_map.sample_to_beat

    # Use compile_to_server_plan which handles gain insertion, fan-in mixers,
    # connection resolution, and calls schedule_everything_else
    UID_GAIN = 0x4741494E

    plan = compile_to_server_plan(
        project, track_irs,
        UID_GAIN=UID_GAIN,
        remap_gain_channels=True,
        sample_rate=sample_rate,
        block_size=block_size,
        total_samples=total_samples,
    )

    return plan

class SignalEngine:
    def __init__(self, ctx: EvalContext, registry: SignalRegistry):
        self.ctx = ctx
        self.cache = ControlCache()
        self.registry = registry
        self._roots: Dict[str, Signal] = {}
        self._compiled: Optional[CompiledSignalGraph] = None
        self._last_prepared_block: Optional[int] = None
        self._snap_a_block: Optional[int] = None
        self._snap_a_vals: Optional[List[float]] = None
        self._snap_b_block: Optional[int] = None
        self._snap_b_vals: Optional[List[float]] = None

    def register_root(self, name: str, signal: Signal) -> None:
        if name in self._roots:
            raise SignalGraphError(f"Root '{name}' already registered")
        self._roots[name] = signal
        self._compiled = None

    def _resolve_refs(self, root: Signal) -> None:
        """
        Walk reachable nodes; bind any RefSignal using registry.
        """
        seen: Set[int] = set()
        stack: List[Signal] = [root]
        while stack:
            n = stack.pop()
            if n.id in seen:
                continue
            seen.add(n.id)

            if isinstance(n, RefSignal) and n._target is None:
                target = self.registry.get(n.key)
                n.bind(target)

            stack.extend(n.children())

    def _ensure_evaluator(self) -> None:
        _ = self.compiled  # ensure compiled
        if not hasattr(self, "_compiled_evaluator") or self._compiled_evaluator is None:
            # if you store it elsewhere, adjust accordingly
            self._compiled_evaluator = CompiledBlockEvaluator(self._compiled)

    def _snapshot_block(self, block_index: int) -> List[float]:
        """
        Returns a cached snapshot (copy) of evaluator.values for this block.
        Keeps up to two snapshots (simple 2-entry LRU).
        """
        self._ensure_evaluator()

        if self._snap_a_block == block_index and self._snap_a_vals is not None:
            return self._snap_a_vals
        if self._snap_b_block == block_index and self._snap_b_vals is not None:
            return self._snap_b_vals

        # Miss: evaluate block once, then copy values array
        self._compiled_evaluator.eval_block(block_index, self.ctx)
        vals = list(self._compiled_evaluator.values)  # snapshot copy

        # Insert/replace (2-entry LRU: push into A, demote old A into B)
        self._snap_b_block, self._snap_b_vals = self._snap_a_block, self._snap_a_vals
        self._snap_a_block, self._snap_a_vals = block_index, vals
        return vals

    def _sig_index(self, signal: Signal) -> int:
        self._ensure_evaluator()
        try:
            return self._compiled_evaluator.index[signal.id]
        except KeyError as e:
            raise SignalGraphError(
                "Signal not present in compiled graph (did you register it or a root that reaches it?)") from e

    def compile(self) -> CompiledSignalGraph:
        if not self._roots:
            raise SignalGraphError("No roots registered")

        # 0) Resolve RefSignals first so cross-track edges exist
        for sig in self._roots.values():
            self._resolve_refs(sig)

        # 1) Cross-track CSE (global canonicalization)
        canon = GlobalCanonicalizer()
        for name, sig in list(self._roots.items()):
            self._roots[name] = canon.canonicalize(sig)

        # 2) Cycle check on canonical roots
        for name, sig in self._roots.items():
            try:
                detect_signal_cycle(sig)
            except Exception as e:
                raise SignalGraphError(f"Cycle detected from root '{name}': {e}") from e

        # 3) Topological sort of all reachable nodes (canonical graph)
        visited: Set[int] = set()
        temp: Set[int] = set()
        topo: List[Signal] = []
        nodes: Dict[int, Signal] = {}

        def dfs(n: Signal) -> None:
            nid = n.id
            if nid in visited:
                return
            if nid in temp:
                raise SignalGraphError("Cycle detected during topo sort")
            temp.add(nid)
            for ch in n.children():
                dfs(ch)
            temp.remove(nid)
            visited.add(nid)
            topo.append(n)
            nodes[nid] = n

        for sig in self._roots.values():
            dfs(sig)

        self._compiled = CompiledSignalGraph(roots=dict(self._roots), topo=topo, nodes=nodes)
        self._compiled_evaluator = CompiledBlockEvaluator(self._compiled)
        self._last_prepared_block = None

        # Optional: keep stats for debugging/profiling
        self._last_cse_stats = canon.stats

        return self._compiled

    @property
    def compiled(self) -> CompiledSignalGraph:
        if self._compiled is None:
            return self.compile()
        return self._compiled

    def at(self, signal: Signal, sample: int) -> float:
        return signal.at(sample, self.ctx, self.cache)

    def prepare_range(self, start_sample: int, end_sample: int) -> None:
        """
        Optional: pre-warm blocks for an interval. Helpful for offline rendering.
        """
        if end_sample < start_sample:
            raise ValueError("end_sample must be >= start_sample")
        bs = self.ctx.block_size
        b0 = start_sample // bs
        b1 = end_sample // bs
        for b in range(b0, b1 + 1):
            self.prepare_block(b)

    def prepare_block(self, block_index: int) -> None: #optional prewarming of the cache
        _ = self._snapshot_block(block_index)  # fills snapshot cache

    def value_block(self, signal: Signal, block_index: int) -> float:
        vals = self._snapshot_block(block_index)
        idx = self._sig_index(signal)
        return float(vals[idx])

    def value(self, signal: Signal, sample: int) -> float:
        if sample < 0:
            raise ValueError("sample must be >= 0")

        bs = self.ctx.block_size
        b = sample // bs
        mid = b * bs + (bs // 2)

        # bracket by block midpoints for continuity
        if sample < mid:
            b0 = max(0, b - 1)
            b1 = b
            mid0 = b0 * bs + (bs // 2)
            mid1 = b1 * bs + (bs // 2)
        else:
            b0 = b
            b1 = b + 1
            mid0 = b0 * bs + (bs // 2)
            mid1 = b1 * bs + (bs // 2)

        vals0 = self._snapshot_block(b0)
        vals1 = self._snapshot_block(b1)
        idx = self._sig_index(signal)

        v0 = float(vals0[idx])
        v1 = float(vals1[idx])

        if mid1 == mid0:
            return v1

        t = (sample - mid0) / float(mid1 - mid0)
        if t <= 0.0:
            return v0
        if t >= 1.0:
            return v1
        return v0 * (1.0 - t) + v1 * t

class SignalRegistry:
    def __init__(self):
        self._exports: Dict[str, Signal] = {}

    def export(self, key: str, signal: Signal) -> None:
        if key in self._exports:
            raise ValueError(f"Signal '{key}' already exported")
        self._exports[key] = signal

    def get(self, key: str) -> Signal:
        try:
            return self._exports[key]
        except KeyError:
            raise KeyError(f"Unknown exported signal '{key}'") from None

    # --- new helpers ---

    def keys(self) -> List[str]:
        return sorted(self._exports.keys())

    def items(self) -> List[Tuple[str, Signal]]:
        return sorted(self._exports.items(), key=lambda kv: kv[0])

    def list(self, prefix: str = "") -> List[str]:
        """
        List exported keys, optionally filtered by prefix.
        Example prefixes:
          "A." or "A.out." or "Master.tempo."
        """
        if not prefix:
            return self.keys()
        return [k for k in self.keys() if k.startswith(prefix)]

    def describe(self, prefix: str = "") -> List[Tuple[str, int, str]]:
        """
        Debug view: (key, signal_id, signal_type)
        """
        out: List[Tuple[str, int, str]] = []
        for k in self.list(prefix):
            s = self._exports[k]
            out.append((k, s.id, type(s).__name__))
        return out

class SignalTrackHandle:
    """A named handle into a SignalRegistry, representing one track's exports."""

    def __init__(self, name: str, registry: SignalRegistry):
        self.name = name
        self.registry = registry

    def export(self, port: str, signal: Signal, *, category: str = "out") -> Signal:
        key = f"{self.name}.{category}.{port}"
        self.registry.export(key, signal)
        return signal

    def import_(self, key: str) -> RefSignal:
        return RefSignal(key)

    def sig(self, key: str) -> RefSignal:
        """
        Lazy reference. Errors (unknown key) appear at engine.compile() time.
        """
        return RefSignal(key)

    def sig_port(self, port: str, *, category: str = "out") -> RefSignal:
        """
        Reference a port on THIS track.
        """
        return RefSignal(f"{self.name}.{category}.{port}")

    def track(self, name: str) -> "SignalTrackHandle":
        return SignalTrackHandle(name, self.registry)

    def sig_checked(self, key: str) -> RefSignal:
        """
        Eager reference. Validates that the key exists right now.
        """
        self.registry.get(key)  # raises if unknown
        return RefSignal(key)

    def list_exports(self, prefix: str = "") -> List[str]:
        return self.registry.list(prefix)

    def describe_exports(self, prefix: str = "") -> List[Tuple[str, int, str]]:
        return self.registry.describe(prefix)

def sanity_check_tempo_map(
    tempo: TempoMap,
    *,
    start_sample: int = 0,
    end_sample: int = 48000 * 10,   # default: 10 seconds at 48kHz
    step_samples: int = 997,        # intentionally not a power of 2 (hits varied phases)
    refine_inverse: bool = True,
    max_err_samples: int = 256,     # tolerance for round-trip beat->sample after inversion
) -> None:
    """
    Practical checks:
      - sample_to_beat monotone
      - beat_to_sample(sample_to_beat(s)) close to s
      - local consistency

    Note:
      - Because beat_to_sample uses rounding (often CEIL), the round-trip won't be exact.
      - A bound like 64-256 samples is usually plenty with block_samples ~128.
    """
    if end_sample <= start_sample:
        raise ValueError("end_sample must be > start_sample")
    if step_samples <= 0:
        raise ValueError("step_samples must be > 0")

    last_beat = None
    worst_err = 0
    worst_s = None

    # We'll also check a local slope is positive by ensuring beat increases over steps.
    for s in range(start_sample, end_sample + 1, step_samples):
        b = tempo.sample_to_beat(s, refine=refine_inverse)
        if last_beat is not None and b < last_beat - 1e-12:
            raise AssertionError(
                f"Non-monotone sample_to_beat: at sample {s}, beat {b} < previous {last_beat}"
            )
        last_beat = b

        s2 = tempo.beat_to_sample(b)  # uses tempo.round_mode
        err = abs(s2 - s)
        if err > worst_err:
            worst_err = err
            worst_s = s

        if err > max_err_samples:
            raise AssertionError(
                f"Round-trip error too large: sample {s} -> beat {b:.9f} -> sample {s2}, "
                f"err={err} (max {max_err_samples}). Consider smaller block_samples, "
                f"or enable refine_inverse, or relax max_err_samples."
            )

    print(
        f"TempoMap sanity check OK: range [{start_sample}, {end_sample}] step {step_samples}. "
        f"Worst round-trip err={worst_err} samples at s={worst_s}."
    )

class CompiledBlockEvaluator:
    """
    Fast control-rate evaluator: computes all nodes once per block (at block midpoint),
    stores results in a dense array, then any number of consumers read from the array.

    This DOES NOT change user-facing signal construction.
    """
    def __init__(self, graph: CompiledSignalGraph):
        self.graph = graph
        self.index: Dict[int, int] = {n.id: i for i, n in enumerate(graph.topo)}
        self.values: List[float] = [0.0] * len(graph.topo)
        self.last_block: Optional[int] = None

    def _mid_sample(self, block: int, ctx: EvalContext) -> int:
        return block * ctx.block_size + (ctx.block_size // 2)

    def eval_block(self, block: int, ctx: EvalContext) -> None:
        if self.last_block == block:
            return

        s_mid = self._mid_sample(block, ctx)

        # Evaluate nodes in topo order; children already computed in self.values.
        for node in self.graph.topo:
            i = self.index[node.id]

            # ----- Leaves -----
            if isinstance(node, Const):
                self.values[i] = node.value
                continue

            if isinstance(node, TimeFn):
                t = s_mid / ctx.sample_rate
                v = float(node.fn(t))
                if not math.isfinite(v):
                    raise ValueError("Signal function returned non-finite value")
                self.values[i] = v
                continue

            if isinstance(node, BeatFn):
                if ctx.beat_at_sample is None:
                    raise ValueError("BeatFn requires ctx.beat_at_sample")
                b = float(ctx.beat_at_sample(s_mid))
                v = float(node.fn(b))
                if not math.isfinite(v):
                    raise ValueError("Signal function returned non-finite value")
                self.values[i] = v
                continue

            if isinstance(node, WavetableOverTime):
                t = s_mid / ctx.sample_rate
                u = (node.phase0 + (t / node.duration))
                u01 = _wrap01(u) if node.loop else _clamp01(u)
                self.values[i] = _wavetable_lookup(node.table, u01, node.interp)
                continue

            # ----- Unary/Binary expressions -----
            if isinstance(node, Add):
                ia = self.index[node.a.id]; ib = self.index[node.b.id]
                self.values[i] = self.values[ia] + self.values[ib]
                continue

            if isinstance(node, Sub):
                ia = self.index[node.a.id]; ib = self.index[node.b.id]
                self.values[i] = self.values[ia] - self.values[ib]
                continue

            if isinstance(node, Mul):
                ia = self.index[node.a.id]; ib = self.index[node.b.id]
                self.values[i] = self.values[ia] * self.values[ib]
                continue

            if isinstance(node, Neg):
                ix = self.index[node.x.id]
                self.values[i] = -self.values[ix]
                continue

            if isinstance(node, Clamp):
                ix = self.index[node.x.id]
                v = self.values[ix]
                if v < node.lo:
                    v = node.lo
                elif v > node.hi:
                    v = node.hi
                self.values[i] = v
                continue

            if isinstance(node, Rectify):
                ix = self.index[node.x.id]
                self.values[i] = abs(self.values[ix])
                continue

            if isinstance(node, Power):
                ix = self.index[node.x.id]
                v = self.values[ix]
                if node.unipolar:
                    if v <= 0.0:
                        self.values[i] = 0.0
                    elif v >= 1.0:
                        self.values[i] = 1.0
                    else:
                        self.values[i] = v ** node.gamma
                else:
                    if v == 0.0:
                        self.values[i] = 0.0
                    else:
                        self.values[i] = math.copysign(abs(v) ** node.gamma, v)
                continue

            if isinstance(node, MapRange):
                ix = self.index[node.x.id]
                x = self.values[ix]
                y = node.out_min + (x - node.in_min) * node.scale
                if node.clamp:
                    lo = min(node.out_min, node.out_max)
                    hi = max(node.out_min, node.out_max)
                    if y < lo:
                        y = lo
                    elif y > hi:
                        y = hi
                self.values[i] = y
                continue

            if isinstance(node, Mix):
                ia = self.index[node.a.id]
                ib = self.index[node.b.id]
                im = self.index[node.amount.id]
                a = self.values[ia]
                b = self.values[ib]
                m = self.values[im]
                self.values[i] = a * (1.0 - m) + b * m
                continue

            # ----- Special stateful control-rate nodes -----
            if isinstance(node, Smooth):
                # Use block-indexed cache in the node
                hit = node._smooth_cache.get(block)
                if hit is not None:
                    self.values[i] = hit
                    continue

                if block == 0:
                    # init from input at block 0 midpoint (already computed)
                    ix = self.index[node.x.id]
                    x0 = float(self.values[ix])
                    node._smooth_cache.set(0, x0)
                    self.values[i] = x0
                    continue

                prev = node._smooth_cache.get(block - 1)
                if prev is None:
                    # compute previous block first by forcing recursion
                    # (safe because it's only one step backwards)
                    self.eval_block(block - 1, ctx)
                    prev = node._smooth_cache.get(block - 1)
                    if prev is None:
                        raise RuntimeError("Smooth cache failed to populate previous block")

                ix = self.index[node.x.id]
                xk = float(self.values[ix])
                a = node._alpha(ctx)
                yk = prev + a * (xk - prev)
                node._smooth_cache.set(block, yk)
                self.values[i] = yk
                continue

            if isinstance(node, WavetableOsc):
                # control-rate oscillator: one sample per block, with phase accumulation
                # We compute phase at this block start using the oscillator's own cache.
                phase = node._phase_at_block_start(block, ctx, cache=None)  # it calls freq.at; we want fast path
                # To avoid node.freq.at recursion, we approximate freq from the already-computed value:
                # If freq is a Const, node._phase_at_block_start is exact.
                # If not Const, integrate using already-evaluated freq at each needed block.
                if not isinstance(node.freq, Const):
                    # Recompute phase using evaluator values (fast) rather than freq.at()
                    b0, ph0 = node._phase_cache.get_nearest_leq(block)
                    ph = ph0
                    dt = ctx.block_size / ctx.sample_rate
                    for bi in range(b0, block):
                        # midpoint sample corresponds to that block; use evaluator value of freq at that block
                        # Ensure we've evaluated that block (we're in it); earlier blocks may not be.
                        if bi != block:
                            self.eval_block(bi, ctx)
                        f = self.value_of(node.freq)  # value at current evaluator's last_block
                        ph += float(f) * dt
                        if node._cache_every_blocks > 0 and ((bi + 1) % node._cache_every_blocks == 0):
                            node._phase_cache.set(bi + 1, ph)
                    node._phase_cache.set(block, ph)
                    phase = ph

                phase01 = _wrap01(phase) if node.loop else _clamp01(phase)
                self.values[i] = _wavetable_lookup(node.table, phase01, node.interp)
                continue

            if isinstance(node, RefSignal):
                # After resolution it has a target. Identity.
                it = self.index[node.target.id]
                self.values[i] = self.values[it]
                continue

            # ----- Fallback -----
            # Anything not specialized: use slow path at midpoint sample.
            self.values[i] = float(node.at(s_mid, ctx, cache=None))

        self.last_block = block

    def value_of(self, signal: Signal) -> float:
        return self.values[self.index[signal.id]]

    def root_value(self, root_name: str) -> float:
        sig = self.graph.roots[root_name]
        return self.value_of(sig)

