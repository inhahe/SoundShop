#todo: make signals nodes in a graph
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Union, Sequence, Tuple, Set, List, Protocol
from fractions import Fraction
import bisect
import math

Number = Union[int, float]

# ----------------------------
# Render context + block cache
# ----------------------------

@dataclass(frozen=True)
class EvalContext:
    sample_rate: int
    block_size: int = 128
    # Optional: if you want beat-aware signals later
    beat_at_sample: Optional[Callable[[int], float]] = None

class BlockCache:
    """
    Cache values per (signal_id, block_index).
    The assumption is you evaluate signals at block boundaries or treat values as piecewise-constant per block.
    """
    def __init__(self):
        self._cache: Dict[tuple[int, int], float] = {}

    def get(self, signal_id: int, block_index: int) -> Optional[float]:
        return self._cache.get((signal_id, block_index))

    def set(self, signal_id: int, block_index: int, value: float) -> None:
        self._cache[(signal_id, block_index)] = value


# ----------------------------
# Signal base class
# ----------------------------

class Signal:
    """
    A signal is a pure function sampled at (sample, ctx).
    By default we cache at block granularity: block = sample // ctx.block_size.
    """

    _next_id = 1

    def __init__(self):
        self._id = Signal._next_id
        Signal._next_id += 1

    @property
    def requires_beat(self) -> bool:
        return False

    @property
    def id(self) -> int:
        return self._id

    def at(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache] = None) -> float:
        """
        Evaluate signal at 'sample'. Default caching is per-block.
        """
        if sample < 0:
            raise ValueError("sample must be >= 0")

        block = sample // ctx.block_size
        if cache is not None:
            hit = cache.get(self._id, block)
            if hit is not None:
                return hit

        v = float(self._eval(sample, ctx, cache))
        if cache is not None:
            cache.set(self._id, block, v)
        return v

    def children(self) -> tuple["Signal", ...]:
        # Leaf signals override with ().
        return ()

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        raise NotImplementedError

    # ---- operator overloads ----

    def __add__(self, other: Union[Signal, Number]) -> Signal:
        return Add(self, as_signal(other))

    def __radd__(self, other: Number) -> Signal:
        return Add(as_signal(other), self)

    def __sub__(self, other: Union[Signal, Number]) -> Signal:
        return Sub(self, as_signal(other))

    def __rsub__(self, other: Number) -> Signal:
        return Sub(as_signal(other), self)

    def __mul__(self, other: Union[Signal, Number]) -> Signal:
        return Mul(self, as_signal(other))

    def __rmul__(self, other: Number) -> Signal:
        return Mul(as_signal(other), self)

    def __neg__(self) -> Signal:
        return Neg(self)

def as_signal(x: Union[Signal, Number]) -> Signal:
    return x if isinstance(x, Signal) else Const(float(x))


# ----------------------------
# Concrete signals
# ----------------------------

class Const(Signal):
    def __init__(self, value: float):
        super().__init__()
        self.value = float(value)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.value

    def children(self) -> tuple["Signal", ...]:
        return ()

class TimeFn(Signal):
    """
    Wrap an arbitrary user function f(t_seconds) -> float
    """
    def __init__(self, fn: Callable[[float], float]):
        super().__init__()
        self.fn = fn

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        t = sample / ctx.sample_rate
        v = float(self.fn(t))
        if not math.isfinite(v):
            raise ValueError("Signal function returned non-finite value")
        return v

    def children(self) -> tuple["Signal", ...]:
        return ()

class BeatFn(Signal):
    """
    Wrap f(beat) -> float, using ctx.beat_at_sample(sample) supplied by your scheduler/tempo map.
    """
    def __init__(self, fn: Callable[[float], float]):
        super().__init__()
        self.fn = fn

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        if ctx.beat_at_sample is None:
            raise ValueError("BeatFn requires ctx.beat_at_sample")
        b = float(ctx.beat_at_sample(sample))
        v = float(self.fn(b))
        if not math.isfinite(v):
            raise ValueError("Signal function returned non-finite value")
        return v

    @property
    def requires_beat(self) -> bool:
        return True

    def children(self) -> tuple["Signal", ...]:
        return ()

# ----------------------------
# Expression nodes
# ----------------------------

class BinaryOp(Signal):
    def __init__(self, a: Signal, b: Signal):
        super().__init__()
        self.a = a
        self.b = b

    @property
    def requires_beat(self) -> bool:
        return self.a.requires_beat or self.b.requires_beat

    def children(self) -> tuple["Signal", ...]:
        return (self.a, self.b)

class Add(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.a.at(sample, ctx, cache) + self.b.at(sample, ctx, cache)

class Sub(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.a.at(sample, ctx, cache) - self.b.at(sample, ctx, cache)

class Mul(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.a.at(sample, ctx, cache) * self.b.at(sample, ctx, cache)

class Neg(Signal):
    def __init__(self, x: Signal):
        super().__init__()
        self.x = x

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return -self.x.at(sample, ctx, cache)

    @property
    def requires_beat(self) -> bool:
        return self.x.requires_beat

    def children(self) -> tuple["Signal", ...]:
        return (self.x,)
# ----------------------------
# Example: define a shared curve and use it for multiple params
# ----------------------------

if __name__ == "__main__":
    ctx = EvalContext(sample_rate=48000, block_size=128)
    cache = BlockCache()

    # shared arbitrary curve: "vibe"
    vibe = TimeFn(lambda t: math.sin(2 * math.pi * 5.0 * t))  # 5 Hz sine

    base_tempo = Const(120.0)
    tempo = base_tempo + 20.0 * vibe
    microtune = 7.0 * vibe
    cutoff = 2000.0 + 500.0 * vibe

    s = 1024  # sample index

    # These will share cached vibe evaluation at the same block.
    print("tempo:", tempo.at(s, ctx, cache))
    print("microtune:", microtune.at(s, ctx, cache))
    print("cutoff:", cutoff.at(s, ctx, cache))

class InterpMode:
    NEAREST = "nearest"
    LINEAR = "linear"

def _wrap01(x: float) -> float:
    return x - math.floor(x)

def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)

def _wavetable_lookup(table: Sequence[float], phase01: float, interp: str) -> float:
    n = len(table)
    if n == 0:
        raise ValueError("Wavetable cannot be empty")
    if n == 1:
        return float(table[0])

    pos = phase01 * n
    if interp == InterpMode.NEAREST:
        i = int(pos) % n
        return float(table[i])

    if interp == InterpMode.LINEAR:
        i0 = int(pos)
        frac = pos - i0
        i0 = i0 % n
        i1 = (i0 + 1) % n
        return float(table[i0]) * (1.0 - frac) + float(table[i1]) * frac

    raise ValueError(f"Unknown interp mode: {interp}")

@dataclass
class PhaseCache:
    """
    Stores phase at block boundaries for a specific oscillator.
    Keyed by block_index -> phase (float cycles, not wrapped).
    """
    phase_by_block: Dict[int, float] = field(default_factory=dict)

    def get_nearest_leq(self, block_index: int) -> Tuple[int, float]:
        """
        Return (b, phase_b) for the greatest cached b <= block_index.
        If none exist, returns (0, phase0) must be handled by caller by seeding block 0.
        """
        if not self.phase_by_block:
            raise ValueError("PhaseCache is empty (seed block 0 first)")
        # dict isn't ordered; do a small search. If you want faster, store sorted keys too.
        # Typically cache is dense, so this is fine; you can optimize later.
        candidates = [b for b in self.phase_by_block.keys() if b <= block_index]
        if not candidates:
            raise ValueError("No cached phase <= requested block")
        b = max(candidates)
        return b, self.phase_by_block[b]

    def set(self, block_index: int, phase: float) -> None:
        self.phase_by_block[block_index] = phase

class WavetableOsc(Signal):
    """
    Wavetable oscillator with optional time-varying frequency.
    Phase units: cycles (1.0 = one full wrap).
    """
    def __init__(
        self,
        table: Sequence[float],
        freq_hz: Union[Signal, Number],
        *,
        phase0: float = 0.0,
        interp: str = InterpMode.LINEAR,
        loop: bool = True,
        cache_every_blocks: int = 64,  # drop phase anchors every N blocks
    ):
        super().__init__()
        self.table = list(map(float, table))
        if not self.table:
            raise ValueError("WavetableOsc table cannot be empty")

        self.freq = as_signal(freq_hz)
        self.phase0 = float(phase0)
        self.interp = interp
        self.loop = loop

        self._phase_cache = PhaseCache()
        self._phase_cache.set(0, self.phase0)
        self._cache_every_blocks = int(cache_every_blocks)

    def _phase_at_block_start(self, block: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        # Constant freq: exact
        if isinstance(self.freq, Const):
            tb = (block * ctx.block_size) / ctx.sample_rate
            return self.phase0 + tb * self.freq.value

        # Variable freq: use phase cache anchors
        # Find nearest cached block <= block
        b0, phase0 = self._phase_cache.get_nearest_leq(block)
        phase = phase0

        dt = ctx.block_size / ctx.sample_rate

        # Integrate b0 .. block-1
        for bi in range(b0, block):
            # midpoint sample for this block
            s_mid = bi * ctx.block_size + (ctx.block_size // 2)
            f_mid = float(self.freq.at(s_mid, ctx, cache))
            phase += f_mid * dt

            # periodically cache
            if self._cache_every_blocks > 0 and ((bi + 1) % self._cache_every_blocks == 0):
                self._phase_cache.set(bi + 1, phase)

        # Always cache the requested block (helps random access)
        self._phase_cache.set(block, phase)
        return phase

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        block = sample // ctx.block_size
        phase = self._phase_at_block_start(block, ctx, cache)
        phase01 = _wrap01(phase) if self.loop else _clamp01(phase)
        return _wavetable_lookup(self.table, phase01, self.interp)

    def children(self) -> tuple["Signal", ...]:
        return (self.freq,)

    """
    Treat the table as a curve over normalized time u in [0,1] (or looping).
    Useful for arbitrary automation/LFO shapes without trig.

    Examples:
      - one-shot envelope-like curve: loop=False, duration_seconds=2.0
      - looping LFO: loop=True, period_seconds=0.5
    """

class WavetableOverTime(Signal):
    """
    Treat the table as a curve over normalized time u in [0,1] (or looping).
    Useful for arbitrary automation/LFO shapes without trig.

    Examples:
      - one-shot envelope-like curve: loop=False, duration_seconds=2.0
      - looping LFO: loop=True, period_seconds=0.5
    """

    def __init__(
        self,
        table: Sequence[float],
        *,
        duration_seconds: float,
        phase0: float = 0.0,
        interp: str = InterpMode.LINEAR,
        loop: bool = False,
    ):
        super().__init__()
        self.table = list(map(float, table))
        if len(self.table) == 0:
            raise ValueError("WavetableOverTime table cannot be empty")
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be > 0")
        self.duration = float(duration_seconds)
        self.phase0 = float(phase0)
        self.interp = interp
        self.loop = loop

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        t = sample / ctx.sample_rate
        u = (self.phase0 + (t / self.duration))
        u01 = _wrap01(u) if self.loop else _clamp01(u)
        return _wavetable_lookup(self.table, u01, self.interp)

    def children(self) -> tuple["Signal", ...]:
        return ()

class SignalCycleError(ValueError):
    pass

def detect_signal_cycle(root: Signal) -> None:
    """
    Raises SignalCycleError if a cycle is reachable from root.
    Otherwise returns None.
    """
    # 0 = unvisited, 1 = visiting (in stack), 2 = done
    color: Dict[int, int] = {}
    stack: List[int] = []

    def dfs(node: Signal):
        nid = node.id
        c = color.get(nid, 0)
        if c == 1:
            # cycle: node is already in the current recursion stack
            # Build a readable cycle path from stack
            cycle_start = stack.index(nid) if nid in stack else 0
            cycle = stack[cycle_start:] + [nid]
            raise SignalCycleError(f"Cycle detected in Signal graph (ids): {cycle}")
        if c == 2:
            return

        color[nid] = 1
        stack.append(nid)

        for ch in node.children():
            dfs(ch)

        stack.pop()
        color[nid] = 2

    dfs(root)

def signal_depends_on_beat(root: Signal) -> bool:
    """
    Returns True if any BeatFn node is reachable from root.
    """
    seen: Set[int] = set()
    stack: List[Signal] = [root]

    while stack:
        n = stack.pop()
        if n.id in seen:
            continue
        seen.add(n.id)
        if isinstance(n, BeatFn):
            return True
        stack.extend(n.children())
    return False

"""
Example usage
A) Classic LFO from a wavetable (looping)
# One cycle wavetable (e.g., triangle)
tri = [0.0, 0.5, 1.0, 0.5]  # toy example; real tables have 256/1024 samples

vibe = WavetableOsc(tri, freq_hz=5.0, interp=InterpMode.LINEAR, loop=True)

tempo = 120 + 20 * vibe
microtune = 7 * vibe
B) Arbitrary curve as a “shape over time” (one-shot or looping)
# A user-drawn automation curve over 2 seconds
curve = [0.0, 0.2, 0.9, 0.4, 0.8, 0.0]

vibe = WavetableOverTime(curve, duration_seconds=2.0, loop=False)
cutoff = 2000 + 500 * vibe
"""

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

    def _bpm_effective(self, beat_est: float, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
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

    def beat_to_sample(self, beat: Union[Fraction, float], *, ctx: Optional[EvalContext]=None, cache: Optional[BlockCache]=None) -> int:
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
            cache = BlockCache()

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
            cache: Optional[BlockCache] = None,
    ) -> float:
        if sample < 0:
            raise ValueError("sample must be >= 0")

        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if cache is None:
            cache = BlockCache()

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

    def _beat_to_sample_with_modulation(self, b_target: float, ctx: EvalContext, cache: BlockCache) -> int:
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

    def ensure_boundary_anchors(self, ctx: Optional[EvalContext] = None, cache: Optional[BlockCache] = None) -> None:
        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if cache is None:
            cache = BlockCache()
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
    cache = BlockCache()

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


class SignalEngine:
    def __init__(self, ctx: EvalContext, registry: SignalRegistry):
        self.ctx = ctx
        self.cache = BlockCache()
        self.registry = registry
        self._roots: Dict[str, Signal] = {}
        self._compiled: Optional[CompiledSignalGraph] = None
        self._last_prepared_block: Optional[int] = None

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

    def compile(self) -> CompiledSignalGraph:
        if not self._roots:
            raise SignalGraphError("No roots registered")

        # 0) Resolve RefSignals first (so cycle detection + topo includes edges)
        for sig in self._roots.values():
            self._resolve_refs(sig)

        # 1) Cycle check per root
        for name, sig in self._roots.items():
            try:
                detect_signal_cycle(sig)
            except Exception as e:
                raise SignalGraphError(f"Cycle detected from root '{name}': {e}") from e

        # 2) Topo sort of all reachable nodes
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
        self._last_prepared_block = None
        return self._compiled

    @property
    def compiled(self) -> CompiledSignalGraph:
        if self._compiled is None:
            return self.compile()
        return self._compiled

    def at(self, signal: Signal, sample: int) -> float:
        return signal.at(sample, self.ctx, self.cache)

    def prepare_block(self, block_index: int) -> None:
        _ = self.compiled
        if self._last_prepared_block == block_index:
            return
        block_start = block_index * self.ctx.block_size
        sample = block_start + (self.ctx.block_size // 2)
        for node in self._compiled.topo:
            node.at(sample, self.ctx, self.cache)
        self._last_prepared_block = block_index
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

class RefSignal(Signal):
    """
    A placeholder node that points at a named signal exported elsewhere.
    It becomes a real edge in the signal graph after resolution.
    """
    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self._target: Optional[Signal] = None

    def bind(self, target: Signal) -> None:
        self._target = target

    @property
    def target(self) -> Signal:
        if self._target is None:
            raise ValueError(f"Unresolved RefSignal('{self.key}')")
        return self._target

    @property
    def requires_beat(self) -> bool:
        # Once bound, inherit
        return self._target.requires_beat if self._target is not None else False

    def children(self) -> tuple["Signal", ...]:
        # Once bound, the ref becomes an edge to its target.
        return (self._target,) if self._target is not None else ()

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        # Delegate evaluation to target
        return self.target.at(sample, ctx, cache)

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

class Track:
    def __init__(self, name: str, registry: SignalRegistry):
        self.name = name
        self.registry = registry

    def export(self, port: str, signal: Signal, *, category: str = "out") -> Signal:
        """
        Export under 'Track.category.port', default category='out'.
        """
        key = f"{self.name}.{category}.{port}"
        self.registry.export(key, signal)
        return signal

    def import_(self, key: str) -> RefSignal:
        return RefSignal(key)

    def sig(self, port: str, *, category: str = "out") -> RefSignal:
        """
        Reference a port on THIS track.
        """
        return RefSignal(f"{self.name}.{category}.{port}")

class Project:
    def __init__(self, ctx: EvalContext):
        self.ctx = ctx
        self.registry = SignalRegistry()
        self.engine = SignalEngine(ctx, registry=self.registry)

    def track(self, name: str) -> "Track":
        return Track(name, self.registry)

    def sig(self, key: str) -> RefSignal:
        """
        Lazy reference. Errors (unknown key) appear at engine.compile() time.
        """
        return RefSignal(key)

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

class UnaryOp(Signal):
    def __init__(self, x: Signal):
        super().__init__()
        self.x = x

    @property
    def requires_beat(self) -> bool:
        return self.x.requires_beat

    def children(self) -> tuple["Signal", ...]:
        return (self.x,)

class Clamp(UnaryOp):
    def __init__(self, x: Signal, lo: Number, hi: Number):
        super().__init__(x)
        self.lo = float(lo)
        self.hi = float(hi)
        if self.hi < self.lo:
            raise ValueError("Clamp requires hi >= lo")

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        v = float(self.x.at(sample, ctx, cache))
        if v < self.lo:
            return self.lo
        if v > self.hi:
            return self.hi
        return v
"""
Usage:
Usage:

vibe = proj.sig("A.mod.vibe")
safe = Clamp(vibe, -1.0, 1.0)
cutoff = 2000 + 500 * safe
"""

@dataclass
class SmoothCache:
    """
    Per Smooth node cache: block_index -> smoothed_value.
    Also stores the last computed block for faster forward stepping.
    """
    values: Dict[int, float] = field(default_factory=dict)

    def get(self, block: int) -> Optional[float]:
        return self.values.get(block)

    def set(self, block: int, value: float) -> None:
        self.values[block] = value

class Smooth(UnaryOp):
# This is the “DAW style” smoothing you typically see on modulation sources to avoid zipper noise.

    """
    One-pole smoothing at control rate (per block).

    time_constant_s:
      - larger => more smoothing (slower response)
      - 0 => no smoothing
    """
    def __init__(self, x: Signal, time_constant_s: float):
        super().__init__(x)
        if time_constant_s < 0:
            raise ValueError("time_constant_s must be >= 0")
        self.tau = float(time_constant_s)
        self._smooth_cache = SmoothCache()

    def _alpha(self, ctx: EvalContext) -> float:
        # discrete one-pole coefficient at control rate
        dt = ctx.block_size / ctx.sample_rate
        if self.tau <= 0:
            return 1.0
        # alpha = 1 - exp(-dt/tau)
        return 1.0 - math.exp(-dt / self.tau)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        block = sample // ctx.block_size

        hit = self._smooth_cache.get(block)
        if hit is not None:
            return hit

        # Ensure previous block is computed (recurrence)
        if block == 0:
            # initialize from input at block 0 midpoint
            s_mid = (ctx.block_size // 2)
            x0 = float(self.x.at(s_mid, ctx, cache))
            self._smooth_cache.set(0, x0)
            return x0

        prev = self._smooth_cache.get(block - 1)
        if prev is None:
            # Compute previous block first (recursive fill)
            prev = self._eval((block - 1) * ctx.block_size, ctx, cache)

        # Current input sample at block midpoint
        s_mid = block * ctx.block_size + (ctx.block_size // 2)
        xk = float(self.x.at(s_mid, ctx, cache))

        a = self._alpha(ctx)
        yk = prev + a * (xk - prev)

        self._smooth_cache.set(block, yk)
        return yk
"""
Why Smooth has its own cache (not BlockCache)

BlockCache caches a signal’s value per block, but smoothing needs the previous output to compute the next output. That’s a recurrence, so it needs a per-node history cache.

This is still deterministic because:

the output at block k is fully determined by:

the input samples at each block midpoint up to k

the time constant

no hidden time progression outside the cache
"""

"""
Usage
raw = proj.sig("A.mod.vibe")

# Smooth over ~50 ms
smooth = Smooth(raw, time_constant_s=0.05)

# Then clamp to a safe range
mapped = Clamp(0.8 * smooth + 0.2, 0.0, 1.0)

cutoff = 500 + 3000 * mapped
"""

class Rectify(UnaryOp):
    """
    Full-wave rectifier: abs(x)
    Common for turning an LFO into a unipolar modulation source.
    """
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return abs(float(self.x.at(sample, ctx, cache)))
"""
Rectify(x) — full-wave rectifier (abs(x))
Usage:

raw = proj.sig("A.mod.vibe")          # e.g. -1..1
uni = Rectify(raw)                    # 0..1
cutoff = 500 + 3000 * Clamp(uni, 0, 1)
"""

class Power(UnaryOp):
    """
    Curve shaping: y = sign(x) * |x|**gamma by default.
    If unipolar=True, assumes x in [0,1] and does y = clamp(x,0,1)**gamma.

    Typical use:
      - gamma > 1: ease-in (less sensitive near 0)
      - gamma < 1: ease-out (more sensitive near 0)
    """
    def __init__(self, x: Signal, gamma: float, *, unipolar: bool = True):
        super().__init__(x)
        if gamma <= 0 or not math.isfinite(gamma):
            raise ValueError("gamma must be finite and > 0")
        self.gamma = float(gamma)
        self.unipolar = bool(unipolar)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        v = float(self.x.at(sample, ctx, cache))

        if self.unipolar:
            # Most DAW "curve" mappings are for 0..1
            if v <= 0.0:
                return 0.0
            if v >= 1.0:
                return 1.0
            return v ** self.gamma

        # Bipolar shaping preserves sign
        if v == 0.0:
            return 0.0
        return math.copysign(abs(v) ** self.gamma, v)
"""
Power(x, gamma) — curve shaping (like “modulation curve” / exponential response)

Make a unipolar LFO that “hangs near 0” then rises sharply
raw = proj.sig("A.mod.vibe")
uni = Clamp(Rectify(raw), 0, 1)
shaped = Power(uni, gamma=3.0, unipolar=True)
cutoff = 500 + 3000 * shaped

Gentle bipolar softening (less extreme near ±1)
raw = proj.sig("A.mod.vibe")          # -1..1
soft = Power(raw, gamma=0.7, unipolar=False)
pan = 0.8 * soft
"""

class MapRange(UnaryOp):
    """
    Map input range to output range.

    y = out_min + (x - in_min) * (out_max - out_min) / (in_max - in_min)

    If clamp=True, output is clamped to [out_min, out_max].
    """

    def __init__(
        self,
        x: Signal,
        in_min: float,
        in_max: float,
        out_min: float,
        out_max: float,
        *,
        clamp: bool = False,
    ):
        super().__init__(x)

        if in_max == in_min:
            raise ValueError("in_max must differ from in_min")

        self.in_min = float(in_min)
        self.in_max = float(in_max)
        self.out_min = float(out_min)
        self.out_max = float(out_max)
        self.clamp = clamp

        self.scale = (self.out_max - self.out_min) / (self.in_max - self.in_min)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        x = float(self.x.at(sample, ctx, cache))
        y = self.out_min + (x - self.in_min) * self.scale

        if self.clamp:
            lo = min(self.out_min, self.out_max)
            hi = max(self.out_min, self.out_max)
            if y < lo:
                return lo
            if y > hi:
                return hi

        return y

"""
Bipolar LFO → filter cutoff
vibe = proj.sig("A.mod.vibe")  # -1..1

cutoff = map_range(vibe, -1, 1, 200, 4000)

Unipolar envelope → gain
env = proj.sig("Drums.mod.env")  # 0..1

gain = map_range(env, 0, 1, -12, 0)
MIDI velocity → filter
velocity = proj.sig("Synth.note.velocity")  # 0..127

cutoff = map_range(velocity, 0, 127, 500, 6000)
Clamp input automatically
cutoff = map_range(vibe, -1, 1, 200, 4000, clamp=True)

Most modulation tasks are:

LFO → parameter
velocity → filter
envelope → gain
macro knob → multiple parameters

All of those are range mappings.
So this single node removes a lot of graph clutter.
"""

class Mix(Signal):
    """
    Linear interpolation between two signals.

    y = a*(1-m) + b*m
    """

    def __init__(self, a: Signal, b: Signal, amount: Union[Signal, Number]):
        super().__init__()
        self.a = a
        self.b = b
        self.amount = as_signal(amount)

    @property
    def requires_beat(self) -> bool:
        return (
            self.a.requires_beat
            or self.b.requires_beat
            or self.amount.requires_beat
        )

    def children(self) -> tuple["Signal", ...]:
        return (self.a, self.b, self.amount)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        a = float(self.a.at(sample, ctx, cache))
        b = float(self.b.at(sample, ctx, cache))
        m = float(self.amount.at(sample, ctx, cache))

        return a * (1.0 - m) + b * m
"""
Crossfade functions:
crossfade
morph
wet/dry
macro blending between two curves

Example usages
Crossfade two LFOs
I don't think this will actually work for my system because this entire file is scheduling-time and this would have to be done in real-time rendering time in C++
lfo1 = WavetableOsc(table1, freq_hz=1)
lfo2 = WavetableOsc(table2, freq_hz=1)
macro = proj.sig("A.macro.1")
vibe = mix(lfo1, lfo2, macro)

Blend automation and LFO
automation = proj.sig("TrackA.auto.cutoff")
lfo = proj.sig("TrackA.mod.vibe")
cutoff_mod = mix(automation, lfo, 0.3)
30% LFO added to automation.

Dry/Wet
dry = Const(0)
wet = proj.sig("FX.reverb.amount")
mix_amt = proj.sig("FX.macro.reverb")
reverb_amount = mix(dry, wet, mix_amt)



"""
