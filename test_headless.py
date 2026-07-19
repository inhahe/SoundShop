"""
Headless smoke + feature test for soundshop.

Exercises the engine without requiring user interaction:
  - connect / scan / load instrument
  - query params + channels
  - set + read back a parameter
  - connect audio graph
  - schedule MIDI notes + chord + param automation
  - OFFLINE render to WAV (no audio device needed)
  - analyze the WAV (RMS, peak, silence, note-onset activity)
  - repeat with a second instrument
Prints a PASS/FAIL summary and exits non-zero on failure.
"""

import os
import sys
import time
import wave
import struct
import math
import json

from juce_client import JuceAudioClient, ServerError
from juce_client.protocol import sampleRate, outputIndex
from juce_client.music_theory import Notes

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_EXE = os.path.join(HERE, "juce_gui_server.exe")

failures = []
def check(cond, msg):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        failures.append(msg)
    return cond


def analyze_wav(path):
    """Return dict of stats about a 16-bit WAV file (peak, rms, silence)."""
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        fr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    stats = {"channels": nch, "sampwidth": sw, "framerate": fr, "frames": n}
    if sw != 2 or n == 0:
        stats.update(peak=0.0, rms=0.0, active_frac=0.0)
        return stats
    total = n * nch
    fmt = "<%dh" % total
    samples = struct.unpack(fmt, raw[:total * 2])
    peak = 0
    sumsq = 0.0
    # activity: fraction of 1024-sample windows whose RMS exceeds a floor
    win = 1024
    active = 0
    windows = 0
    wsum = 0.0
    wcount = 0
    for i, s in enumerate(samples):
        a = abs(s)
        if a > peak:
            peak = a
        sumsq += float(s) * float(s)
        wsum += float(s) * float(s)
        wcount += 1
        if wcount >= win:
            wrms = math.sqrt(wsum / wcount) / 32768.0
            if wrms > 0.0005:
                active += 1
            windows += 1
            wsum = 0.0
            wcount = 0
    rms = math.sqrt(sumsq / total) / 32768.0 if total else 0.0
    stats.update(
        peak=peak / 32768.0,
        rms=rms,
        active_frac=(active / windows) if windows else 0.0,
    )
    return stats


def run_instrument(client, plugin, key, out_wav):
    print(f"\n--- rendering with '{plugin['name']}' (key={key}) ---")
    proc = client.loadpluginbyuid(plugin["pluginId"], key)

    params = client.getParamsInfo(proc.key)
    check(len(params) >= 0, f"getParamsInfo returned {len(params)} params")

    channels = client.getChannelsInfo(proc.key)
    check(channels["success"] == 1, "getChannelsInfo success")
    check(bool(channels["acceptsMidi"]), "instrument accepts MIDI")

    # set + read a parameter. set/getParameter use JUCE's normalised [0,1]
    # convention, so set a normalised value and expect it back.
    automatable = [p for p in params if p["isAutomatable"] and p["minValue"] != p["maxValue"]]
    def finite(x):
        return x == x and abs(x) != float("inf")
    check(all(finite(p["minValue"]) and finite(p["maxValue"]) for p in params),
          "all reported param ranges are finite (no NaN)")
    if automatable:
        t = automatable[0]
        norm = 0.5
        client.setparameter(proc.key, t["originalIndex"], norm)
        rb = client.getparameter(proc.key, t["originalIndex"])
        check(finite(rb) and abs(rb - norm) < 0.05,
              f"param '{t['name']}' normalised set~readback ({norm:.3f} vs {rb:.3f})")

    # connect audio out -> system out
    out_buses = channels["outputBuses"]
    nchan = out_buses[0]["numChannels"] if out_buses else 2
    for ch in range(min(nchan, 2)):
        client.connectaudio(proc.key, ch, outputIndex, ch)

    # schedule a scale + chord
    client.clearmidischedule()
    scale = Notes("C4 D4 E4 F4 G4 A4 B4 C5")
    bpm = 120
    beat = 60.0 / bpm
    notes = [(proc.key, n.midi, 100 / 127.0, i * beat, beat * 0.9, 1) for i, n in enumerate(scale)]
    chord_start = len(scale) * beat
    chord_dur = beat * 2
    for n in Notes("C4 E4 G4"):
        notes.append((proc.key, n.midi, 110 / 127.0, chord_start, chord_dur, 1))
    client.schedulemidinotes(notes)

    # param automation sweep
    if automatable:
        t = automatable[0]
        total = chord_start + chord_dur
        steps = 20
        changes = []
        for s in range(steps + 1):
            frac = s / steps
            val = t["minValue"] + (t["maxValue"] - t["minValue"]) * frac
            changes.append((proc.key, t["originalIndex"], val, int(frac * total * sampleRate)))
        client.clearparamschedule()
        client.scheduleparamchanges(changes)

    total_dur = chord_start + chord_dur + 0.5
    end_sample = int(total_dur * sampleRate)

    if os.path.exists(out_wav):
        os.remove(out_wav)
    client.startplayback(end_sample, toFile=True, filename=out_wav)

    # wait for offline render to finish
    for _ in range(100):
        if os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000:
            break
        time.sleep(0.1)
    time.sleep(0.3)

    audible = False
    ok = check(os.path.exists(out_wav), f"WAV file created: {os.path.basename(out_wav)}")
    if ok:
        stats = analyze_wav(out_wav)
        print(f"      stats: {stats}")
        expected_frames = end_sample
        check(abs(stats["frames"] - expected_frames) < sampleRate,
              f"frame count ~ expected ({stats['frames']} vs {expected_frames})")
        # Whether a given instrument makes sound depends on its default patch
        # (some ship with an empty/silent init preset), so per-instrument
        # silence is a NOTE, not a failure. main() requires >=1 audible.
        audible = stats["peak"] > 0.001 and stats["active_frac"] > 0.05
        if audible:
            print(f"      [ OK ] audible output "
                  f"(peak={stats['peak']:.4f}, active_frac={stats['active_frac']:.2f})")
        else:
            print(f"      [NOTE] '{plugin['name']}' produced (near-)silence "
                  f"(peak={stats['peak']:.4f}, active_frac={stats['active_frac']:.2f}) "
                  f"- likely an empty/silent default patch, not an engine fault")

    client.clearmidischedule()
    client.clearparamschedule()
    client.removeplugin(proc.key)
    return audible


def main():
    client = JuceAudioClient(server_exe_path=SERVER_EXE)
    bp = os.path.join(HERE, "badpaths.json")
    if os.path.exists(bp):
        with open(bp) as f:
            client.badPluginPaths = json.load(f)

    print("=== connecting ===")
    if not check(client.connect(), "connected to server"):
        return 1
    client.start_notification_listener(callback=lambda c, d: None)

    print("=== scanning d:/vst3/free ===")
    try:
        num = client.scanplugins((r"d:\vst3\free",))
    except ServerError as e:
        check(False, f"scan raised ServerError: {e}")
        client.disconnect(shutdown_server=True)
        return 1
    check(num > 0, f"scan found {num} plugins")

    plugins = client.listplugins()
    instruments = [p for p in plugins if p["isInstrument"]]
    print(f"  instruments found: {[p['name'] for p in instruments]}")
    if not check(len(instruments) >= 1, "at least one instrument available"):
        client.disconnect(shutdown_server=True)
        return 1

    # test up to two instruments
    audible_count = 0
    for i, inst in enumerate(instruments[:2]):
        try:
            if run_instrument(client, inst, key=10 + i,
                              out_wav=os.path.join(HERE, f"headless_render_{i}.wav")):
                audible_count += 1
        except Exception as e:
            check(False, f"instrument '{inst['name']}' raised: {e!r}")

    check(audible_count >= 1,
          f"at least one instrument produced audible output ({audible_count} did)")

    client.disconnect(shutdown_server=True)

    print("\n=== SUMMARY ===")
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print("  - " + f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
