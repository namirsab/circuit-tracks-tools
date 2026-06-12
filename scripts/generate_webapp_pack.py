"""Generate the webapp's bundled sample/patch pack.

Synthesizes 64 CC0 drum samples (4 banks x 16 slots, mirroring the factory
pack's slot layout) and builds 16 synth patches with PatchBuilder, then
writes a Novation Components-style pack (index.json + samples/ + patches/)
to webapp/pack/.

Everything is generated from code — no third-party audio is included — so
the resulting pack is freely redistributable.

Usage: python scripts/generate_webapp_pack.py
"""

from __future__ import annotations

import json
import math
import random
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from circuit_tracks.patch_builder import (  # noqa: E402
    preset_bass,
    preset_lead,
    preset_pad,
    preset_pluck,
)

SR = 44100
OUT = ROOT / "webapp" / "pack"

rng = random.Random(20260612)


# --- DSP helpers -----------------------------------------------------------


def silence(seconds):
    return [0.0] * int(SR * seconds)


def sine_drum(freq, pitch_drop, drop_time, decay, length, drive=1.0):
    """Sine with exponential pitch envelope — kicks, toms, pitched percs."""
    n = int(SR * length)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        f = freq + pitch_drop * math.exp(-t / drop_time)
        phase += 2 * math.pi * f / SR
        s = math.sin(phase) * math.exp(-t / decay)
        out.append(math.tanh(s * drive) / math.tanh(drive) if drive > 1 else s)
    return out


def noise(length):
    return [rng.uniform(-1, 1) for _ in range(int(SR * length))]


def lowpass(x, cutoff):
    a = math.exp(-2 * math.pi * cutoff / SR)
    y, prev = [], 0.0
    for s in x:
        prev = (1 - a) * s + a * prev
        y.append(prev)
    return y


def highpass(x, cutoff):
    lp = lowpass(x, cutoff)
    return [s - lo for s, lo in zip(x, lp, strict=True)]


def bandpass(x, lo, hi):
    return highpass(lowpass(x, hi), lo)


def env_decay(x, decay, attack=0.0005):
    n_att = max(1, int(SR * attack))
    out = []
    for i, s in enumerate(x):
        t = i / SR
        a = min(1.0, i / n_att)
        out.append(s * a * math.exp(-t / decay))
    return out


def metallic(length, base=320.0):
    """Stack of detuned square waves at inharmonic ratios (808 cymbal trick)."""
    ratios = [1.0, 1.342, 1.2312, 1.6532, 1.9523, 2.1523]
    n = int(SR * length)
    out = []
    for i in range(n):
        t = i / SR
        s = sum(1.0 if math.sin(2 * math.pi * base * r * t) > 0 else -1.0 for r in ratios)
        out.append(s / len(ratios))
    return out


def mix(*parts):
    n = max(len(p) for p in parts)
    out = [0.0] * n
    for p in parts:
        for i, s in enumerate(p):
            out[i] += s
    return out


def gain(x, g):
    return [s * g for s in x]


def delay_into(x, seconds):
    return silence(seconds) + x


def bitcrush(x, bits=6, downsample=3):
    levels = 2**bits
    out, held = [], 0.0
    for i, s in enumerate(x):
        if i % downsample == 0:
            held = round(s * levels) / levels
        out.append(held)
    return out


def normalize(x, peak=0.891):  # -1 dBFS
    m = max(abs(s) for s in x) or 1.0
    return [s * peak / m for s in x]


def fade_tail(x, seconds=0.012):
    n = int(SR * seconds)
    out = list(x)
    total = len(out)
    for i in range(max(0, total - n), total):
        out[i] *= (total - i) / n
    return out


def write_wav(path, samples):
    data = normalize(fade_tail(samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(s * 32767)) for s in data))


# --- Drum voices -----------------------------------------------------------


def kick(freq, drop, decay, drive, click=0.0):
    body = sine_drum(freq, drop, 0.03, decay, decay * 2.2, drive)
    if click:
        body = mix(body, gain(env_decay(highpass(noise(0.012), 2500), 0.004), click))
    return body


def snare(body_freq, body_decay, noise_decay, noise_lo, noise_hi, noise_mix, length):
    body = sine_drum(body_freq, 60, 0.02, body_decay, length)
    snap = env_decay(bandpass(noise(length), noise_lo, noise_hi), noise_decay)
    return mix(gain(body, 1 - noise_mix), gain(snap, noise_mix * 2.2))


def hat(decay, base, hp_cut, length, tone=0.6):
    ring = metallic(length, base)
    hiss = noise(length)
    return env_decay(highpass(mix(gain(ring, tone), gain(hiss, 1 - tone)), hp_cut), decay)


def clap(spread, decay, lo, hi):
    bursts = [gain(env_decay(bandpass(noise(0.03), lo, hi), 0.008), 0.8) for _ in range(3)]
    tail = env_decay(bandpass(noise(decay * 3), lo, hi), decay)
    parts = [delay_into(b, i * spread) for i, b in enumerate(bursts)]
    parts.append(delay_into(tail, 3 * spread))
    return mix(*parts)


def shaker(decay, length):
    x = bandpass(noise(length), 3000, 9000)
    n_att = int(SR * 0.012)
    return [s * min(1.0, i / n_att) * math.exp(-max(0, i / SR - 0.012) / decay) for i, s in enumerate(x)]


def rim(freq):
    ping = sine_drum(freq, 200, 0.004, 0.025, 0.08)
    snap = env_decay(highpass(noise(0.02), 1800), 0.006)
    return mix(ping, gain(snap, 0.5))


def zap(start, end, length):
    n = int(SR * length)
    out, phase = [], 0.0
    for i in range(n):
        t = i / SR
        f = end + (start - end) * math.exp(-t / (length / 4))
        phase += 2 * math.pi * f / SR
        out.append(math.tanh(2.5 * math.sin(phase)) * math.exp(-t / (length / 3)))
    return out


def bass_note(freq, decay, length, brightness):
    n = int(SR * length)
    saw = []
    for i in range(n):
        t = i / SR
        s = sum(math.sin(2 * math.pi * freq * h * t) / h for h in range(1, 9))
        saw.append(s * 0.5)
    return env_decay(lowpass(saw, brightness), decay)


def chord_stab(root, intervals, decay, length, brightness):
    n = int(SR * length)
    out = [0.0] * n
    for semis in intervals:
        f = root * 2 ** (semis / 12)
        det = rng.uniform(0.998, 1.002)
        for i in range(n):
            t = i / SR
            s = sum(math.sin(2 * math.pi * f * det * h * t) / h for h in range(1, 6))
            out[i] += s * 0.25
    return env_decay(lowpass(out, brightness), decay)


def bell(freq, decay, length):
    partials = [(1.0, 1.0), (2.76, 0.6), (5.40, 0.35), (8.93, 0.2)]
    n = int(SR * length)
    out = []
    for i in range(n):
        t = i / SR
        s = sum(a * math.sin(2 * math.pi * freq * r * t) * math.exp(-t * r / (decay * 2)) for r, a in partials)
        out.append(s * 0.4)
    return out


def riser(length):
    n = int(SR * length)
    out, phase = [], 0.0
    hiss = lowpass(noise(length), 6000)
    for i in range(n):
        t = i / SR
        f = 80 * 2 ** (t / length * 4)
        phase += 2 * math.pi * f / SR
        amp = (t / length) ** 1.5
        out.append((math.sin(phase) * 0.5 + hiss[i] * 0.5) * amp)
    return out


# --- Banks -----------------------------------------------------------------
# Slot layout per bank (mirrors the factory pack):
# 0 KickA  1 KickB  2 SnareA  3 SnareB  4 HHA  5 HHB  6 OHHA  7 OHHB
# 8 PercA  9 PercB  10 PercC  11 ClapA  12 BassA  13 MelA  14 MelB  15 FXA


def bank_deep():
    return [
        ("DeepKickA", kick(52, 70, 0.30, 1.4)),
        ("DeepKickB", kick(48, 55, 0.42, 1.2)),
        ("DeepSnareA", snare(185, 0.09, 0.16, 900, 7000, 0.55, 0.35)),
        ("DeepSnareB", snare(160, 0.12, 0.22, 600, 5000, 0.45, 0.45)),
        ("DeepHHA", hat(0.045, 300, 6500, 0.15)),
        ("DeepHHB", hat(0.065, 280, 5500, 0.2)),
        ("DeepOHHA", hat(0.30, 300, 6000, 0.7)),
        ("DeepOHHB", hat(0.42, 270, 5000, 0.9)),
        ("DeepPercA", sine_drum(330, 380, 0.012, 0.07, 0.2)),
        ("DeepPercB", sine_drum(120, 70, 0.025, 0.18, 0.45)),
        ("DeepPercC", shaker(0.07, 0.18)),
        ("DeepClapA", clap(0.011, 0.07, 700, 6500)),
        ("DeepBassA", bass_note(55, 0.35, 0.8, 900)),
        ("DeepMelA", chord_stab(220, [0, 3, 7, 12], 0.22, 0.7, 2200)),
        ("DeepMelB", bell(440, 0.5, 1.2)),
        ("DeepFXA", riser(1.0)),
    ]


def bank_punch():
    return [
        ("PunchKickA", kick(50, 110, 0.22, 3.5, click=0.6)),
        ("PunchKickB", kick(55, 140, 0.16, 5.0, click=0.9)),
        ("PunchSnareA", snare(200, 0.06, 0.12, 1200, 9000, 0.7, 0.3)),
        ("PunchSnareB", snare(230, 0.05, 0.09, 1500, 10000, 0.8, 0.25)),
        ("PunchHHA", hat(0.035, 340, 7500, 0.12, tone=0.75)),
        ("PunchHHB", hat(0.05, 360, 8000, 0.15, tone=0.8)),
        ("PunchOHHA", hat(0.25, 340, 7000, 0.6, tone=0.75)),
        ("PunchOHHB", hat(0.38, 320, 6500, 0.85, tone=0.7)),
        ("PunchPercA", rim(950)),
        ("PunchPercB", zap(1800, 60, 0.18)),
        ("PunchPercC", sine_drum(90, 50, 0.02, 0.12, 0.3, drive=2.5)),
        ("PunchClapA", clap(0.008, 0.05, 1000, 8000)),
        ("PunchBassA", bass_note(41.2, 0.3, 0.7, 600)),
        ("PunchMelA", chord_stab(196, [0, 5, 7], 0.12, 0.4, 3000)),
        ("PunchMelB", zap(2600, 200, 0.3)),
        ("PunchFXA", env_decay(metallic(0.8, 240), 0.5)),
    ]


def bank_crunch():
    return [
        ("CrunchKickA", bitcrush(kick(54, 90, 0.25, 2.0, click=0.4), 7, 2)),
        ("CrunchKickB", bitcrush(kick(45, 60, 0.35, 1.6), 6, 3)),
        ("CrunchSnareA", bitcrush(snare(190, 0.07, 0.13, 1000, 8000, 0.6, 0.3), 6, 2)),
        ("CrunchSnareB", bitcrush(snare(170, 0.09, 0.18, 800, 6000, 0.5, 0.4), 5, 3)),
        ("CrunchHHA", bitcrush(hat(0.04, 320, 7000, 0.12), 6, 2)),
        ("CrunchHHB", bitcrush(hat(0.06, 300, 6000, 0.16), 6, 2)),
        ("CrunchOHHA", bitcrush(hat(0.28, 310, 6500, 0.65), 6, 2)),
        ("CrunchOHHB", bitcrush(hat(0.40, 290, 5500, 0.85), 5, 3)),
        ("CrunchPercA", bitcrush(rim(800), 6, 2)),
        ("CrunchPercB", bitcrush(sine_drum(250, 300, 0.015, 0.09, 0.25), 5, 3)),
        ("CrunchPercC", bitcrush(shaker(0.06, 0.16), 6, 2)),
        ("CrunchClapA", bitcrush(clap(0.013, 0.06, 800, 7000), 6, 2)),
        ("CrunchBassA", bitcrush(bass_note(49, 0.3, 0.7, 800), 7, 2)),
        ("CrunchMelA", bitcrush(chord_stab(233, [0, 4, 7, 11], 0.18, 0.55, 2500), 7, 2)),
        ("CrunchMelB", bitcrush(bell(523, 0.35, 0.9), 6, 2)),
        ("CrunchFXA", bitcrush(zap(3000, 80, 0.5), 5, 3)),
    ]


def bank_air():
    return [
        ("AirKickA", kick(46, 40, 0.5, 1.1)),
        ("AirKickB", lowpass(kick(40, 30, 0.65, 1.0), 600)),
        ("AirSnareA", snare(150, 0.14, 0.30, 400, 4000, 0.4, 0.6)),
        ("AirSnareB", gain(env_decay(bandpass(noise(0.7), 500, 3500), 0.35), 1.5)),
        ("AirHHA", hat(0.06, 260, 5000, 0.2, tone=0.3)),
        ("AirHHB", shaker(0.05, 0.14)),
        ("AirOHHA", hat(0.5, 250, 4500, 1.1, tone=0.3)),
        ("AirOHHB", env_decay(bandpass(noise(1.2), 4000, 10000), 0.45)),
        ("AirPercA", sine_drum(520, 180, 0.01, 0.10, 0.3)),
        ("AirPercB", bell(660, 0.3, 0.8)),
        ("AirPercC", sine_drum(95, 35, 0.03, 0.28, 0.7)),
        ("AirClapA", clap(0.016, 0.12, 500, 5000)),
        ("AirBassA", bass_note(36.7, 0.5, 1.2, 400)),
        ("AirMelA", chord_stab(174.6, [0, 7, 14, 17], 0.4, 1.3, 1600)),
        ("AirMelB", bell(880, 0.7, 1.8)),
        ("AirFXA", riser(1.6)),
    ]


# --- Patches ---------------------------------------------------------------


def build_patches():
    patches = []

    p = preset_bass("Deep Bass")
    patches.append(p)

    p = preset_bass("Acid Line").filter(frequency=30, resonance=90, filter_type=1, env2_to_freq=110)
    p.env_filter(attack=0, decay=45, sustain=0, release=15)
    patches.append(p)

    p = preset_bass("Sub Bass").osc1(wave=0).osc2(wave=0, semitones=52)
    p.filter(frequency=45, resonance=0, filter_type=1, env2_to_freq=40)
    patches.append(p)

    p = preset_bass("Rubber Bass").osc1(wave=13).filter(frequency=42, resonance=55, filter_type=1, env2_to_freq=85)
    p.env_amp(attack=0, decay=55, sustain=60, release=15)
    patches.append(p)

    patches.append(preset_pad("Warm Pad"))

    p = preset_pad("Glass Pad").osc1(wave=1, density=14, density_detune=26).filter(frequency=85, resonance=8)
    p.chorus(level=40, rate=25, feedback=50, mod_depth=80)
    patches.append(p)

    p = preset_pad("Dark Pad").filter(frequency=42, resonance=25, filter_type=1, env2_to_freq=50)
    p.lfo1(waveform=0, rate=25)
    patches.append(p)

    p = preset_pad("Choir Drift").osc1(wave=21).osc2(wave=21, semitones=64, cents=72)
    p.env_amp(attack=80, decay=100, sustain=127, release=100)
    patches.append(p)

    patches.append(preset_lead("Solar Lead"))

    p = preset_lead("Square Lead").osc1(wave=13).osc2(wave=13, semitones=64, cents=74)
    patches.append(p)

    p = preset_lead("Saw Scream").distortion(level=80, type=0).filter(frequency=85, resonance=35)
    patches.append(p)

    p = preset_lead("Soft Whistle").osc1(wave=0).osc2(wave=0, semitones=76).distortion(level=0)
    p.filter(frequency=90, resonance=5)
    patches.append(p)

    patches.append(preset_pluck("Pluck"))

    p = preset_pluck("Bell Pluck").osc2(wave=0, semitones=88, cents=64)
    p.env_amp(attack=0, decay=95, sustain=0, release=70)
    patches.append(p)

    p = preset_pluck("Tight Key").filter(frequency=55, resonance=30, filter_type=1, env2_to_freq=80)
    p.env_amp(attack=0, decay=60, sustain=0, release=25)
    patches.append(p)

    p = preset_pluck("Echo Pluck").voice(polyphony=2, portamento=0)
    p.env_amp(attack=0, decay=90, sustain=0, release=90)
    p.lfo1(waveform=0, rate=55)
    p.add_mod("LFO 1+", "filter frequency", depth=40)
    patches.append(p)

    # --- Second wave: mod-matrix and wavetable explorations ---

    p = preset_bass("Wobble Bass").filter(frequency=25, resonance=70, filter_type=1, env2_to_freq=40)
    p.lfo1(waveform=0, rate=50)
    p.add_mod("LFO 1+", "filter frequency", depth=110)
    patches.append(p)

    p = preset_bass("Neuro Growl").osc1(wave=18).osc2(wave=19, semitones=52)
    p.filter(frequency=35, resonance=60, filter_type=1, env2_to_freq=60)
    p.lfo2(waveform=1, rate=45)
    p.add_mod("LFO 2+", "osc 1 pulse width / index", depth=90)
    p.add_mod("LFO 2+/-", "filter frequency", depth=55)
    p.distortion(level=35, type=1)
    patches.append(p)

    p = preset_bass("Ring Bass").mixer(osc1_level=90, osc2_level=0, ring_mod=110)
    p.osc2(wave=0, semitones=71)  # ring partner a fifth up
    p.filter(frequency=55, resonance=20, filter_type=1, env2_to_freq=70)
    patches.append(p)

    p = preset_pad("PWM Strings").osc1(wave=12).osc2(wave=12, cents=72)
    p.env_amp(attack=45, decay=90, sustain=110, release=70)
    p.lfo1(waveform=1, rate=35)
    p.add_mod("LFO 1+/-", "osc 1 pulse width / index", depth=60)
    p.add_mod("LFO 1+", "osc 2 pulse width / index", depth=45)
    p.chorus(level=50, rate=28, feedback=55, mod_depth=75)
    patches.append(p)

    p = preset_pad("Vocal Morph").osc1(wave=22).osc2(wave=24, semitones=64, cents=70)
    p.lfo2(waveform=0, rate=20)
    p.add_mod("LFO 2+", "osc 1 pulse width / index", depth=85)
    p.add_mod("LFO 2+/-", "osc 2 pulse width / index", depth=70)
    patches.append(p)

    p = preset_pad("Breath Drone").voice(octave=62).mixer(osc1_level=95, osc2_level=85, noise=35)
    p.filter(frequency=50, resonance=35, filter_type=2, env2_to_freq=30)
    p.env_amp(attack=90, decay=100, sustain=127, release=110)
    p.lfo1(waveform=0, rate=18)
    p.add_mod("LFO 1+/-", "filter frequency", depth=45)
    patches.append(p)

    p = preset_pad("Dark Drone").voice(octave=60)
    p.filter(frequency=30, resonance=75, filter_type=1, env2_to_freq=20)
    p.lfo1(waveform=0, rate=12)
    p.add_mod("LFO 1+", "filter frequency", depth=35)
    p.add_mod("LFO 1+/-", "osc 2 pitch", depth=8)
    patches.append(p)

    p = preset_lead("Sync Scream").osc1(wave=16).osc2(wave=2, semitones=64)
    p.env3(attack=0, decay=80, sustain=0, release=40)
    p.add_mod("env 3", "osc 1 v-sync", depth=100)
    p.distortion(level=50, type=2)
    patches.append(p)

    p = preset_lead("8bit Hero").osc1(wave=18).osc2(wave=13, semitones=76)
    p.voice(polyphony=0, portamento=0)
    p.filter(frequency=95, resonance=0)
    p.distortion(level=45, type=5)  # bit reducer
    p.chorus(level=0)
    patches.append(p)

    p = preset_lead("Scream Whistle").osc1(wave=0).osc2(wave=0, semitones=88)
    p.voice(polyphony=0, portamento=55)
    p.filter(frequency=80, resonance=60, filter_type=3, env2_to_freq=40)
    p.lfo1(waveform=0, rate=80, delay=55)
    patches.append(p)

    p = preset_pluck("Organ Keys").osc1(wave=0).osc2(wave=13, semitones=76)
    p.mixer(osc1_level=110, osc2_level=55)
    p.env_amp(attack=0, decay=0, sustain=127, release=10)
    p.env_filter(attack=0, decay=0, sustain=127, release=10)
    p.filter(frequency=85, resonance=0, env2_to_freq=0)
    p.chorus(level=30, rate=45, feedback=40, mod_depth=50)
    patches.append(p)

    p = preset_pluck("Brass Stab").osc1(wave=2).osc2(wave=2, cents=66)
    p.voice(polyphony=2)
    p.env_amp(attack=5, decay=75, sustain=70, release=25)
    p.env_filter(attack=8, decay=55, sustain=35, release=25)
    p.filter(frequency=45, resonance=15, filter_type=1, env2_to_freq=95)
    p.add_mod("velocity", "filter frequency", depth=50)
    patches.append(p)

    p = preset_pluck("Glass Bells").osc1(wave=1).osc2(wave=0, semitones=83)  # +19 = minor 12th
    p.mixer(osc1_level=85, osc2_level=70, ring_mod=45)
    p.env_amp(attack=0, decay=100, sustain=0, release=95)
    p.env_filter(attack=0, decay=85, sustain=0, release=80)
    p.filter(frequency=75, resonance=10, env2_to_freq=45)
    patches.append(p)

    p = preset_pluck("Tape Keys").osc1(wave=1).osc2(wave=1, cents=67)
    p.env_amp(attack=3, decay=85, sustain=40, release=55)
    p.filter(frequency=60, resonance=8, env2_to_freq=35)
    p.lfo2(waveform=0, rate=30)
    p.add_mod("LFO 2+/-", "osc 1 & 2 pitch", depth=6)  # tape wow
    p.chorus(level=35, rate=20, feedback=45, mod_depth=60)
    patches.append(p)

    p = preset_lead("Laser Zap").osc1(wave=13).voice(polyphony=0)
    p.env3(attack=0, decay=45, sustain=0, release=10)
    p.add_mod("env 3", "osc 1 & 2 pitch", depth=127)
    p.env_amp(attack=0, decay=50, sustain=0, release=15)
    p.filter(frequency=100, resonance=20)
    patches.append(p)

    p = preset_pad("Riser FX").mixer(osc1_level=0, osc2_level=0, noise=120)
    p.filter(frequency=10, resonance=85, filter_type=3, env2_to_freq=127)
    p.env_amp(attack=95, decay=100, sustain=127, release=60)
    p.env_filter(attack=105, decay=90, sustain=127, release=50)
    patches.append(p)

    return patches


# --- Pack assembly ---------------------------------------------------------


def main():
    samples_dir = OUT / "samples"
    patches_dir = OUT / "patches"
    samples_dir.mkdir(parents=True, exist_ok=True)
    patches_dir.mkdir(parents=True, exist_ok=True)

    all_samples = bank_deep() + bank_punch() + bank_crunch() + bank_air()
    assert len(all_samples) == 64

    index_samples = []
    for i, (name, data) in enumerate(all_samples):
        write_wav(samples_dir / f"sample_{i}.wav", data)
        index_samples.append({"name": name, "url": f"samples/sample_{i}.wav"})
        print(f"  sample_{i:02d}  {name:14s} {len(data) / SR:.2f}s")

    index_patches = []
    for i, builder in enumerate(build_patches()):
        syx = builder.build_syx()
        (patches_dir / f"patch_{i}.syx").write_bytes(syx)
        name = builder.build()[:16].decode("ascii", "replace").strip()
        index_patches.append({"name": name, "url": f"patches/patch_{i}.syx"})
        print(f"  patch_{i:02d}   {name}")

    index = {
        "name": "Circuit Web Starter",
        "product": "circuit-tracks",
        "version": "1.0",
        "projects": [],
        "samples": index_samples,
        "patches": index_patches,
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"\nPack written to {OUT}")


if __name__ == "__main__":
    main()
