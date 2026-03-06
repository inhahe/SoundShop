from __future__ import annotations

import hashlib, math, struct
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Union, Sequence, Tuple, Set, List, Any

Number = Union[int, float]
# ----------------------------
# Render context + block cache
# ----------------------------

@dataclass(frozen=True)
class MidiNoteEvent:
    sample: int
    on: bool
    channel: int
    pitch: int
    velocity: int
    note_id: int  # optional stable id

@dataclass(frozen=True)
class MidiCCEvent:
    sample: int
    channel: int
    cc: int
    value: int  # 0..127

@dataclass(frozen=True)
class ParamEvent:
    sample: int
    node_id: int     # plugin node in graph
    param_id: int    # VST3 param id (or your own)
    value: float     # normalized or real, your choice

@dataclass(frozen=True)
class EvalContext:
    sample_rate: int
    block_size: int = 128
    # Optional: if you want beat-aware signals later
    beat_at_sample: Optional[Callable[[int], float]] = None

@dataclass(frozen=True)
class PluginNode:
    node_id: int
    vst3_uid: str            # or (plugin_path, class_id)
    name: str
    kind: str                # "instrument" | "effect"
    # plus initial param state, program, etc.

@dataclass(frozen=True)
class PluginEdge:
    src_node: int
    src_port: str            # "audio.out:0", "midi.out", etc.
    dst_node: int
    dst_port: str
    kind: str                # "audio" | "midi"
    gain: float = 1.0        # optional

@dataclass
class RenderPlan:
    nodes: list[PluginNode]
    edges: list[PluginEdge]
    notes: list[MidiNoteEvent]
    ccs: list[MidiCCEvent]
    params: list[ParamEvent]

class ControlCache:
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


def _rebuild_node(n: Signal, ch: tuple[Signal, ...]) -> Signal:
    # folds identically defined nodes into one to save computation time

    # Leaves: return the same node (safe) except we might want new Consts
    if isinstance(n, Const):
        return Const(n.value)
    if isinstance(n, TimeFn):
        return n
    if isinstance(n, BeatFn):
        return n
    if isinstance(n, RefSignal):
        # keep same ref node instance (it may already be bound)
        return n

    # Ops

    if isinstance(n, Add):
        if isinstance(ch[0], Const) and isinstance(ch[1], Const):
            return Const(ch[0].value + ch[1].value)
        return Add(ch[0], ch[1])
    if isinstance(n, Sub):
        return Sub(ch[0], ch[1])
    # --- Mul(Const, Const) ---
    if isinstance(n, Mul):
        if isinstance(ch[0], Const) and isinstance(ch[1], Const):
            return Const(ch[0].value * ch[1].value)
        return Mul(ch[0], ch[1])
    # --- Neg(Const) ---
    if isinstance(n, Neg):
        if isinstance(ch[0], Const):
            return Const(-ch[0].value)
        return Neg(ch[0])
    # --- Clamp(Const) ---
    if isinstance(n, Clamp):
        if isinstance(ch[0], Const):
            v = ch[0].value
            if v < n.lo:
                v = n.lo
            elif v > n.hi:
                v = n.hi
            return Const(v)
        return Clamp(ch[0], n.lo, n.hi)
    # Modifiers
    if isinstance(n, Rectify):
        return Rectify(ch[0])
    if isinstance(n, Power):
        return Power(ch[0], n.gamma, unipolar=n.unipolar)
        # --- MapRange(Const) ---
    if isinstance(n, MapRange):
        if isinstance(ch[0], Const):
            x = ch[0].value
            y = n.out_min + (x - n.in_min) * n.scale
            if n.clamp:
                lo = min(n.out_min, n.out_max)
                hi = max(n.out_min, n.out_max)
                if y < lo:
                    y = lo
                elif y > hi:
                    y = hi
            return Const(y)
        # IMPORTANT: rebuild MapRange so its 'scale' is consistent with ctor
        return MapRange(ch[0], n.in_min, n.in_max, n.out_min, n.out_max, clamp=n.clamp)
    if isinstance(n, Mix):
        return Mix(ch[0], ch[1], ch[2])

    # Wavetables
    if isinstance(n, WavetableOverTime):
        return WavetableOverTime(
            n.table,
            duration_seconds=n.duration,
            phase0=n.phase0,
            interp=n.interp,
            loop=n.loop,
        )
    if isinstance(n, WavetableOsc):
        # Only safe to rebuild (and thus dedupe) if freq is Const
        if isinstance(n.freq, Const):
            return WavetableOsc(
                n.table,
                ch[0],  # freq signal (will be Const)
                phase0=n.phase0,
                interp=n.interp,
                loop=n.loop,
                cache_every_blocks=n._cache_every_blocks,
            )
        return n  # keep identity for variable freq

    # Stateful nodes: keep as-is
    if isinstance(n, Smooth):
        return n

    return n

# ----------------------------
# Signal base class
# ----------------------------

@dataclass
class CanonStats:
    visited: int = 0
    replaced: int = 0
    created: int = 0

def _node_key(node: Signal, child_ids: Tuple[int, ...]) -> Tuple[Any, ...]:
    t = type(node)
    if isinstance(node, Const):
        return (t, node.value)
    if isinstance(node, TimeFn):
        return (t, id(node.fn))
    if isinstance(node, BeatFn):
        return (t, id(node.fn))
    if isinstance(node, RefSignal):
        return (t, node.key)
    if isinstance(node, Add) or isinstance(node, Mul):
        a, b = sorted(child_ids)
        return (t, a, b)
    if isinstance(node, Sub):
        return (t, child_ids[0], child_ids[1])
    if isinstance(node, Neg):
        return (t, child_ids[0])
    if isinstance(node, Clamp):
        return (t, child_ids[0], node.lo, node.hi)
    if isinstance(node, Rectify):
        return (t, child_ids[0])
    if isinstance(node, Power):
        return (t, child_ids[0], node.gamma, node.unipolar)
    if isinstance(node, MapRange):
        return (t, child_ids[0], node.in_min, node.in_max, node.out_min, node.out_max, node.clamp)
    if isinstance(node, Mix):
        return (t, child_ids[0], child_ids[1], child_ids[2])
    if isinstance(node, WavetableOverTime):
        return (t, ("tbl", node._table_fp), node.duration, node.phase0, node.interp, node.loop)
    if isinstance(node, WavetableOsc):
        if isinstance(node.freq, Const):
            return (t, ("tbl", node._table_fp), ("ConstFreq", node.freq.value), node.phase0, node.interp, node.loop)
        return (t, id(node))
    if isinstance(node, Smooth):
        return (t, id(node))
    return (t, id(node))

class GlobalCanonicalizer:
    """
    Cross-track CSE: one interning table shared across all roots.
    """
    def __init__(self):
        self.intern: Dict[Tuple[Any, ...], Signal] = {}
        self.memo: Dict[int, Signal] = {}   # original node id -> canonical node
        self.stats = CanonStats()

    def canonicalize(self, root: Signal) -> Signal:
        def rec(n: Signal) -> Signal:
            nid = n.id
            if nid in self.memo:
                return self.memo[nid]

            self.stats.visited += 1

            # Canonicalize children first
            ch = tuple(rec(c) for c in n.children())
            child_ids = tuple(c.id for c in ch)

            key = _node_key(n, child_ids)

            existing = self.intern.get(key)
            if existing is not None:
                self.memo[nid] = existing
                self.stats.replaced += 1
                return existing

            # Build a node that points to canonical children (plus constant folding)
            new_node = _rebuild_node(n, ch)

            self.intern[key] = new_node
            self.memo[nid] = new_node
            self.stats.created += 1
            return new_node

        return rec(root)

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

    def at(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache] = None) -> float:
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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        return self.a.at(sample, ctx, cache) + self.b.at(sample, ctx, cache)

class Sub(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        return self.a.at(sample, ctx, cache) - self.b.at(sample, ctx, cache)

class Mul(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        return self.a.at(sample, ctx, cache) * self.b.at(sample, ctx, cache)

class Neg(Signal):
    def __init__(self, x: Signal):
        super().__init__()
        self.x = x

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        return -self.x.at(sample, ctx, cache)

    @property
    def requires_beat(self) -> bool:
        return self.x.requires_beat

    def children(self) -> tuple["Signal", ...]:
        return (self.x,)

class RefSignal(Signal):
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
        return self._target.requires_beat if self._target is not None else False

    def children(self) -> tuple["Signal", ...]:
        return (self._target,) if self._target is not None else ()

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        return self.target.at(sample, ctx, cache)

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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        v = float(self.x.at(sample, ctx, cache))
        if v < self.lo:
            return self.lo
        if v > self.hi:
            return self.hi
        return v

@dataclass
class SmoothCache:
    values: Dict[int, float] = field(default_factory=dict)

    def get(self, block: int) -> Optional[float]:
        return self.values.get(block)

    def set(self, block: int, value: float) -> None:
        self.values[block] = value

class Smooth(UnaryOp):
    def __init__(self, x: Signal, time_constant_s: float):
        super().__init__(x)
        if time_constant_s < 0:
            raise ValueError("time_constant_s must be >= 0")
        self.tau = float(time_constant_s)
        self._smooth_cache = SmoothCache()

    def _alpha(self, ctx: EvalContext) -> float:
        dt = ctx.block_size / ctx.sample_rate
        if self.tau <= 0:
            return 1.0
        return 1.0 - math.exp(-dt / self.tau)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        block = sample // ctx.block_size

        hit = self._smooth_cache.get(block)
        if hit is not None:
            return hit

        if block == 0:
            s_mid = (ctx.block_size // 2)
            x0 = float(self.x.at(s_mid, ctx, cache))
            self._smooth_cache.set(0, x0)
            return x0

        prev = self._smooth_cache.get(block - 1)
        if prev is None:
            prev = self._eval((block - 1) * ctx.block_size, ctx, cache)

        s_mid = block * ctx.block_size + (ctx.block_size // 2)
        xk = float(self.x.at(s_mid, ctx, cache))

        a = self._alpha(ctx)
        yk = prev + a * (xk - prev)

        self._smooth_cache.set(block, yk)
        return yk

class Rectify(UnaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        return abs(float(self.x.at(sample, ctx, cache)))

class Power(UnaryOp):
    def __init__(self, x: Signal, gamma: float, *, unipolar: bool = True):
        super().__init__(x)
        if gamma <= 0 or not math.isfinite(gamma):
            raise ValueError("gamma must be finite and > 0")
        self.gamma = float(gamma)
        self.unipolar = bool(unipolar)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        v = float(self.x.at(sample, ctx, cache))
        if self.unipolar:
            if v <= 0.0:
                return 0.0
            if v >= 1.0:
                return 1.0
            return v ** self.gamma
        if v == 0.0:
            return 0.0
        return math.copysign(abs(v) ** self.gamma, v)

class MapRange(UnaryOp):
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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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

class Mix(Signal):
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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
        m = self.amount.at(sample, ctx, cache)
        va = self.a.at(sample, ctx, cache)
        vb = self.b.at(sample, ctx, cache)
        return va * (1.0 - m) + vb * m

# ----------------------------
# Example: define a shared curve and use it for multiple params
# ----------------------------

if __name__ == "__main__":
    ctx = EvalContext(sample_rate=48000, block_size=128)
    cache = ControlCache()

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

def table_fingerprint(table: Sequence[float]) -> str:
    h = hashlib.sha1()
    for x in table:
        h.update(struct.pack("<d", float(x)))
    return h.hexdigest()

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

        self._table_fp = table_fingerprint(self.table)

        self.freq = as_signal(freq_hz)
        self.phase0 = float(phase0)
        self.interp = interp
        self.loop = loop

        self._phase_cache = PhaseCache()
        self._phase_cache.set(0, self.phase0)
        self._cache_every_blocks = int(cache_every_blocks)

    def _phase_at_block_start(self, block: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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
        self._table_fp = table_fingerprint(self.table)
        self.duration = float(duration_seconds)
        self.phase0 = float(phase0)
        self.interp = interp
        self.loop = loop

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[ControlCache]) -> float:
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

