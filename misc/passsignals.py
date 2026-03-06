vibe = proj.sig("A.mod.vibe")          # reference to A's exported signal

b_vibe = 0.5 * vibe + 0.1              # scale + offset
b_microtune = 7 * b_vibe               # derive a new target
b_cutoff = 2000 + 500 * b_vibe