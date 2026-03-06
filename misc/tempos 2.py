"""
One important recommendation

Call:

tempo.ensure_boundary_anchors()

after building your tempo map (especially if you have lambda segments). That ensures every segment boundary has an anchor, so a lambda segment never complains about “missing boundary anchor”.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, List, Optional, Protocol, Tuple
import bisect
import math

# ----------------------------
# Public note types
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
    """
    Quantize a beat value (float) to the nearest multiple of 'grid' (Fraction).
    Example: grid=Fraction(1,16) snaps to 16th notes.
    """
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

    # Return exact multiple of grid
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
# TempoMap
# ----------------------------

@dataclass
class TempoMap:
    segments: List[TempoSegment]
    sample_rate: int
    anchors: AnchorCache = field(default_factory=AnchorCache)

    block_samples: int = 128
    anchor_every_samples: int = 8192
    round_mode: str = RoundMode.CEIL  # beat->sample rounding

    def __post_init__(self):
        self.segments.sort(key=lambda s: s.b0)
        if not self.segments:
            raise ValueError("TempoMap needs at least one segment")
        # Anchor at first segment start = sample 0
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

    def _find_segment_for_time(self, t: float) -> TempoSegment:
        # segments have t0 but not necessarily t1 stored; compute t1 for analytic, or approximate.
        # For simplicity: binary search by beat anchors instead (works well if you use sample->beat then beat->seg).
        # We'll route sample_to_beat through anchors + integration and segment lookup by beat.
        raise NotImplementedError("Not needed in this design")

    # ---- Beat -> Sample ----

    def beat_to_sample(self, beat: Fraction) -> int:
        b = float(beat)
        seg = self._find_segment_for_beat(b)
        if not isinstance(seg, LambdaBpmByBeat):
            t = seg.beat_to_time_seconds(b)
            return round_float_to_int(t * self.sample_rate, self.round_mode)
        return self._beat_to_sample_lambda_cached(b, seg)

    def _beat_to_sample_lambda_cached(self, b_target: float, seg: LambdaBpmByBeat) -> int:
        b_anchor, s_anchor = self.anchors.find_anchor_leq_beat(b_target)

        # Ensure we start within the lambda segment.
        if b_anchor < seg.b0:
            # In production, you should ensure boundary anchors exist for every segment start.
            # Easiest: call ensure_boundary_anchors() once after constructing the tempo map.
            raise ValueError("Missing anchor at lambda segment boundary")

        cur_b = b_anchor
        cur_s = s_anchor

        while cur_b < b_target:
            # adapt block size to avoid huge overshoot
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

    # ---- Sample -> Beat ----

    def sample_to_beat(self, sample: int) -> float:
        """
        Invert mapping approximately:
          - find anchor at/before sample
          - integrate forward in blocks until reaching target sample
        Returns beat as float (continuous).
        """
        b_anchor, s_anchor = self.anchors.find_anchor_leq_sample(sample)
        cur_b = b_anchor
        cur_s = s_anchor

        # Integrate forward until we reach 'sample'
        while cur_s < sample:
            remaining = sample - cur_s
            block = min(self.block_samples, remaining)
            dt = block / self.sample_rate

            # Determine which tempo segment we're in by current beat.
            seg = self._find_segment_for_beat(cur_b)
            if not isinstance(seg, LambdaBpmByBeat):
                # We can move exactly in time by converting beat<->time with this segment,
                # but simplest is to treat it like numeric using bpm at midpoint.
                bpm_now = seg.bpm(cur_b)
                beats_per_sec = bpm_now / 60.0
                db_euler = beats_per_sec * dt
                mid_b = cur_b + 0.5 * db_euler
                bpm_mid = seg.bpm(mid_b)
                db = (bpm_mid / 60.0) * dt
            else:
                bpm_now = seg.bpm(cur_b)
                beats_per_sec = bpm_now / 60.0
                db_euler = beats_per_sec * dt
                mid_b = cur_b + 0.5 * db_euler
                bpm_mid = seg.bpm(mid_b)
                db = (bpm_mid / 60.0) * dt

            cur_b += db
            cur_s += block

            # Add anchors periodically
            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                if cur_b <= self.anchors.beats[-1]:
                    raise ValueError("Non-monotonic beat progress during integration")
                self.anchors.add_anchor(cur_b, cur_s)

        return cur_b

    # ---- Prebuilding anchors at boundaries ----

    def ensure_boundary_anchors(self) -> None:
        """
        Ensures anchors at each segment b0 so lambda segments can start from a clean anchor.
        This may integrate through earlier lambda segments to reach later boundaries, but only once.
        """
        for seg in self.segments[1:]:
            # convert boundary beat -> sample, forcing cache growth
            _ = self.beat_to_sample(Fraction(seg.b0).limit_denominator())


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
    min_dur: Optional[Fraction] = Fraction(1, 256),
) -> BeatArrangement:
    """
    Convert scheduled notes back into beat notes. This is inherently lossy.
    Steps:
      - start beat = inverse mapping sample->beat
      - end beat   = inverse mapping (start+dur)->beat
      - quantize both to grid
      - clamp duration
    """
    out: List[BeatNote] = []

    notes_sorted = sorted(notes, key=lambda n: (n.start_sample, n.origin_id))
    for n in notes_sorted:
        b0 = tempo.sample_to_beat(n.start_sample)
        b1 = tempo.sample_to_beat(n.start_sample + n.dur_samples)

        qb0 = quantize_beat_float_to_fraction(b0, grid, quantize_mode)
        qb1 = quantize_beat_float_to_fraction(b1, grid, quantize_mode)

        dur = qb1 - qb0
        if dur <= 0:
            if min_dur is None or min_dur <= 0:
                continue
            dur = min_dur

        out.append(BeatNote(
            start=qb0,
            dur=dur,
            pitch=n.pitch,
            vel=n.vel,
            origin_id=n.origin_id
        ))

    # Optional: sort & coalesce, etc.
    out.sort(key=lambda n: (float(n.start), n.origin_id))
    return out