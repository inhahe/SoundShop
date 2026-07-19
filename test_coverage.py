"""
Coverage test for soundshop engine paths not exercised by test_headless.py,
test_features.py, or test_nested_modulation.py:

  - music_theory regression (note<->MIDI mapping, build_table, change_key,
    shift_semitones/octaves) -- pure Python, guards the off-by-one fixes.
  - Audio file player: load a WAV into a graph node, route it to the output,
    schedule a region, render offline, and verify the rendered audio actually
    contains the source signal. This exercises connectAudio's ability to reach
    an audio-file-player node (previously impossible: connectAudio only looked
    in loadedPlugins, so a loaded file could never be routed to output).
  - connect_midi node resolution (valid pair succeeds, bad id fails).
  - Plugin metadata paths: loadplugin (by path), getPluginInfo, listbadpaths.
  - Ordered-note playback command plumbing (schedule/start/stop/clear). NOTE:
    ordered playback only advances on live keyboard note-ons, so it cannot
    produce audio headlessly; this only checks the command protocol.
  - clearallplugins.

Prints a PASS/FAIL summary and exits non-zero on failure.
"""

import os
import sys
import time
import wave
import struct
import math
import json

from juce_client import JuceAudioClient
from juce_client.protocol import sampleRate, outputIndex
from juce_client.music_theory import (
    Note, build_table, change_key, shift_semitones, shift_octaves,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_EXE = os.path.join(HERE, "juce_gui_server.exe")

failures = []


def check(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        failures.append(msg)
    return cond


# ------------------------------------------------------------------ WAV utils
def write_sine_wav(path, freq=440.0, seconds=1.0, sr=sampleRate, amp=0.5):
    n = int(seconds * sr)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            v = int(amp * 32767 * math.sin(2 * math.pi * freq * i / sr))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


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


def render(client, out_wav, dur):
    if os.path.exists(out_wav):
        os.remove(out_wav)
    client.startplayback(int(dur * sampleRate), toFile=True, filename=out_wav)
    for _ in range(100):
        if os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000:
            break
        time.sleep(0.1)
    time.sleep(0.3)


# --------------------------------------------------------------- theory tests
def test_music_theory():
    print("\n--- music_theory regression (pure Python) ---")
    # Scientific pitch: C4 == 60, A4 == 69, B/C octave boundary correct.
    cases = [("C-1", 0), ("C4", 60), ("A4", 69), ("B4", 71), ("C5", 72), ("G4", 67)]
    for name, exp in cases:
        got = Note(name).midi
        check(got == exp, f"Note('{name}').midi == {exp} (got {got})")

    check(build_table("C", 0) == [0, 2, 4, 5, 7, 9, 11], "C major scale pitch classes")
    check(build_table("A", 5) == [9, 11, 0, 2, 4, 5, 7], "A aeolian scale pitch classes")

    # C major -> G major: C4->G4, D4->A4, E4->B4.
    ck = change_key(["C4", "D4", "E4"], "C", 0, "G", 0)
    check(ck == [67, 69, 71], f"change_key C->G ionian == [67,69,71] (got {ck})")

    # Degree access: degree 4 of C major is G4 (67).
    dg = Note(key="C", degree=4).midi
    check(dg == 67, f"Note(key='C', degree=4).midi == 67 (got {dg})")

    check(shift_semitones(["C4"], 2) == [62], "shift_semitones +2")
    check(shift_octaves(["C4"], 1) == [72], "shift_octaves +1")


# --------------------------------------------------------- audio-player tests
def test_audio_file_player(client):
    print("\n--- audio file player: load -> route -> schedule -> render ---")
    src = os.path.join(HERE, "_cov_source.wav")
    write_sine_wav(src, freq=440.0, seconds=1.0)
    src_peak, src_rms = wav_rms(src)
    print(f"      source sine peak={src_peak:.4f} rms={src_rms:.4f}")

    player_id = client.loadaudiofileintoplayer(src)
    print(f"      player_id={player_id}")
    if not check(player_id >= 0, "loadaudiofileintoplayer returned a valid id"):
        return

    # Route the player's stereo output to the graph output. This is the path
    # that used to be impossible (connectAudio only resolved loadedPlugins).
    c0 = client.connectaudio(player_id, 0, outputIndex, 0)
    c1 = client.connectaudio(player_id, 1, outputIndex, 1)
    check(c0 == 1 and c1 == 1, "connectaudio routed player output to graph output")

    # Schedule the region to play from timeline sample 0, file offset 0.
    r = client.scheduleaudioregion(player_id, 0, 0)
    check(r == 1, "scheduleaudioregion accepted")

    out = os.path.join(HERE, "_cov_player.wav")
    render(client, out, 1.2)
    ok = check(os.path.exists(out), "player render produced a file")
    if ok:
        peak, rms = wav_rms(out)
        print(f"      rendered peak={peak:.4f} rms={rms:.4f}")
        check(peak > 0.05, "rendered audio is non-silent (player reached output)")
        # The rendered RMS should be in the ballpark of the source sine's RMS.
        check(abs(rms - src_rms) < 0.15,
              f"rendered RMS ~ source RMS ({rms:.3f} vs {src_rms:.3f})")

    # Clean up player + connections for later sections.
    client.stopaudioregion(player_id)
    for f in (src, out):
        if os.path.exists(f):
            os.remove(f)
    return player_id


def test_connect_midi_resolution(client, plugins):
    print("\n--- connect_midi node resolution ---")
    insts = [p for p in plugins if p["isInstrument"]]
    if len(insts) < 2:
        check(len(insts) >= 2, "need >=2 instruments to test connect_midi (skipped)")
        return
    a = client.loadpluginbyuid(insts[0]["pluginId"], 30)
    b = client.loadpluginbyuid(insts[1]["pluginId"], 31)
    ok = client.connectmidi(a.key, b.key)
    check(ok == 1, "connectmidi(validA, validB) succeeded")
    bad = client.connectmidi(999999, b.key)
    check(bad == 0, "connectmidi(bad id, valid) failed as expected")
    client.removeplugin(a.key)
    client.removeplugin(b.key)


def test_ordered_playback_plumbing(client):
    print("\n--- ordered playback command plumbing ---")
    # (order, note, velocity, channel, duration_samples)
    notes = [
        (0, 60, 100, 1, sampleRate // 2),
        (1, 64, 100, 1, sampleRate // 2),
        (2, 67, 100, 1, sampleRate // 2),
    ]
    count = client.scheduleorderednotes(notes)
    check(count == len(notes), f"scheduleorderednotes returned count {count}")
    started = client.startorderedplayback(False, False)
    check(started == 1, "startorderedplayback returned success")
    stopped = client.stoporderedplayback()
    check(stopped == 1, "stoporderedplayback returned success")
    cleared = client.clearorderednotes()
    check(cleared == 1, "clearorderednotes returned success")


def test_plugin_metadata(client, plugins):
    print("\n--- plugin metadata paths ---")
    # loadplugin by path (as opposed to by uid).
    inst = next((p for p in plugins if p["isInstrument"] and p.get("path")), None)
    if inst is None:
        check(False, "found an instrument with a path to load by path")
        return
    success, name, uid, errmsg = client.loadplugin(inst["path"], 40)
    check(success == 1, f"loadplugin by path succeeded ({name or errmsg})")
    if success:
        client.removeplugin(40)
    # getPluginInfo takes the scan-list pluginId (== desc.uniqueId), the same id
    # listplugins reports -- not a loaded key.
    info = client.getPluginInfo(inst["pluginId"])
    check(info["pluginId"] == inst["pluginId"], "getPluginInfo returns the requested id")
    check(info["name"] == inst["name"], f"getPluginInfo name matches ('{info['name']}')")
    # A bogus id must not crash the server; it returns a blank record.
    blank = client.getPluginInfo(999999999)
    check(blank["name"] == "", "getPluginInfo(bad id) returns a blank record, no crash")
    bad = client.listbadpaths()
    check(isinstance(bad, list), f"listbadpaths returned a list ({len(bad)} entries)")


def main():
    # music_theory needs no server.
    test_music_theory()

    client = JuceAudioClient(server_exe_path=SERVER_EXE)
    bp = os.path.join(HERE, "badpaths.json")
    if os.path.exists(bp):
        with open(bp) as f:
            client.badPluginPaths = json.load(f)

    print("\n=== connecting ===")
    if not check(client.connect(), "connected"):
        return 1
    client.start_notification_listener(callback=lambda c, d: None)
    client.scanplugins((r"d:\vst3\free",))
    plugins = client.listplugins()

    # Audio file player is fully headless (no instrument needed).
    test_audio_file_player(client)

    test_plugin_metadata(client, plugins)
    test_connect_midi_resolution(client, plugins)
    test_ordered_playback_plumbing(client)

    print("\n--- clearallplugins ---")
    check(client.clearallplugins() == 1, "clearallplugins returned success")

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
