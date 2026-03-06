from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, List, Optional, Protocol, Tuple
import bisect
import math
import random

# ----------------------------
# Public note types
# ----------------------------

@dataclass(frozen=True)
class BeatNote:
    start: Fraction
    dur: Fraction
    pitch: int
    vel: int
    origin_id: int  # or bytes/uuid/etc.

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
# Rounding policy
# ----------------------------

class RoundMode:
    CEIL = "ceil"       # never early (good default for note-on)
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


# ----------------------------
# Anchor cache: beat <-> sample
# ----------------------------

@dataclass
class AnchorCache:
    """
    Stores monotonic anchors for beat->sample mapping.

    Invariants:
      - beats are strictly increasing
      - samples are strictly increasing
    """
    beats: List[float] = field(default_factory=list)
    samples: List[int] = field(default_factory=list)

    def __post_init__(self):
        if len(self.beats) != len(self.samples):
            raise ValueError("AnchorCache beats/samples length mismatch")

    def add_anchor(self, beat: float, sample: int) -> None:
        if self.beats:
            if beat <= self.beats[-1] or sample <= self.samples[-1]:
                # In production you might allow equality; but strict is safer.
                raise ValueError("Anchors must be strictly increasing")
        self.beats.append(beat)
        self.samples.append(sample)

    def find_anchor_leq_beat(self, beat: float) -> Tuple[float, int]:
        """Return (b_anchor, s_anchor) for the greatest anchor with b <= beat."""
        if not self.beats:
            raise ValueError("No anchors")
        i = bisect.bisect_right(self.beats, beat) - 1
        i = max(i, 0)
        return self.beats[i], self.samples[i]

    def find_anchor_leq_sample(self, sample: int) -> Tuple[float, int]:
        """Return (b_anchor, s_anchor) for the greatest anchor with s <= sample."""
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
    def bpm(self, beat: float) -> float: ...
    def beat_to_time_seconds(self, beat: float) -> float: ...
    def time_seconds_to_beat(self, t: float) -> float: ...


@dataclass
class ConstBpmByBeat:
    """
    BPM is constant for beat in [b0, b1).
    Provides exact beat<->time mapping in that interval.
    """
    b0: float
    b1: float
    bpm_value: float
    t0: float  # absolute time (seconds) at b0

    def bpm(self, beat: float) -> float:
        return self.bpm_value

    def beat_to_time_seconds(self, beat: float) -> float:
        if not (self.b0 <= beat <= self.b1):
            raise ValueError("beat out of segment")
        spb = 60.0 / self.bpm_value
        return self.t0 + (beat - self.b0) * spb

    def time_seconds_to_beat(self, t: float) -> float:
        spb = 60.0 / self.bpm_value
        return self.b0 + (t - self.t0) / spb


@dataclass
class LinearBpmByBeat:
    """
    BPM(beat) = m*beat + c for beat in [b0, b1).
    Provides closed-form beat<->time mapping.
    """
    b0: float
    b1: float
    m: float
    c: float
    t0: float  # absolute time (seconds) at b0

    def bpm(self, beat: float) -> float:
        return self.m * beat + self.c

    def beat_to_time_seconds(self, beat: float) -> float:
        if not (self.b0 <= beat <= self.b1):
            raise ValueError("beat out of segment")
        if abs(self.m) < 1e-15:
            # Degenerate to constant
            bpm0 = self.bpm(self.b0)
            if bpm0 <= 0:
                raise ValueError("BPM must stay > 0")
            spb = 60.0 / bpm0
            return self.t0 + (beat - self.b0) * spb

        num = self.m * beat + self.c
        den = self.m * self.b0 + self.c
        if num <= 0 or den <= 0:
            raise ValueError("BPM must stay > 0 throughout segment")
        # ∫ 60/(m*b+c) db = (60/m) ln((m*b+c)/(m*b0+c))
        return self.t0 + (60.0 / self.m) * math.log(num / den)

    def time_seconds_to_beat(self, t: float) -> float:
        if abs(self.m) < 1e-15:
            bpm0 = self.bpm(self.b0)
            spb = 60.0 / bpm0
            return self.b0 + (t - self.t0) / spb

        den = self.m * self.b0 + self.c
        if den <= 0:
            raise ValueError("BPM must stay > 0")
        # exp((m/60)*(t-t0)) = (m*b + c)/den
        factor = math.exp((self.m / 60.0) * (t - self.t0))
        b = (den * factor - self.c) / self.m
        return b


@dataclass
class LambdaBpmByBeat:
    """
    BPM is an arbitrary function of beat. We do numeric integration for beat->time,
    but we will rely on AnchorCache at the TempoMap level so we only integrate locally.
    """
    b0: float
    b1: float
    bpm_fn: Callable[[float], float]
    t0: float  # absolute time at b0 (seconds)

    def bpm(self, beat: float) -> float:
        bpm = float(self.bpm_fn(beat))
        if bpm <= 0:
            raise ValueError("BPM must be > 0")
        return bpm

    def beat_to_time_seconds(self, beat: float) -> float:
        # Not used directly for fast scheduling; TempoMap uses cached integration.
        raise NotImplementedError("Use TempoMap.beat_to_time_seconds_cached")

    def time_seconds_to_beat(self, t: float) -> float:
        raise NotImplementedError("Implement only if you need Scheduled->Beat retime")


# ----------------------------
# TempoMap with anchors + mixed segments
# ----------------------------

@dataclass
class TempoMap:
    """
    Provides beat<->sample mapping with:
      - exact mapping for analytic segments
      - cached numeric integration for lambda segments

    Notes:
      - For lambda segments, we integrate forward in TIME from an anchor near the target beat.
      - Anchors are stored as beat->sample points.
    """
    segments: List[TempoSegment]
    sample_rate: int
    anchors: AnchorCache = field(default_factory=AnchorCache)

    # numeric integration knobs for lambda segments
    block_samples: int = 128          # integrate in blocks of samples for speed
    anchor_every_samples: int = 8192  # store an anchor about this often
    round_mode: str = RoundMode.CEIL  # beat->sample rounding

    def __post_init__(self):
        self.segments.sort(key=lambda s: s.b0)
        if not self.segments:
            raise ValueError("TempoMap needs at least one segment")

        # Initialize the anchor cache at the start of the first segment
        # Assume t0 of first segment corresponds to sample 0.
        # (You can generalize to offsets easily.)
        self.anchors.add_anchor(self.segments[0].b0, 0)

    def _find_segment_for_beat(self, beat: float) -> TempoSegment:
        b0s = [s.b0 for s in self.segments]
        i = bisect.bisect_right(b0s, beat) - 1
        if i < 0:
            raise ValueError("beat before first tempo segment")
        seg = self.segments[i]
        if not (seg.b0 <= beat < seg.b1):
            raise ValueError("beat not covered by any tempo segment")
        return seg

    def beat_to_sample(self, beat: Fraction) -> int:
        """
        Convert an exact beat (Fraction) into a sample index (int) using:
          - segment closed-form when possible
          - cached numeric integration when segment is lambda
        """
        b = float(beat)

        # Fast path: if segment is analytic, map by time formula
        seg = self._find_segment_for_beat(b)
        if not isinstance(seg, LambdaBpmByBeat):
            t = seg.beat_to_time_seconds(b)
            s = t * self.sample_rate
            return round_float_to_int(s, self.round_mode)

        # Lambda path: use anchors + local integration in sample blocks
        return self._beat_to_sample_lambda_cached(b, seg)

    def _beat_to_sample_lambda_cached(self, b_target: float, seg: LambdaBpmByBeat) -> int:
        # Find anchor at/before target beat
        b_anchor, s_anchor = self.anchors.find_anchor_leq_beat(b_target)

        # If anchor is earlier than seg.b0, clamp to segment start
        if b_anchor < seg.b0:
            b_anchor = seg.b0
            # Need sample at seg.b0; easiest is to compute from existing anchor by integrating,
            # but in practice you'll ensure anchors exist at all segment boundaries.
            # For now: require anchors aligned with segment boundaries.
            raise ValueError("Missing anchor at lambda segment boundary (add one during map build)")

        # Integrate forward from (b_anchor, s_anchor) until we reach b_target.
        # We integrate in SAMPLE blocks: each block has dt = block_samples/sample_rate.
        # beat increment is db = (BPM(b_mid)/60) * dt (midpoint integration in beat-space).
        cur_b = b_anchor
        cur_s = s_anchor

        while cur_b < b_target:
            block = self.block_samples

            # avoid stepping past the end of segment too far
            # (optional; mostly for safety)
            # We also avoid overshooting b_target massively by adapting block size.
            bpm1 = seg.bpm(cur_b)
            beats_per_sec = bpm1 / 60.0
            if beats_per_sec <= 0:
                raise ValueError("BPM must be > 0")

            # maximum block to not overshoot b_target too much:
            remaining_beats = b_target - cur_b
            max_block = int(math.ceil(remaining_beats * self.sample_rate / beats_per_sec))
            block = max(1, min(block, max_block))

            dt = block / self.sample_rate

            # Midpoint method in beat-domain (robust enough; Euler also fine here)
            # Predict mid-beat for BPM evaluation
            db_euler = beats_per_sec * dt
            mid_b = cur_b + 0.5 * db_euler
            bpm_mid = seg.bpm(mid_b)
            db = (bpm_mid / 60.0) * dt

            cur_b += db
            cur_s += block

            # Drop anchors periodically
            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                # ensure monotonicity; if cur_b didn't advance, something is wrong
                if cur_b <= self.anchors.beats[-1]:
                    raise ValueError("Non-monotonic beat progress during integration")
                self.anchors.add_anchor(cur_b, cur_s)

        # We stepped to >= b_target; cur_s is the first sample at/after (approximately).
        # For note-ons, CEIL semantics are generally desired.
        return cur_s

    def ensure_boundary_anchors(self) -> None:
        """
        Optional helper: add anchors at every segment boundary so lambda segments
        can start from exact boundary anchors without special-casing.

        For analytic segments, we compute boundary time exactly.
        For lambda segments, we "walk" from the previous boundary anchor by integration
        (not shown here to keep it short).
        """
        for seg in self.segments[1:]:
            b = seg.b0
            # compute sample at boundary using beat_to_sample (which will create anchors)
            _ = self.beat_to_sample(Fraction(b).limit_denominator())


# ----------------------------
# Scheduling: BeatArrangement -> ScheduledArrangement
# ----------------------------

def schedule(
    notes: BeatArrangement,
    tempo: TempoMap,
    sample_rate: int,
    round_mode: str = RoundMode.CEIL,
) -> ScheduledArrangement:
    """
    Convert beat notes to scheduled notes in sample time.
    - Uses tempo.beat_to_sample which uses caching/anchors.
    - Converts duration by scheduling both start and end beats (robust under tempo changes).
    """
    tempo.sample_rate = sample_rate
    tempo.round_mode = round_mode

    # Sort by start beat to make cache usage efficient
    notes_sorted = sorted(notes, key=lambda n: (float(n.start), n.origin_id))

    out: List[ScheduledNote] = []
    for n in notes_sorted:
        start_s = tempo.beat_to_sample(n.start)
        end_s = tempo.beat_to_sample(n.start + n.dur)

        dur_s = max(0, end_s - start_s)
        if dur_s == 0:
            # you can drop zero-length notes or keep them; define a policy
            continue

        out.append(ScheduledNote(
            start_sample=start_s,
            dur_samples=dur_s,
            pitch=n.pitch,
            vel=n.vel,
            origin_id=n.origin_id
        ))
    return out