"""
ctx = EvalContext(sample_rate=48000, block_size=128)
engine = SignalEngine(ctx)

engine.register_root("track1.tempo_mod", tempo)
engine.register_root("track2.cutoff_mod", cutoff)
engine.compile()

During rendering/scheduling:

block = cur_sample // ctx.block_size
engine.prepare_block(block)   # optional but nice

tempo_val = engine.root_at("track1.tempo_mod", cur_sample)
cutoff_val = engine.root_at("track2.cutoff_mod", cur_sample)
"""

"""
Example inside your scheduler:

s0 = tempo.beat_to_sample(n.start, ctx=engine.ctx, cache=engine.cache)
"""

"""
at TempoMap.set_bpm_modulation(signal):

run detect_signal_cycle(signal)

reject signal_depends_on_beat(signal)
"""

"""
4) Example: “pass a signal between tracks”
ctx = EvalContext(sample_rate=48000, block_size=128)
proj = Project(ctx)

a = proj.track("A")
b = proj.track("B")

# Track A defines and exports a shared curve
vibe = TimeFn(lambda t: math.sin(2 * math.pi * 5.0 * t))
a.export("vibe", vibe)

# Track B imports A.vibe and uses it in its own expressions
vibe_in = b.import_("A.vibe")
microtune = 7.0 * vibe_in
cutoff = 2000.0 + 500.0 * vibe_in

# Register whatever you want as “roots” for evaluation/scheduling
proj.engine.register_root("B.microtune", microtune)
proj.engine.register_root("B.cutoff", cutoff)

proj.engine.compile()

s = 1024
block = s // ctx.block_size
proj.engine.prepare_block(block)  # optional pre-warm

print(proj.engine.at(microtune, s))
print(proj.engine.at(cutoff, s))
"""

"""
Example usage (nice and terse)
ctx = EvalContext(sample_rate=48000, block_size=128)
proj = Project(ctx)

A = proj.track("A")
B = proj.track("B")

# A exports a signal
A.export("vibe", TimeFn(lambda t: math.sin(2*math.pi*5*t)))

# B consumes it using project.sig(...)
vibe = proj.sig("A.vibe")
cutoff = 2000 + 500 * vibe
microtune = 7 * vibe

proj.engine.register_root("B.cutoff", cutoff)
proj.engine.register_root("B.microtune", microtune)
proj.engine.compile()

s = 1024
proj.engine.prepare_block(s // ctx.block_size)
print(proj.engine.root_at("B.cutoff", s))
print(proj.engine.root_at("B.microtune", s))

# B wants to expose its own outputs for others
B.export("microtune", microtune)

# Later, C can do:
v = proj.sig("B.microtune")
# B wants to expose its own outputs for others
B.export("microtune", microtune)

# Later, C can do:
v = proj.sig("B.microtune")
"""

"""
Example workflow
ctx = EvalContext(sample_rate=48000, block_size=128)
proj = Project(ctx)

A = proj.track("A")
B = proj.track("B")

# A publishes a shared LFO-like curve
vibe = WavetableOsc([0.0, 1.0, 0.0, -1.0], freq_hz=5.0)
A.export("vibe", vibe, category="mod")  # A.mod.vibe

# B consumes it
v = proj.sig("A.mod.vibe")
cutoff = 2000 + 500 * v
microtune = 7 * v

B.export("cutoff", cutoff, category="param")     # B.param.cutoff
B.export("microtune", microtune, category="param")

proj.engine.register_root("B.param.cutoff", proj.sig_checked("B.param.cutoff"))
proj.engine.register_root("B.param.microtune", proj.sig_checked("B.param.microtune"))

# Debug: list exports
print(proj.list_exports())           # all
print(proj.list_exports("A."))       # A only
print(proj.describe_exports("A."))   # (key, id, type)

proj.engine.compile()
"""

"""
Can interpolate samples between blocks or use the mid-block value
example:
A) Tempo modulation during scheduling → use value_block
Why: tempo affects beat↔sample integration. A per-sample interpolated tempo is usually meaningless unless you also shrink your integration step. Use one tempo value per integration step (block).
block = cur_sample // ctx.block_size
bpm_mod = engine.value_block(tempo_mod_signal, block)
effective_bpm = base_bpm + bpm_mod

B) Filter cutoff (audible) → use value (interpolated)
Why: stepwise cutoff produces zipper noise on many filters. Interpolating makes it much smoother without full audio-rate modulation.
cutoff_hz = engine.value(cutoff_signal, sample)
filter.set_cutoff(cutoff_hz)

C) Gain / volume automation → usually value
Why: discontinuous gain changes can click. Interpolation is cheap and helps.
gain = engine.value(gain_signal, sample)  # e.g. linear gain or dB converted
out = in * gain
(If you already have Smooth, you might be okay with value_block, but value is still safer.)

D) Panning automation → value is nice
Why: pan jumps are audible; interpolate for smooth motion.
pan = engine.value(pan_signal, sample)  # -1..1
left, right = pan_law(pan)

E) MIDI-only modulation / note transforms → value_block
Why: MIDI transforms happen at event times, not sample times. You just need a value at the event’s scheduled sample or block.
event_block = event_sample // ctx.block_size
transpose = engine.value_block(transpose_signal, event_block)
new_pitch = pitch + int(round(transpose))

F) Macro knobs controlling many parameters → either
If it’s controlling DSP parameters: value
If it’s controlling structure/scheduling: value_block
Example: macro controlling both a cutoff and a note density:
macro = proj.sig("Master.mod.macro1")
# audio param
cutoff = map_range(macro, 0, 1, 200, 6000)
cutoff_hz = engine.value(cutoff, sample)
# scheduling param
density = map_range(macro, 0, 1, 1, 8, clamp=True)
repeats_per_beat = engine.value_block(density, block)
"""

p = Project()

master = p.add_track("Master")  # you can treat this specially later if you want

drums  = p.add_track("Drums", parent=master)   # auto: Drums.out -> Master.in
kick   = p.add_track("Kick",  parent=drums)    # auto: Kick.out  -> Drums.in
snare  = p.add_track("Snare", parent=drums)    # auto: Snare.out -> Drums.in

# Override: route Snare straight to Master instead
p.disconnect(src=snare, src_port="out", dst=drums, dst_port="in")
p.connect(snare, "out", master, "in")

# See what ports are “exposed” just by connections:
print(p.exposed_ports(drums))   # inputs likely {"in"}, outputs likely {"out"}




p = Project()
master = p.add_track("Master")
drums  = p.add_track("Drums", parent=master)   # Drums -> Master
kick   = p.add_track("Kick", parent=drums)     # Kick -> Drums

compiled = compile_audio_graph(p)
print([p.tracks[t].name for t in compiled.order])
# e.g. ["Kick", "Drums", "Master"]




compiled = compile_audio_graph(p)
validate_ports(p, strict=True)


Project.auto_declare_ports = True


“Production” (recommended)

Hard errors on typos / missing ports / cycles:

compiled = project.compile(
    strict_ports=True,
    freeze_ports=True,
    detect_cycles=True,
)
“Prototyping”

Let connections invent ports, and let compile create any missing ports:

compiled = project.compile(
    strict_ports=False,
    freeze_ports=False,
    auto_create_missing_ports=True,
)
“Lock then render”

Often you’ll do:

project.freeze_ports()
compiled = project.compile(strict_ports=True, detect_cycles=True, freeze_ports=True)
# hand compiled.audio.order + compiled.audio.incoming_by_track to your renderer backend