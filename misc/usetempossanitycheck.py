sanity_check_tempo_map(
    tempo,
    start_sample=0,
    end_sample=sr * 20,   # test first 20 seconds
    step_samples=997,
    refine_inverse=True,
    max_err_samples=256,
)

"""
If this fails, what it typically means

block_samples is too large for a sharply varying tempo curve
→ reduce block_samples (e.g., 64 instead of 128)

Your beat_to_sample rounding policy makes the round-trip bound bigger
→ keep CEIL for note-ons, but accept larger max_err_samples

Lambda tempo has discontinuities and your anchor density is too low
→ reduce anchor_every_samples (more anchors) or reduce block_samples
"""