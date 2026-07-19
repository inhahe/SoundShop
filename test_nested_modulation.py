"""
Nested-modulation verification for soundshop: "an LFO modulating an LFO
modulating sound."

Two things are verified:

1. Plugin state passthrough (getpluginstate / setpluginstate). This is the
   feature that lets a client save and restore a full patch, including routing
   that is NOT exposed as host-automatable parameters (e.g. Vital's modulation
   matrix), so a preset authored with LFO->LFO routing can be loaded. We verify
   it FUNCTIONALLY: capture a state, perturb an automatable parameter, restore
   the state, and confirm the parameter snaps back.

2. Nested modulation actually reaches the rendered audio. We drive per-note
   PITCH (via scheduled 14-bit pitch-bend) with a *fast* vibrato whose
   instantaneous rate is itself modulated by a *slow* LFO
   (rate(t) = center + depth*sin(2*pi*slow*t)). Pitch bend is chosen because
   every instrument responds to it natively, so we don't depend on a plugin
   exposing a continuous amplitude/cutoff parameter (Vital exposes none, and
   u-he synths mark their params non-automatable). We then track the rendered
   fundamental frequency over time and use a Hilbert-based instantaneous-rate
   estimate to show the vibrato rate SWINGS between fast and slow in step with
   the slow LFO. A single fixed LFO cannot produce a time-varying vibrato rate,
   so this discriminates true nested modulation from plain vibrato.

Renders offline to WAV (no audio device needed). Prints PASS/FAIL and exits
non-zero on failure.
"""

import os
import sys
import time
import wave
import math
import json

import numpy as np

from juce_client import JuceAudioClient, ServerError
from juce_client.protocol import sampleRate, outputIndex

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_EXE = os.path.join(HERE, "juce_gui_server.exe")

PB_CENTER = 8192  # 14-bit pitch-bend centre

failures = []
def check(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        failures.append(msg)
    return cond


def read_wav_mono(path):
    with wave.open(path, "rb") as w:
        nch, sw, fr, n = (w.getnchannels(), w.getsampwidth(),
                          w.getframerate(), w.getnframes())
        raw = w.readframes(n)
    if sw != 2 or n == 0:
        return np.zeros(0), fr
    s = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if nch > 1:
        s = s.reshape(-1, nch).mean(axis=1)
    return s, fr


def render(client, out_wav, dur):
    if os.path.exists(out_wav):
        os.remove(out_wav)
    client.startplayback(int(dur * sampleRate), toFile=True, filename=out_wav)
    for _ in range(200):
        if os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000:
            break
        time.sleep(0.1)
    time.sleep(0.3)


def moving_average(x, win):
    if win <= 1:
        return x
    k = np.ones(win) / win
    return np.convolve(x, k, mode="same")


def analytic_inst_freq(x, fs):
    """Instantaneous frequency (Hz) of x via the FFT Hilbert transform."""
    n = len(x)
    X = np.fft.fft(x)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1
        h[1:n // 2] = 2
    else:
        h[0] = 1
        h[1:(n + 1) // 2] = 2
    analytic = np.fft.ifft(X * h)
    phase = np.unwrap(np.angle(analytic))
    return np.diff(phase) / (2.0 * np.pi) * fs


def track_pitch(s, fr, frame_hz=400.0, fmin=80.0, fmax=1200.0):
    """Per-frame fundamental frequency via autocorrelation. Returns (f0, times)."""
    hop = int(fr / frame_hz)
    win = int(fr * 0.04)  # 40 ms window
    lo = int(fr / fmax)
    hi = int(fr / fmin)
    f0 = []
    times = []
    for start in range(0, len(s) - win, hop):
        frame = s[start:start + win]
        frame = frame - frame.mean()
        if np.sqrt(np.mean(frame * frame)) < 1e-4:
            f0.append(np.nan)
            times.append(start / fr)
            continue
        ac = np.correlate(frame, frame, mode="full")[win - 1:]
        seg = ac[lo:hi]
        if seg.size == 0:
            f0.append(np.nan)
        else:
            lag = lo + int(np.argmax(seg))
            f0.append(fr / lag if lag > 0 else np.nan)
        times.append(start / fr)
    return np.array(f0), np.array(times)


def pick_audible_instrument(client, plugins):
    for inst in [p for p in plugins if p["isInstrument"]]:
        proc = client.loadpluginbyuid(inst["pluginId"], 20)
        ch = client.getChannelsInfo(proc.key)
        nchan = ch["outputBuses"][0]["numChannels"] if ch["outputBuses"] else 2
        for c in range(min(nchan, 2)):
            client.connectaudio(proc.key, c, outputIndex, c)
        client.clearmidischedule()
        client.schedulemidinotes([(proc.key, 60, 1.0, 0.0, 1.0, 1)])
        out = os.path.join(HERE, "_probe.wav")
        render(client, out, 1.5)
        s, _ = read_wav_mono(out) if os.path.exists(out) else (np.zeros(0), 0)
        if os.path.exists(out):
            os.remove(out)
        if s.size and np.max(np.abs(s)) > 0.01:
            return inst, proc
        client.clearmidischedule()
        client.removeplugin(proc.key)
    return None, None


def build_nested_bends(total_dur, slow, center, depth, semitone_depth,
                       bend_range_semis=2.0, step_hz=300.0):
    """Fast vibrato whose instantaneous rate is modulated by a slow LFO.
    Returns [(value14bit, time_s), ...]."""
    n = int(total_dur * step_hz)
    ts = np.arange(n) / step_hz
    dt = 1.0 / step_hz
    rate = center + depth * np.sin(2 * np.pi * slow * ts)          # nested rate(t)
    theta = 2 * np.pi * np.cumsum(rate) * dt
    semis = semitone_depth * np.sin(theta)                          # pitch offset (semitones)
    frac = np.clip(semis / bend_range_semis, -1.0, 1.0)            # of full bend range
    values = np.round(PB_CENTER + frac * 8191).astype(int)
    values = np.clip(values, 0, 16383)
    return list(zip(values.tolist(), ts.tolist()))


def measure_vibrato(f0, times, slow, center, depth, total_dur):
    """Estimate the vibrato's instantaneous rate over time from the pitch track."""
    # interpolate over NaNs, resample to uniform grid
    good = ~np.isnan(f0)
    if good.sum() < 10:
        return 0, 0, 0, 0.0
    fs = 1.0 / np.median(np.diff(times))
    f0i = np.interp(times, times[good], f0[good])
    # cents deviation from a slow running mean -> the vibrato waveform
    trend = moving_average(f0i, int(0.30 * fs))
    trend[trend <= 0] = np.nan
    dev = 1200.0 * np.log2(np.clip(f0i / trend, 1e-6, None))
    dev = np.nan_to_num(dev)
    # trim attack/release
    a = int(0.6 * fs)
    b = int((total_dur - 0.6) * fs)
    dev = dev[a:b]
    if dev.size < fs:
        return 0, 0, 0, 0.0
    inst = analytic_inst_freq(dev, fs)
    inst = inst[int(0.2 * fs):-int(0.2 * fs)]
    inst = np.clip(inst, 0, center + depth + 6)
    inst_s = moving_average(inst, int(0.12 * fs))
    fmin, fmax, fmean = float(np.min(inst_s)), float(np.max(inst_s)), float(np.mean(inst_s))
    t = np.arange(inst_s.size) / fs
    t0 = 0.6 + 0.2
    intended = center + depth * np.sin(2 * np.pi * slow * (t + t0))
    im, it = inst_s - inst_s.mean(), intended - intended.mean()
    denom = np.linalg.norm(im) * np.linalg.norm(it)
    corr = float(np.dot(im, it) / denom) if denom > 1e-9 else 0.0
    return fmin, fmax, fmean, corr


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
    autom = [p for p in params
             if p["isAutomatable"] and p["minValue"] != p["maxValue"] and not p["isBoolean"]]

    # --- 1. plugin state passthrough: functional round-trip ---
    print("\n--- plugin state passthrough (functional round-trip) ---")
    blob = client.getpluginstate(proc.key)
    check(len(blob) > 100, f"getpluginstate returned a non-trivial blob ({len(blob)} bytes)")
    # Pick a genuine patch parameter to perturb: skip meta/host parameters
    # (e.g. "Beats Per Minute", tempo/sync), which reflect host context and are
    # deliberately NOT captured by getStateInformation, so they wouldn't restore.
    HOSTISH = ("bpm", "beats per minute", "tempo", "sync", "host", "playhead")
    state_params = [p for p in autom
                    if not p["isMetaParameter"]
                    and not any(k in p["name"].lower() for k in HOSTISH)]
    if state_params:
        tp = state_params[0]
        lo, hi = tp["minValue"], tp["maxValue"]
        orig = client.getparameter(proc.key, tp["originalIndex"])
        perturbed = hi if abs(orig - lo) < abs(orig - hi) else lo
        client.setparameter(proc.key, tp["originalIndex"], float(perturbed))
        moved = client.getparameter(proc.key, tp["originalIndex"])
        rc = client.setpluginstate(proc.key, blob)
        check(rc == 1, "setpluginstate accepted the saved blob")
        restored = client.getparameter(proc.key, tp["originalIndex"])
        print(f"      param '{tp['name']}': orig={orig:.3f} perturbed->{moved:.3f} restored->{restored:.3f}")
        check(abs(restored - orig) < abs(moved - orig) * 0.5 + 1e-3,
              "restoring state returned the perturbed parameter toward its saved value")

    # --- 2. nested modulation: LFO-rate-modulated vibrato reaches the audio ---
    print("\n--- nested modulation (vibrato rate modulated by a slower LFO) ---")
    total_dur = 6.0
    slow, center, depth = 0.35, 8.0, 4.0    # vibrato rate swings 4..12 Hz at 0.35 Hz
    semitone_depth = 0.6                      # vibrato depth (well within +/-2 semi bend)
    bends = build_nested_bends(total_dur, slow, center, depth, semitone_depth)

    client.clearmidischedule()
    # sustained note for the whole render, plus the nested pitch-bend curve
    client.schedulemidinotes([(proc.key, 62, 1.0, 0.0, total_dur - 0.2, 1)])
    client.schedulepitchbends([(proc.key, v, t, 1) for (v, t) in bends])
    # return bend to centre at the end
    client.schedulepitchbends([(proc.key, PB_CENTER, total_dur - 0.1, 1)])

    out_n = os.path.join(HERE, "_nested.wav")
    render(client, out_n, total_dur)
    s, fr = read_wav_mono(out_n) if os.path.exists(out_n) else (np.zeros(0), sampleRate)
    ok = check(s.size > 0 and np.max(np.abs(s)) > 0.001, "nested-modulation render produced audio")
    if ok:
        f0, times = track_pitch(s, fr)
        med = np.nanmedian(f0)
        print(f"      median tracked pitch: {med:.1f} Hz (note 62 ~ 293.7 Hz)")
        fmin, fmax, fmean, corr = measure_vibrato(f0, times, slow, center, depth, total_dur)
        print(f"      measured vibrato rate: min={fmin:.2f} Hz  max={fmax:.2f} Hz  "
              f"mean={fmean:.2f} Hz  corr_with_intended={corr:.2f}")
        print(f"      intended: center={center} Hz depth={depth} Hz "
              f"(swing {center-depth:.0f}..{center+depth:.0f} Hz) at {slow} Hz")
        check(fmax - fmin > 3.0,
              f"vibrato rate varies over time (swing {fmax - fmin:.2f} Hz > 3 Hz)")
        check(corr > 0.4,
              f"measured vibrato rate follows the slow LFO (corr {corr:.2f} > 0.4)")
        check(abs(fmean - center) < 3.0,
              f"mean vibrato rate near configured center ({fmean:.2f} ~ {center} Hz)")
    if os.path.exists(out_n):
        os.remove(out_n)

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
