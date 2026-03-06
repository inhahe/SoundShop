song.add_audio_file("vox_take1", "/path/vox1.wav")

vox = song.add_audio_track("vox")

# record/import takes
vox.add_take_clip("take1", file_id="vox_take1", start=0, length=8_000_000)
vox.add_take_clip("take2", file_id="vox_take2", start=0, length=8_000_000)

# set alignment offset (slip the whole take)
vox.set_take_offset("take2", -1200)

# create comp
vox.comp = Comp(segments=[
    CompSegment(0, 2_000_000, "take1", xfade_samples=4800),
    CompSegment(2_000_000, 4_000_000, "take2", xfade_samples=4800),
])