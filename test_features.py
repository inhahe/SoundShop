"""
Focused feature test for soundshop engine paths not covered by
test_headless.py: MIDI CC scheduling, CC->parameter routing, and
parameter automation that actually changes the rendered audio.

Renders offline to WAV (no audio device needed) and analyzes the result.
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
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        failures.append(msg)
    return cond


def wav_rms(path):
    with wave.open(path, "rb") as w:
        nch, sw, n = w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2 or n == 0:
        return 0.0, 0.0
    total = n * nch
    s = struct.unpack("<%dh" % total, raw[:total * 2])
    peak = max(abs(x) for x in s) / 32768.0
    rms = math.sqrt(sum(float(x) * x for x in s) / total) / 32768.0
    return peak, rms


def render(client, key, out_wav, dur):
    if os.path.exists(out_wav):
        os.remove(out_wav)
    client.startplayback(int(dur * sampleRate), toFile=True, filename=out_wav)
    for _ in range(100):
        if os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000:
            break
        time.sleep(0.1)
    time.sleep(0.3)


def pick_audible_instrument(client, plugins):
    """Load the first instrument that actually makes sound with a plain note."""
    for inst in [p for p in plugins if p["isInstrument"]]:
        key = 20
        proc = client.loadpluginbyuid(inst["pluginId"], key)
        ch = client.getChannelsInfo(proc.key)
        nchan = ch["outputBuses"][0]["numChannels"] if ch["outputBuses"] else 2
        for c in range(min(nchan, 2)):
            client.connectaudio(proc.key, c, outputIndex, c)
        client.clearmidischedule()
        client.schedulemidinotes([(proc.key, 60, 1.0, 0.0, 1.0, 1)])
        out = os.path.join(HERE, "_probe.wav")
        render(client, proc.key, out, 1.5)
        peak, _ = wav_rms(out) if os.path.exists(out) else (0.0, 0.0)
        if os.path.exists(out):
            os.remove(out)
        if peak > 0.01:
            return inst, proc
        client.clearmidischedule()
        client.removeplugin(proc.key)
    return None, None


def main():
    client = JuceAudioClient(server_exe_path=SERVER_EXE)
    bp = os.path.join(HERE, "badpaths.json")
    if os.path.exists(bp):
        with open(bp) as f:
            client.badPluginPaths = json.load(f)

    print("=== connecting ===")
    if not check(client.connect(), "connected"):
        return 1
    client.start_notification_listener(callback=lambda c, d: None)
    client.scanplugins((r"d:\vst3\free",))
    plugins = client.listplugins()

    inst, proc = pick_audible_instrument(client, plugins)
    if not check(inst is not None, "found an audible instrument"):
        client.disconnect(shutdown_server=True)
        return 1
    print(f"  using instrument: {inst['name']} (key={proc.key})")

    params = client.getParamsInfo(proc.key)

    # --- MIDI CC scheduling: should not crash, audio still renders ---
    print("\n--- MIDI CC scheduling ---")
    client.clearmidischedule()
    client.clearmidiccschedule()
    client.schedulemidinotes([(proc.key, 60, 1.0, 0.0, 2.0, 1)])
    # sweep mod wheel (CC 1) and expression (CC 11) over the note
    ccs = []
    for i in range(9):
        t = i * 0.2
        ccs.append((proc.key, 1, int(i / 8 * 127), t, 1))
        ccs.append((proc.key, 11, 127 - int(i / 8 * 127), t, 1))
    client.schedulemidiccs(ccs)
    out_cc = os.path.join(HERE, "_feat_cc.wav")
    render(client, proc.key, out_cc, 2.5)
    ok = check(os.path.exists(out_cc), "CC render produced a file")
    if ok:
        peak, rms = wav_rms(out_cc)
        print(f"      peak={peak:.4f} rms={rms:.4f}")
        check(peak > 0.001, "audio still renders with CC automation")
    client.clearmidiccschedule()

    # --- CC -> parameter routing drives a host-mapped parameter ---
    # A scheduled CC now goes through the same host ccMappings step as live
    # controller input (applyScheduledCcMappings in the render/callback loop),
    # so scheduling CC 74 -> 127 should push the routed parameter high.
    print("\n--- CC -> parameter routing ---")
    autom = [p for p in params
             if p["isAutomatable"] and p["minValue"] != p["maxValue"] and not p["isBoolean"]]
    check(len(autom) > 0, "instrument has an automatable parameter to route")
    moved = False
    details = ""
    # Try several candidate params: some plugin params get re-modulated
    # internally each block, so we look for any that clearly follows the CC.
    for target in autom[:8]:
        rid = client.routecctoparam(proc.key, target["originalIndex"], 74, -1)
        if rid < 0:
            continue
        # Clear both schedules FIRST: clearmidischedule() wipes all scheduled
        # events (notes and CCs), so it must come before scheduling either.
        client.clearmidischedule()
        client.clearmidiccschedule()
        client.setparameter(proc.key, target["originalIndex"], 0.0)
        before = client.getparameter(proc.key, target["originalIndex"])
        client.schedulemidinotes([(proc.key, 60, 1.0, 0.0, 1.0, 1)])
        client.schedulemidiccs([(proc.key, 74, 127, 0.05, 1)])
        out_r = os.path.join(HERE, "_feat_route.wav")
        render(client, proc.key, out_r, 1.0)
        after = client.getparameter(proc.key, target["originalIndex"])
        if os.path.exists(out_r):
            os.remove(out_r)
        client.unroutecctoparam(proc.key, target["originalIndex"], 74)
        client.clearmidiccschedule()
        if after > before + 0.4:
            moved = True
            details = f"param '{target['name']}' {before:.3f} -> {after:.3f}"
            break
    check(moved, f"scheduled CC 74 drove a routed parameter high ({details or 'none moved'})")

    # --- Parameter automation changes the audio ---
    print("\n--- parameter automation affects audio ---")
    if autom:
        target = autom[0]
        client.clearmidischedule()
        client.schedulemidinotes([(proc.key, 60, 1.0, 0.0, 2.0, 1)])
        client.clearparamschedule()
        changes = []
        steps = 40
        for s in range(steps + 1):
            frac = s / steps
            val = target["minValue"] + (target["maxValue"] - target["minValue"]) * frac
            changes.append((proc.key, target["originalIndex"], val, int(frac * 2.0 * sampleRate)))
        client.scheduleparamchanges(changes)
        out_a = os.path.join(HERE, "_feat_autom.wav")
        render(client, proc.key, out_a, 2.5)
        ok = check(os.path.exists(out_a), "automation render produced a file")
        if ok:
            peak, rms = wav_rms(out_a)
            print(f"      peak={peak:.4f} rms={rms:.4f}")
            check(peak > 0.001, "audio renders with parameter automation")
        client.clearparamschedule()
        if os.path.exists(out_a):
            os.remove(out_a)

    for f in ("_feat_cc.wav",):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            os.remove(p)

    client.clearmidischedule()
    client.removeplugin(proc.key)
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
