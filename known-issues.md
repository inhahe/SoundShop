# Known Issues & Technical Debt

Running log of unsolved bugs, design limitations, and tech debt. Fix items
here as they're addressed; add new ones as they're found.

## Open

_(none currently tracked)_

## Fixed (kept for history)

- **Audio file player was unroutable → always silent in renders** — a WAV
  loaded via `load_audio_file` becomes an `AudioFilePlayerNode` in the graph,
  but `connectAudio`/`connectMidi` only resolved ids from `loadedPlugins`, so
  its output could never be connected to the graph output — the player node
  existed but could not be heard. Worse, player ids came from
  `nextAudioPlayerId` starting at 0, colliding with user-supplied plugin keys.
  Fixed by (a) giving player ids a distinct high range (`nextAudioPlayerId`
  starts at 1,000,000) so the two id spaces never collide, and (b) adding
  `resolveNodeId(id, out)` which checks `loadedPlugins` then
  `audioFilePlayerNodes`, used by both `connectAudio` and `connectMidi`.
  Verified in `test_coverage.py`: a 440 Hz sine WAV routed player→output
  renders non-silent with RMS matching the source (0.323 vs 0.354).

- **`loadPlugin` (load-by-path) deadlocked the whole server** — it took a
  `juce::MessageManagerLock` and then called the *synchronous*
  `formatManager.createPluginInstance`, which dispatches plugin construction to
  the message thread and blocks; holding the MML meant the message thread could
  never run that work → hard hang on the first by-path load (the pipe then
  reads EOF whenever the server is killed). `loadPluginByUid` takes no such
  lock and worked fine. Fixed by removing the MML from `loadPlugin`, and while
  there, making it also create+connect a `MidiSourceNode` and add the param
  listener in realtime mode (exactly like `loadPluginByUid`), so a by-path
  plugin can actually receive scheduled MIDI. Verified: `loadplugin` by path
  returns `(1, 'Vital', 3, '')` with no hang.

- **`getPluginInfo` crashed on every call** — it did
  `auto plugin = availablePlugins[key];`, an unchecked `std::vector::operator[]`.
  The client passes the plugin's `uniqueId` (what `listPlugins` reports as
  `pluginId`), not a vector index, so this read out of bounds and crashed the
  server. Fixed by looking up the entry whose `desc.uniqueId == pluginId` and
  writing a well-formed *blank* record (same field layout, empty name) when no
  match is found, so a bad id can never desync the pipe or crash. Verified in
  `test_coverage.py` (valid id returns matching name/id; bogus id returns a
  blank record, no crash).

- **`getParamsInfo` hid every continuous parameter** — the validity filter had
  `if (paramR.numSteps == INT_MAX) isValid = false;`. In JUCE a *continuous*
  (non-stepped) parameter reports `getNumSteps() ==
  AudioProcessor::getDefaultNumParameterSteps() == 0x7fffffff == INT_MAX`, so
  this dropped *all* continuous params and returned only discrete
  switches/enums/transpose. That made hosted synths look crippled — e.g. Vital
  appeared to expose "no cutoff/level/amount" knobs, when in fact it exposes
  775 params of which 429 are continuous (`Filter 1 Cutoff`, `Filter 1
  Resonance`, `Modulation N Amount`, gains, levels, …). Removed the filter
  (`isDiscrete` already distinguishes continuous params). Verified: setting
  `Filter 1 Cutoff` via `setparameter` moves the render's spectral centroid
  (2222 Hz → 3705 Hz), so continuous params are both listed and audibly
  settable through the normal `AudioProcessorParameter::setValue` path. This
  also corrects an earlier wrong conclusion in this file that "Vital exposes no
  continuous synthesis parameters to the host."

- **Scheduled MIDI CCs did not honor host CC→parameter routing** — `routecctoparam`
  registered a host-side CC→param mapping, but that mapping was only applied to
  *live* keyboard/controller input in `handleIncomingMidiMessage`; scheduled CCs
  went straight to the plugin via `MidiSourceNode` and bypassed it. Fixed by
  factoring the mapping application into `PluginHostService::applyCcToMappings()`
  and calling it from a new `applyScheduledCcMappings()` (backed by
  `MidiScheduler::forEachCcInBlock`) once per block in both the realtime callback
  (`RecordingAudioCallback`) and the offline render loop (`renderToFile`). A
  scheduled CC now drives a host-mapped parameter exactly like live input.
  (Also removed dead `dynamic_cast<RangedAudioParameter>` code in the old inline
  loop that computed a value and discarded it.)

- **Non-deterministic renders / apparent "silent" second instrument** — MIDI is
  scheduled relative to the MIDI scheduler's *current* cursor
  (`scheduleNote: startSample = currentSamplePosition + time*sr`), but the cursor
  was only reset to 0 at the *start* of a render — after the user had already
  scheduled. So after the first playback the cursor was left at the end, and the
  next batch of scheduled events landed past the following render's window,
  producing quieter/silent output on alternating renders. This is what made
  Zebra2 (the *second* instrument rendered in `test_headless.py`) look like it
  had a silent default patch — it was actually the drift. Fixed by resetting the
  scheduler/timeline cursor to 0 when playback ends (end of `renderToFile` and in
  `stopPlayback`), so every playback is deterministic and "time t" always means t
  from the start of the next playback. Verified: 5 consecutive identical renders
  now give identical peaks, and both Vital and Zebra2 render audibly.

- **music_theory note→MIDI off-by-one + broken octave boundary** — `make_tables`
  started `semi = -2`, making C4→61 and mislabeling the B/C octave boundary
  (B4→60). Fixed by starting `semi = -3` so C naturals land on multiples of 12
  and the `(semi-12)//12` octave formula aligns with scientific pitch notation.

- **music_theory key/degree/change_key crashed for every input** — the
  degree-based `Note(key=..., degree=...)` path and `change_key()` treated
  `key_tables[key][mode]` (a `dict{semi: Note}`) as a degree-ordered list,
  raising `KeyError`/`AttributeError` unconditionally. Fixed to use
  `build_table(key, mode)` (degree-ordered pitch classes) for the MIDI value
  and `key_tables` only for note spelling; also added the missing `'C'` key to
  `start_dict`.

- **Total offline-render silence** — graph play-config was never set, so the
  output IO node reported 0 channels and `connectaudio` silently no-op'd; plus
  the MIDI scheduler cursor was never advanced during render. Fixed in
  `PluginHostService.h` (`setupAudioIO` / `startPlayback` / `renderToFile`) and
  `MidiScheduler` (`setCurrentPosition` + `syncMidiSchedulerToTimeline`).

- **64× too-long render** — `renderToFile` confused sample count with block
  count; fixed the block/sample conversion.

- **NaN parameter ranges** — `paramInfo` struct members were uninitialized;
  added default initializers.

- **`juce_client.protocol` un-importable** — missing `import os` and a broken
  indentation block; fixed, which unblocked the whole package.

## Capabilities added while chasing "LFO modulating LFO" testing

- **Plugin state get/set passthrough** — `get_plugin_state` / `set_plugin_state`
  commands (Common.h enum, `cmd_get_plugin_state`/`cmd_set_plugin_state` in
  `PluginHostService.h`, dispatch table) wrap the plugin's
  `getStateInformation`/`setStateInformation`. Python: `getpluginstate(key)
  -> bytes` and `setpluginstate(key, data)` (audio_playback.py), carried over a
  binary-safe length-prefixed frame (`sendbytes`/`readbytes1` in pipe_io.py —
  the old `sendstr`/`readstr` UTF-8-encode and would corrupt an arbitrary
  blob). This lets a client save/restore a full patch, *including* routing that
  is not exposed as host-automatable params (e.g. Vital's modulation matrix).
  Note: a plugin may re-normalise its blob on load, so a byte-for-byte round
  trip is NOT guaranteed (Vital's grows 233327 -> 233349 bytes). Verify
  functionally (restore a perturbed parameter) instead.
- **Scheduled pitch bend** — `MidiScheduler::schedulePitchBend` existed but was
  never reachable from Python. Wired up an end-to-end `schedule_pitch_bend`
  command (Common.h, `cmd_schedule_pitch_bend`, dispatch table, protocol.py,
  `schedulepitchbend`/`schedulepitchbends` in midi_schedule.py). Pitch bend is
  handled natively by every instrument, so it is a clean vehicle for continuous
  per-note modulation without needing a host-exposed continuous parameter.

## Design limitation: Vital-internal LFO→LFO routing not authorable headless

- Correction: Vital DOES expose continuous synthesis params (cutoff, level,
  `Modulation N Amount`, …) to the host — see the `getParamsInfo` filter fix
  above; the earlier "no continuous params" claim was a bug in our own param
  filter, not a plugin limitation. What genuinely is *not* a host parameter is
  the modulation **source→destination routing** itself (which LFO drives which
  target). In Vital that mapping is set in the GUI / stored in the preset, not
  as an automatable param. `Modulation N Amount` (the depth of slot N) IS
  automatable, but with no routing assigned it drives nothing. Vital's VST3
  state blob is opaque compressed binary (no plaintext JSON to edit), and there
  is no factory `.vital` preset with LFO→LFO routing on disk, so an *internal*
  Vital LFO→LFO patch can't be authored purely headlessly today. The state
  passthrough is the enabling hook for loading such a preset when one exists.
  Meanwhile `test_nested_modulation.py` verifies nested modulation reaches the
  audio through the engine's own path: a fast pitch-bend vibrato whose rate is
  modulated by a slower LFO, confirmed by tracking the rendered fundamental and
  recovering a vibrato rate that swings 4..12 Hz and correlates ~0.99 with the
  intended slow modulator.

## Design limitation: ordered-note playback needs live keyboard input

- `schedule_ordered_notes` / `start_ordered_playback` etc. set up a sequence
  that advances **one order-group per incoming keyboard note-on**
  (`triggerNextOrderedNotes` is only called from `handleIncomingMidiMessage`).
  With no physical/virtual keyboard feeding note-ons, the sequence never
  advances and produces no audio — so it can't be verified headlessly. Notes
  are also sent to `keyboardRoutedPlugin`, which must be routed first.
  `test_coverage.py` only checks the command plumbing (schedule returns the
  count; start/stop/clear return success). Full audio verification would need a
  simulated keyboard-input path into `handleIncomingMidiMessage`, which does
  not currently exist.

## Notes for test authors

- **`clearmidischedule()` clears ALL scheduled MIDI events (notes *and* CCs).**
  Call it *before* scheduling either notes or CCs for a take, never between
  them, or you will wipe events you just scheduled. `clearmidiccschedule()`
  clears only CC events.
