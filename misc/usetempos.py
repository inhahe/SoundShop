from fractions import Fraction
import math

# ----- 1) Build tempo map -----

sr = 48000

builder = TempoMapBuilder(sample_rate=sr)

# Segment 1: beats [0, 16) constant 120 BPM
builder.add_const(b0=0.0, b1=16.0, bpm=120.0)

# Segment 2: beats [16, 32) linear ramp 120 -> 240 BPM
builder.add_linear_by_beat(b0=16.0, b1=32.0, bpm0=120.0, bpm1=240.0)

# Segment 3: beats [32, 48) lambda wiggle around ~200 BPM
def wiggle_bpm(b: float) -> float:
    # 200 BPM +/- 20 BPM with a slow sine, plus a tiny higher-frequency ripple
    return 200.0 + 20.0 * math.sin((b - 32.0) * math.pi / 4.0) + 3.0 * math.sin((b - 32.0) * math.pi * 2.0)

builder.add_lambda_by_beat(b0=32.0, b1=48.0, bpm_fn=wiggle_bpm)

tempo = builder.build(block_samples=128, anchor_every_samples=8192)

# ----- 2) Make some beat-based notes -----

notes_beats = [
    BeatNote(start=Fraction(0, 1),   dur=Fraction(1, 2), pitch=60, vel=90, origin_id=1),  # C4, 1/2 beat
    BeatNote(start=Fraction(4, 1),   dur=Fraction(1, 1), pitch=64, vel=90, origin_id=2),  # E4, 1 beat
    BeatNote(start=Fraction(15, 1),  dur=Fraction(1, 4), pitch=67, vel=90, origin_id=3),  # G4, 1/4 beat (near boundary)
    BeatNote(start=Fraction(16, 1),  dur=Fraction(1, 2), pitch=72, vel=90, origin_id=4),  # C5, at start of ramp
    BeatNote(start=Fraction(24, 1),  dur=Fraction(1, 1), pitch=76, vel=90, origin_id=5),  # E5, mid-ramp
    BeatNote(start=Fraction(33, 1),  dur=Fraction(3, 4), pitch=79, vel=90, origin_id=6),  # G5, inside lambda
    BeatNote(start=Fraction(47, 1),  dur=Fraction(1, 2), pitch=84, vel=90, origin_id=7),  # C6, near end
]

# ----- 3) Schedule beat notes -> sample notes -----

scheduled = schedule_beats_to_samples(notes_beats, tempo, round_mode=RoundMode.CEIL)

print("Scheduled notes (start_sample, dur_samples, pitch, origin_id):")
for n in scheduled:
    print(n.start_sample, n.dur_samples, n.pitch, n.origin_id)

# ----- 4) Retime back to beats (lossy) -----
# Quantize to 1/16 notes so output is readable.
retimed = retime_samples_to_beats(
    scheduled,
    tempo,
    grid=Fraction(1, 16),
    quantize_mode=RoundMode.NEAREST,
    refine_inverse=True,
    min_dur=Fraction(1, 256),
)

print("\nRetimed notes (start_beat, dur_beat, pitch, origin_id):")
for n in retimed:
    print(n.start, n.dur, n.pitch, n.origin_id)

# ----- 5) Optional: show how close the retimed starts are to originals -----

orig_by_id = {n.origin_id: n for n in notes_beats}
print("\nStart-beat deltas after round-trip (in beats):")
for n in retimed:
    orig = orig_by_id[n.origin_id]
    delta = float(n.start - orig.start)
    print(f"id={n.origin_id}: delta={delta:+.6f} beats")