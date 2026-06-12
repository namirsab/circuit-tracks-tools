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
from circuit_tracks.song import parse_song, song_to_ncs  # noqa: E402

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
    p.filter(frequency=115, resonance=0, env2_to_freq=0)
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


# 96 family variations filling the bank to 128: 6 families x 4 archetypes x
# 4 variants (I..IV), each variant a distinct tone/envelope/mod character.
def build_variation_patches():  # noqa: PLR0915
    out = []
    roman = ["I", "II", "III", "IV"]

    # (archetype, osc1 wave, osc2 wave, osc2 semitones)
    BASS_ARCH = [("Deep", 2, 2, 52), ("Punch", 13, 13, 52), ("Hollow", 1, 0, 52), ("Gnarl", 18, 2, 52)]
    for arch, w1, w2, semis in BASS_ARCH:
        for v in range(4):
            p = preset_bass(f"{arch} Bass {roman[v]}").osc1(wave=w1).osc2(wave=w2, semitones=semis)
            if v == 0:  # clean
                p.filter(frequency=50 + 8, resonance=8, filter_type=1, env2_to_freq=80)
            elif v == 1:  # resonant squelch
                p.filter(frequency=28, resonance=78, filter_type=1, env2_to_freq=105)
                p.env_filter(attack=0, decay=50, sustain=0, release=15)
            elif v == 2:  # slow sub
                p.filter(frequency=38, resonance=12, filter_type=1, env2_to_freq=45)
                p.env_amp(attack=10, decay=90, sustain=110, release=45)
            else:  # driven
                p.filter(frequency=55, resonance=30, filter_type=1, env2_to_freq=70)
                p.distortion(level=45, type=1)
            out.append(p)

    PAD_ARCH = [("Silk", 2, 2), ("Glass", 1, 1), ("Vox", 21, 23), ("Haze", 12, 12)]
    for arch, w1, w2 in PAD_ARCH:
        for v in range(4):
            p = preset_pad(f"{arch} Pad {roman[v]}").osc1(wave=w1).osc2(wave=w2, semitones=64, cents=70)
            if v == 0:  # slow swell
                p.env_amp(attack=75, decay=95, sustain=127, release=95)
            elif v == 1:  # shimmer (slow pitch drift)
                p.lfo2(waveform=0, rate=22)
                p.add_mod("LFO 2+/-", "osc 2 pitch", depth=7)
                p.chorus(level=45, rate=24, feedback=55, mod_depth=75)
            elif v == 2:  # dark
                p.filter(frequency=38, resonance=22, filter_type=1, env2_to_freq=40)
                p.voice(octave=62)
            else:  # airy (breath noise)
                p.mixer(osc1_level=90, osc2_level=80, noise=28)
                p.filter(frequency=80, resonance=10, env2_to_freq=30)
            out.append(p)

    LEAD_ARCH = [("Solar", 2, 13), ("Square", 13, 13), ("Nasty", 18, 19), ("Sync", 16, 2)]
    for arch, w1, w2 in LEAD_ARCH:
        for v in range(4):
            p = preset_lead(f"{arch} Lead {roman[v]}").osc1(wave=w1).osc2(wave=w2, semitones=76)
            if v == 0:  # straight
                p.distortion(level=20, type=0)
            elif v == 1:  # vibrato
                p.lfo1(waveform=0, rate=82, delay=50)
                p.add_mod("LFO 1+/-", "osc 1 & 2 pitch", depth=10)
            elif v == 2:  # glide
                p.voice(polyphony=0, portamento=60)
                p.filter(frequency=62, resonance=18)
            else:  # screaming
                p.distortion(level=70, type=2)
                p.filter(frequency=88, resonance=40)
            out.append(p)

    PLUCK_ARCH = [("Crystal", 1, 0), ("Saw", 2, 2), ("Kalimba", 0, 0), ("Digi", 20, 13)]
    for arch, w1, w2 in PLUCK_ARCH:
        for v in range(4):
            p = preset_pluck(f"{arch} Pluck {roman[v]}").osc1(wave=w1).osc2(wave=w2, semitones=76 if v == 1 else 64)
            if v == 0:  # tight
                p.env_amp(attack=0, decay=55, sustain=0, release=20)
                p.env_filter(attack=0, decay=45, sustain=0, release=18)
            elif v == 1:  # ringing octave
                p.env_amp(attack=0, decay=100, sustain=0, release=85)
            elif v == 2:  # resonant
                p.filter(frequency=35, resonance=60, filter_type=1, env2_to_freq=95)
            else:  # velo-sensitive snap
                p.add_mod("velocity", "filter frequency", depth=55)
                p.env_amp(attack=0, decay=70, sustain=0, release=30)
            out.append(p)

    KEYS_ARCH = [("EP", 0, 1), ("Organ", 0, 13), ("Clav", 12, 12), ("Stab", 2, 2)]
    for arch, w1, w2 in KEYS_ARCH:
        for v in range(4):
            p = preset_pluck(f"{arch} Keys {roman[v]}").osc1(wave=w1).osc2(wave=w2, semitones=76)
            p.voice(polyphony=2)
            if arch == "Organ":
                p.env_amp(attack=0, decay=0, sustain=127, release=8)
                p.env_filter(attack=0, decay=0, sustain=127, release=8)
                p.filter(frequency=118 - v * 4, resonance=0, env2_to_freq=0)
                p.chorus(level=25 + v * 12, rate=40 + v * 8, feedback=40, mod_depth=50)
            elif v == 0:  # soft
                p.env_amp(attack=2, decay=85, sustain=35, release=45)
                p.filter(frequency=58, resonance=8, env2_to_freq=40)
            elif v == 1:  # bright comp
                p.filter(frequency=72, resonance=20, env2_to_freq=65)
                p.env_amp(attack=0, decay=65, sustain=55, release=20)
            elif v == 2:  # phased (chorus heavy)
                p.chorus(level=60, rate=30, feedback=65, mod_depth=85)
                p.env_amp(attack=2, decay=80, sustain=45, release=40)
            else:  # punchy stab
                p.env_amp(attack=0, decay=55, sustain=20, release=18)
                p.env_filter(attack=0, decay=45, sustain=15, release=18)
                p.filter(frequency=48, resonance=25, env2_to_freq=85)
            out.append(p)

    FX_ARCH = [("Drone", 2, 2), ("Texture", 22, 25), ("Swell", 1, 1), ("Fall", 13, 0)]
    for arch, w1, w2 in FX_ARCH:
        for v in range(4):
            p = preset_pad(f"{arch} FX {roman[v]}").osc1(wave=w1).osc2(wave=w2, semitones=64, cents=68)
            if arch == "Drone":
                p.voice(octave=60 + (v % 2) * 2)
                p.filter(frequency=26 + v * 10, resonance=55 + v * 8, filter_type=1, env2_to_freq=25)
                p.lfo1(waveform=0, rate=10 + v * 6)
                p.add_mod("LFO 1+", "filter frequency", depth=30 + v * 10)
            elif arch == "Texture":
                p.lfo2(waveform=4 if v >= 2 else 0, rate=15 + v * 12)
                p.add_mod("LFO 2+", "osc 1 pulse width / index", depth=60 + v * 15)
                p.add_mod("LFO 2+/-", "filter frequency", depth=30 + v * 8)
            elif arch == "Swell":
                p.mixer(osc1_level=70, osc2_level=60, noise=40 + v * 18)
                p.env_amp(attack=80 + v * 12, decay=100, sustain=127, release=80)
                p.filter(frequency=30 + v * 14, resonance=45, filter_type=3, env2_to_freq=100)
                p.env_filter(attack=90 + v * 10, decay=90, sustain=127, release=60)
            else:  # Fall: pitched drops via env3
                p.voice(polyphony=0)
                p.env3(attack=0, decay=40 + v * 22, sustain=0, release=20)
                p.add_mod("env 3", "osc 1 & 2 pitch", depth=127 - v * 20)
                p.env_amp(attack=0, decay=55 + v * 18, sustain=0, release=25)
            out.append(p)

    return out


# --- Demo projects ---------------------------------------------------------
# 16 individually composed grooves, four per sample bank, written as song
# dicts and compiled to .ncs with song_to_ncs(). All patterns are 32 steps.
# Each project leans on a different trick: probability ghosts, per-step
# sample flips, tie slides, macro automation lanes, sidechain, FX throws.


def _drums(hits, **extra):
    # hits: {pos: vel} or {pos: (vel, {extras})} — extras: probability, sample
    steps = {}
    for i, v in hits.items():
        steps[str(i)] = {"velocity": v[0], **v[1]} if isinstance(v, tuple) else {"velocity": v}
    return {"steps": steps, **extra}


def _synth(notes, **extra):
    # notes: {pos: (note_or_chord, vel, gate)} or (..., {extras: tie/probability/macros})
    steps = {}
    for i, spec in notes.items():
        st = {"velocity": spec[1], "gate": spec[2], **(spec[3] if len(spec) > 3 else {})}
        if isinstance(spec[0], list):
            st["notes"] = spec[0]
        else:
            st["note"] = spec[0]
        steps[str(i)] = st
    return {"steps": steps, **extra}


def _every(start, stride, vel, end=32):
    return {i: vel for i in range(start, end, stride)}


def _project(name, bpm, color, sounds, patterns, order, fx, swing=50):
    return {
        "name": name,
        "bpm": bpm,
        "swing": swing,
        "color": color,
        "scale": {"root": "C", "type": "chromatic"},
        "sounds": sounds,
        "fx": fx,
        "patterns": patterns,
        "song": order,
    }


# Deep bank: 0 kickA 1 kickB 2 snA 3 snB 4 hh 5 hh2 6 ohh 7 ohh2
#            8 blip 9 tom 10 shaker 11 clap 12 bass 13 stab 14 bell 15 riser
# Punch +16, Crunch +32, Air +48 (Air: 53 shaker, 55 wash, 57/62 bells, 58 tom)


def _neon_harbor():
    """Deep house: ghost kick, OHH flips, shaker probability, sidechained
    Am9/Fmaj7 keys, slow filter sweep riding the bass."""
    a = 45
    drums = {
        "drum1": _drums(_every(0, 8, 112) | {30: (58, {"probability": 0.45})}),
        "drum2": _drums({8: 102, 24: 106, 28: (64, {"probability": 0.3, "sample": 2})}),
        "drum3": _drums({**_every(4, 8, 90), 12: (84, {"sample": 6}), 28: (88, {"sample": 6})}),
        "drum4": _drums({i: (44 + (i % 8) * 3, {"probability": 0.7}) for i in range(2, 32, 4)}),
    }
    bass1 = _synth(
        {
            0: (a, 104, 1.5),
            7: (a, 88, 0.8),
            8: (a, 100, 1.5),
            15: (a + 12, 84, 0.8),
            16: (a, 104, 1.5),
            23: (a + 7, 88, 0.8),
            24: (a + 3, 100, 1.5),
            30: (a - 2, 92, 1),
        },
        macros={"5": {str(s): 18 + s * 3 for s in range(0, 32, 4)}},
    )
    bass2 = _synth(
        {
            0: (a, 104, 1.5),
            8: (a + 8, 100, 1.5),
            16: (a + 7, 104, 1.5),
            24: (a + 5, 100, 1.5),
            28: (a + 12, 90, 0.8),
            30: (a + 10, 86, 0.8),
        },
        macros={"5": {str(s): 110 - s * 2 for s in range(0, 32, 4)}},
    )
    keys1 = _synth({4: ([57, 64, 71], 84, 3), 12: ([57, 64, 71], 70, 2), 20: ([57, 65, 69], 84, 3)})
    keys2 = _synth({4: ([53, 60, 69], 84, 3), 12: ([55, 62, 71], 78, 2), 20: ([57, 64, 72], 86, 4)})
    return _project(
        "Neon Harbor",
        122,
        9,
        sounds={
            "synth1": {"preset": "bass", "name": "Harbor Sub", "params": {"filter_frequency": 20}},
            "synth2": {"preset": "pluck", "name": "Neon Keys", "params": {"chorus_level": 40, "env1_release": 70}},
            "drum1": {"sample": 0},
            "drum2": {"sample": 11},
            "drum3": {"sample": 4, "decay": 96},
            "drum4": {"sample": 10, "level": 84, "pan": 80},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": bass1, "synth2": keys1}},
            "p2": {"length": 32, "tracks": drums | {"synth1": bass2, "synth2": keys2}},
        },
        order=["p1", "p1", "p2", "p2"],
        fx={
            "reverb_preset": 2,
            "delay_preset": 6,
            "reverb_sends": {"synth2": 35, "drum2": 28},
            "delay_sends": {"synth2": 25},
            "sidechain": {"synth2": {"preset": 3, "source": "drum1", "depth": 95}},
        },
    )


def _velvet_loop():
    """Lo-fi swung house: dusty kick B, rim ghosts, tied EP chords with a
    delay throw on the second half."""
    drums = {
        "drum1": _drums({0: 110, 8: 104, 16: 110, 24: 104, 11: (70, {"probability": 0.5})}),
        "drum2": _drums({8: 96, 24: 100}),
        "drum3": _drums({4: 80, 6: (48, {"probability": 0.6}), 12: 84, 20: 80, 22: (52, {"probability": 0.6}), 28: 84}),
        "drum4": _drums({3: (58, {"probability": 0.65}), 13: 62, 19: (54, {"probability": 0.65}), 29: 60}),
    }
    keys1 = _synth(
        {
            0: ([53, 60, 65], 78, 4, {"tie": True}),
            4: ([53, 60, 65], 60, 2),
            16: ([51, 58, 65], 78, 4, {"tie": True}),
            20: ([51, 58, 65], 60, 2),
        },
        mixer={"delay_send": {"24": 80, "28": 10}},
    )
    keys2 = _synth(
        {
            0: ([48, 57, 64], 78, 4, {"tie": True}),
            8: ([48, 57, 64], 58, 2),
            16: ([50, 58, 65], 80, 6, {"tie": True}),
            26: ([53, 60, 67], 64, 2),
        },
        mixer={"delay_send": {"24": 90, "28": 10}},
    )
    bass = _synth({0: (41, 96, 3), 12: (41, 70, 1), 16: (39, 96, 3), 28: (44, 80, 1.5)})
    return _project(
        "Velvet Loop",
        116,
        1,
        sounds={
            "synth1": {"preset": "bass", "name": "Felt Bass", "params": {"filter_frequency": 32}},
            "synth2": {
                "preset": "pluck",
                "name": "Velvet EP",
                "params": {"env1_attack": 8, "env1_release": 60, "chorus_level": 55, "filter_frequency": 48},
            },
            "drum1": {"sample": 1, "decay": 110},
            "drum2": {"sample": 3, "pitch": 60},
            "drum3": {"sample": 5, "decay": 80, "level": 88},
            "drum4": {"sample": 9, "pitch": 70, "level": 80},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": keys1}},
            "p2": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": keys2}},
        },
        order=["p1", "p2"],
        fx={
            "reverb_preset": 1,
            "delay_preset": 8,
            "reverb_sends": {"synth2": 30, "drum2": 35},
            "delay_sends": {"synth2": 20},
        },
        swing=58,
    )


def _glass_garden():
    """Melodic house: 16th arp with a velocity wave, bell hits on drum 4,
    riser flip at the turnaround."""
    arp_notes = [48, 55, 60, 64, 67, 64, 60, 55]
    arp1 = {i: (arp_notes[(i // 2) % 8], 64 + 28 * (i % 8 == 0) + 12 * (i % 4 == 0), 0.6) for i in range(0, 32, 2)}
    arp2 = {i: (arp_notes[(i // 2) % 8] + 5, 64 + 28 * (i % 8 == 0) + 12 * (i % 4 == 0), 0.6) for i in range(0, 32, 2)}
    drums = {
        "drum1": _drums(_every(0, 8, 110)),
        "drum2": _drums({8: 98, 24: 100}),
        "drum3": _drums(_every(4, 8, 86) | {18: (60, {"probability": 0.55})}),
        "drum4": _drums(
            {0: (72, {"sample": 14}), 11: (58, {"sample": 14, "probability": 0.6}), 22: (66, {"sample": 14})}
        ),
    }
    drums2 = dict(drums) | {"drum2": _drums({8: 98, 24: 100, 28: (74, {"sample": 15})})}
    bass = _synth({i: (36 + (7 if i >= 16 else 0), 100, 1.8) for i in (0, 6, 8, 14, 16, 22, 24, 30)})
    return _project(
        "Glass Garden",
        124,
        5,
        sounds={
            "synth1": {"preset": "bass", "name": "Garden Sub", "params": {"filter_frequency": 26}},
            "synth2": {
                "preset": "pluck",
                "name": "Glass Arp",
                "params": {"osc1_wave": 1, "env1_release": 45, "filter_frequency": 30},
            },
            "drum1": {"sample": 0},
            "drum2": {"sample": 11},
            "drum3": {"sample": 4, "decay": 90},
            "drum4": {"sample": 14, "pitch": 76, "level": 78, "pan": 48},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": _synth(arp1)}},
            "p2": {"length": 32, "tracks": drums2 | {"synth1": bass, "synth2": _synth(arp2)}},
        },
        order=["p1", "p1", "p2", "p2"],
        fx={
            "reverb_preset": 4,
            "delay_preset": 5,
            "reverb_sends": {"synth2": 40, "drum4": 50},
            "delay_sends": {"synth2": 35},
            "sidechain": {"synth2": {"preset": 2, "source": "drum1", "depth": 70}},
        },
    )


def _midnight_mall():
    """UK garage: broken kick, shuffled zig-zag hats, chopped chord stabs
    with probability, tied sub slides."""
    chop = {
        2: ([63, 67, 70], 88, 0.8),
        5: ([63, 67, 70], 60, 0.5, {"probability": 0.7}),
        10: ([62, 65, 70], 84, 0.8),
        18: ([63, 67, 70], 88, 0.8),
        21: ([65, 68, 72], 64, 0.5, {"probability": 0.55}),
        26: ([62, 65, 70], 84, 0.8),
    }
    drums = {
        "drum1": _drums({0: 114, 10: 92, 16: 110, 22: 90}),
        "drum2": _drums({8: 104, 24: 106, 30: (62, {"probability": 0.5})}),
        "drum3": _drums(
            {
                i: (88 if i % 8 == 4 else 46 + (i * 5) % 22, {"probability": 0.8 if i % 8 != 4 else 1.0})
                for i in range(2, 32, 2)
            }
        ),
        "drum4": _drums({7: (66, {"probability": 0.6}), 15: 70, 23: (60, {"probability": 0.6}), 31: 72}),
    }
    bass1 = _synth(
        {
            0: (39, 102, 2, {"tie": True}),
            4: (39, 90, 1),
            16: (37, 102, 2, {"tie": True}),
            20: (37, 90, 1),
            28: (42, 94, 1),
        }
    )
    bass2 = _synth(
        {0: (39, 102, 2), 8: (44, 96, 1.5, {"tie": True}), 11: (42, 88, 1), 16: (37, 102, 2), 24: (34, 100, 3)}
    )
    return _project(
        "Midnight Mall",
        130,
        12,
        sounds={
            "synth1": {"preset": "bass", "name": "Mall Sub", "params": {"filter_frequency": 24, "portamento_rate": 45}},
            "synth2": {"preset": "pluck", "name": "Chop Chord", "params": {"env1_release": 30, "filter_frequency": 55}},
            "drum1": {"sample": 0},
            "drum2": {"sample": 2},
            "drum3": {"sample": 4, "decay": 84},
            "drum4": {"sample": 8, "pitch": 72, "level": 82},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": bass1, "synth2": _synth(chop)}},
            "p2": {"length": 32, "tracks": drums | {"synth1": bass2, "synth2": _synth(chop)}},
        },
        order=["p1", "p2"],
        fx={
            "reverb_preset": 1,
            "delay_preset": 4,
            "reverb_sends": {"synth2": 22, "drum2": 26},
            "delay_sends": {"synth2": 28},
        },
        swing=55,
    )


def _concrete_pulse():
    """Peak techno: rolling accented 16th bass under a rising filter lane,
    zap percs with a falling pitch lane, sidechained stabs."""
    bass = {i: (41 - 12 * (i % 4 == 2), 70 + 44 * (i % 4 == 0), 0.7) for i in range(0, 32, 2)}
    drums = {
        "drum1": _drums(_every(0, 8, 122)),
        "drum2": _drums({8: 98, 24: 98}),
        "drum3": _drums(_every(4, 8, 92) | _every(2, 8, 50)),
        "drum4": _drums(
            {3: (78, {"probability": 0.7}), 11: (70, {"probability": 0.7}), 19: 82, 27: (74, {"probability": 0.7})},
            params={"pitch": {"0": 80, "16": 64, "28": 52}},
        ),
    }
    stabs1 = _synth({12: (65, 96, 0.6), 28: (65, 92, 0.6)})
    stabs2 = _synth({4: (65, 96, 0.6), 12: (68, 92, 0.6), 20: (65, 96, 0.6), 28: (72, 98, 0.8)})
    syn1 = {"macros": {"5": {str(s): 20 + s * 3 for s in range(0, 32, 2)}}}
    return _project(
        "Concrete Pulse",
        132,
        0,
        sounds={
            "synth1": {
                "preset": "bass",
                "name": "Pulse Roll",
                "params": {"filter_frequency": 15, "filter_resonance": 35},
            },
            "synth2": {
                "preset": "lead",
                "name": "Concrete Stab",
                "params": {"env1_release": 25, "distortion_level": 35},
            },
            "drum1": {"sample": 16},
            "drum2": {"sample": 27},
            "drum3": {"sample": 20, "decay": 88},
            "drum4": {"sample": 25, "level": 86},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": _synth(bass, **syn1), "synth2": stabs1}},
            "p2": {"length": 32, "tracks": drums | {"synth1": _synth(bass, **syn1), "synth2": stabs2}},
        },
        order=["p1", "p1", "p2", "p2"],
        fx={
            "reverb_preset": 3,
            "delay_preset": 3,
            "reverb_sends": {"synth2": 30, "drum2": 20},
            "delay_sends": {"synth2": 22, "drum4": 30},
            "sidechain": {
                "synth1": {"preset": 4, "source": "drum1", "depth": 80},
                "synth2": {"preset": 3, "source": "drum1", "depth": 100},
            },
        },
    )


def _voltage_run():
    """Acid: tied 16th line with octave jumps and accents, filter and
    resonance lanes ramping all pattern long, metallic sprinkles."""
    line1 = {
        0: (45, 112, 0.7),
        2: (45, 64, 0.5, {"tie": True}),
        4: (57, 100, 0.5),
        6: (45, 64, 0.5),
        8: (45, 110, 0.7),
        10: (48, 70, 0.5, {"tie": True}),
        12: (48, 96, 0.5),
        14: (45, 60, 0.5),
        16: (45, 112, 0.7),
        18: (45, 64, 0.5),
        20: (57, 104, 0.5, {"tie": True}),
        22: (55, 72, 0.5),
        24: (45, 110, 0.7),
        26: (43, 88, 0.5),
        28: (45, 96, 0.5),
        30: (52, 90, 0.5, {"tie": True}),
    }
    line2 = {i: (n + 5, v, g, *e) for i, (n, v, g, *e) in line1.items()}
    drums = {
        "drum1": _drums(_every(0, 8, 118)),
        "drum2": _drums({8: 92, 24: 94}),
        "drum3": _drums(_every(4, 8, 88)),
        "drum4": _drums(
            {
                5: (54, {"probability": 0.4}),
                13: (58, {"probability": 0.4}),
                21: (50, {"probability": 0.4}),
                29: (60, {"probability": 0.4}),
            }
        ),
    }
    lanes = {
        "macros": {"5": {str(s): 10 + s * 3 for s in range(0, 32, 2)}, "6": {"0": 40, "8": 65, "16": 90, "24": 115}}
    }
    return _project(
        "Voltage Run",
        138,
        3,
        sounds={
            "synth1": {
                "preset": "bass",
                "name": "Voltage 303",
                "params": {"filter_frequency": 8, "filter_resonance": 70, "env2_decay": 55},
            },
            "synth2": {"preset": "pad", "name": "Static Air", "params": {"filter_frequency": 30}},
            "drum1": {"sample": 17},
            "drum2": {"sample": 27},
            "drum3": {"sample": 21, "decay": 78},
            "drum4": {"sample": 31, "level": 70, "decay": 70},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": _synth(line1, **lanes)}},
            "p2": {"length": 32, "tracks": drums | {"synth1": _synth(line2, **lanes)}},
        },
        order=["p1", "p1", "p1", "p2"],
        fx={
            "reverb_preset": 2,
            "delay_preset": 2,
            "reverb_sends": {"drum2": 25},
            "delay_sends": {"synth1": 18, "drum4": 45},
        },
    )


def _iron_orbit():
    """EBM: syncopated kick, rim 16ths on probability, gliding minor-key
    lead riff, driven tom fills closing the loop."""
    riff = {
        0: (48, 106, 1.5),
        6: (48, 88, 0.8),
        8: (51, 100, 1, {"tie": True}),
        11: (50, 84, 0.8),
        16: (48, 106, 1.5),
        22: (55, 92, 0.8),
        24: (53, 100, 1.5, {"tie": True}),
        30: (51, 88, 0.8),
    }
    drums = {
        "drum1": _drums({0: 118, 7: 96, 8: 112, 16: 118, 23: 96, 24: 112}),
        "drum2": _drums({8: 100, 24: 102}),
        "drum3": _drums({i: (52 + (i % 4) * 8, {"probability": 0.5}) for i in range(0, 32, 2)}),
        "drum4": _drums({12: (74, {"probability": 0.6}), 28: 80}),
    }
    drums2 = dict(drums) | {
        "drum4": _drums({12: 74, 26: (84, {"sample": 26}), 28: (92, {"sample": 26}), 30: (100, {"sample": 26})}),
    }
    return _project(
        "Iron Orbit",
        126,
        10,
        sounds={
            "synth1": {
                "preset": "lead",
                "name": "Orbit Riff",
                "params": {"portamento_rate": 38, "distortion_level": 30, "filter_frequency": 45},
            },
            "synth2": {
                "preset": "pad",
                "name": "Iron Drone",
                "params": {"filter_frequency": 28, "keyboard_octave": 62},
            },
            "drum1": {"sample": 16, "decay": 105},
            "drum2": {"sample": 18},
            "drum3": {"sample": 24, "level": 84},
            "drum4": {"sample": 26, "pitch": 58},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": _synth(riff), "synth2": _synth({0: ([36, 43], 70, 16)})}},
            "p2": {
                "length": 32,
                "tracks": drums2 | {"synth1": _synth(riff), "synth2": _synth({0: ([36, 43], 70, 16)})},
            },
        },
        order=["p1", "p2"],
        fx={
            "reverb_preset": 3,
            "delay_preset": 7,
            "reverb_sends": {"synth1": 18, "drum2": 30},
            "delay_sends": {"synth1": 25},
        },
    )


def _strobe_sector():
    """Hard techno: offbeat bass stabs, one-note 16th lead on probability,
    snare build with rising velocity through the second pattern."""
    drums1 = {
        "drum1": _drums(_every(0, 8, 124)),
        "drum2": _drums({8: 96, 24: 96}),
        "drum3": _drums(_every(4, 8, 94)),
        "drum4": _drums({0: (60, {"sample": 31})}),
    }
    drums2 = dict(drums1) | {
        "drum2": _drums({8: 96, 24: 96} | {i: (50 + (i - 16) * 4, {}) for i in range(16, 32, 2)}),
    }
    stabs = _synth({i: (31, 108, 0.8) for i in (4, 12, 20, 28)})
    lead = _synth({i: (67, 58 + (i % 8) * 6, 0.4, {"probability": 0.8}) for i in range(0, 32, 2)})
    return _project(
        "Strobe Sector",
        142,
        13,
        sounds={
            "synth1": {
                "preset": "bass",
                "name": "Strobe Stab",
                "params": {"filter_frequency": 40, "distortion_level": 40},
            },
            "synth2": {"preset": "lead", "name": "Strobe Blip", "params": {"env1_release": 12, "filter_frequency": 70}},
            "drum1": {"sample": 17, "decay": 100},
            "drum2": {"sample": 19},
            "drum3": {"sample": 22, "decay": 70},
            "drum4": {"sample": 31, "decay": 110, "level": 75},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums1 | {"synth1": stabs, "synth2": lead}},
            "p2": {"length": 32, "tracks": drums2 | {"synth1": stabs, "synth2": lead}},
        },
        order=["p1", "p1", "p1", "p2"],
        fx={
            "reverb_preset": 0,
            "delay_preset": 1,
            "reverb_sends": {"drum2": 18},
            "delay_sends": {"synth2": 30},
            "sidechain": {"synth2": {"preset": 5, "source": "drum1", "depth": 85}},
        },
    )


def _rust_funk():
    """Funk break: ghost-note snares, OHH flips, syncopated tie bass,
    clav-style stabs answering the bassline."""
    drums = {
        "drum1": _drums({0: 118, 10: 94, 13: (72, {"probability": 0.6}), 16: 112, 26: 96}),
        "drum2": _drums(
            {
                8: 110,
                11: (44, {"probability": 0.6}),
                21: (40, {"probability": 0.6}),
                24: 112,
                29: (46, {"probability": 0.55}),
            }
        ),
        "drum3": _drums({**_every(2, 4, 80), 14: (86, {"sample": 38}), 30: (88, {"sample": 38})}),
        "drum4": _drums({5: (64, {"probability": 0.7}), 13: 70, 23: (62, {"probability": 0.7})}),
    }
    bass1 = _synth(
        {
            0: (40, 108, 1.5),
            6: (40, 84, 0.8, {"tie": True}),
            8: (43, 96, 1),
            14: (45, 88, 0.8),
            16: (40, 106, 1.5),
            24: (35, 100, 2, {"tie": True}),
            28: (38, 90, 1),
        }
    )
    bass2 = _synth(
        {
            0: (40, 108, 1.5),
            8: (47, 98, 1),
            12: (45, 90, 0.8, {"tie": True}),
            16: (43, 104, 1.5),
            22: (40, 92, 0.8),
            24: (38, 102, 2),
            30: (42, 94, 0.8),
        }
    )
    clav = _synth(
        {
            4: ([52, 59], 92, 0.6),
            7: ([52, 59], 70, 0.4, {"probability": 0.7}),
            20: ([50, 57], 92, 0.6),
            27: ([55, 62], 80, 0.5, {"probability": 0.6}),
        }
    )
    return _project(
        "Rust Funk",
        104,
        2,
        sounds={
            "synth1": {
                "preset": "bass",
                "name": "Rust Bass",
                "params": {"filter_frequency": 38, "filter_resonance": 25},
            },
            "synth2": {
                "preset": "pluck",
                "name": "Rust Clav",
                "params": {"osc1_wave": 12, "env1_release": 22, "filter_frequency": 60},
            },
            "drum1": {"sample": 32},
            "drum2": {"sample": 34},
            "drum3": {"sample": 36, "decay": 82},
            "drum4": {"sample": 42, "level": 80, "pan": 86},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": bass1, "synth2": clav}},
            "p2": {"length": 32, "tracks": drums | {"synth1": bass2, "synth2": clav}},
        },
        order=["p1", "p2"],
        fx={"reverb_preset": 1, "delay_preset": 9, "reverb_sends": {"drum2": 22}, "delay_sends": {"synth2": 20}},
        swing=54,
    )


def _circuit_breaker():
    """Electro: zap hits riding a falling pitch lane, octave-bounce bass,
    sparse bleep melody, kick A/B flips."""
    drums = {
        "drum1": _drums({0: 116, 8: 108, 16: 114, 26: (100, {"sample": 33})}),
        "drum2": _drums({8: 104, 24: 106}),
        "drum3": _drums({i: (46 + (i % 8) * 4, {"probability": 0.75}) for i in range(0, 32, 2)}),
        "drum4": _drums({6: 84, 14: 78, 22: 86, 30: 80}, params={"pitch": {"0": 96, "8": 80, "16": 64, "24": 48}}),
    }
    bass = _synth({i: (45 - 12 * ((i // 4) % 2), 102, 0.8) for i in range(0, 32, 4)})
    bleeps1 = _synth({10: (69, 88, 0.4), 14: (72, 80, 0.4, {"probability": 0.7}), 26: (67, 86, 0.4)})
    bleeps2 = _synth({2: (69, 88, 0.4), 10: (74, 84, 0.4), 18: (72, 86, 0.4, {"probability": 0.7}), 26: (76, 90, 0.5)})
    return _project(
        "Circuit Breaker",
        112,
        7,
        sounds={
            "synth1": {"preset": "bass", "name": "Breaker Bass", "params": {"osc1_wave": 13, "filter_frequency": 42}},
            "synth2": {
                "preset": "pluck",
                "name": "Data Bleep",
                "params": {"osc1_wave": 20, "env1_release": 18, "filter_frequency": 75},
            },
            "drum1": {"sample": 32},
            "drum2": {"sample": 43},
            "drum3": {"sample": 40, "level": 82},
            "drum4": {"sample": 47, "level": 88},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": bleeps1}},
            "p2": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": bleeps2}},
        },
        order=["p1", "p2"],
        fx={
            "reverb_preset": 2,
            "delay_preset": 10,
            "reverb_sends": {"synth2": 26},
            "delay_sends": {"synth2": 38, "drum4": 30},
        },
    )


def _pixel_alley():
    """Boom bap: lazy swung kick, fat snare, tied long bass, bell hook on
    probability, EP chords holding the changes."""
    drums = {
        "drum1": _drums({0: 114, 7: (92, {"probability": 0.8}), 17: 108, 24: 100}),
        "drum2": _drums({8: 118, 24: 116}),
        "drum3": _drums({i: (78 if i % 4 == 0 else 52, {}) for i in range(0, 32, 2)}),
        "drum4": _drums(
            {
                4: (66, {"probability": 0.6, "sample": 46}),
                15: (58, {"sample": 46}),
                22: (62, {"probability": 0.5, "sample": 46}),
            }
        ),
    }
    bass = _synth({0: (38, 102, 4, {"tie": True}), 8: (38, 80, 2), 16: (34, 102, 4, {"tie": True}), 26: (41, 88, 1.5)})
    keys1 = _synth({0: ([50, 57, 65], 72, 6), 16: ([48, 55, 64], 72, 6)})
    keys2 = _synth({0: ([50, 57, 65], 72, 6), 16: ([53, 60, 69], 74, 6), 28: ([55, 62, 70], 60, 3)})
    return _project(
        "Pixel Alley",
        92,
        11,
        sounds={
            "synth1": {"preset": "bass", "name": "Alley Bass", "params": {"filter_frequency": 30}},
            "synth2": {
                "preset": "pluck",
                "name": "Pixel EP",
                "params": {"env1_attack": 6, "env1_release": 70, "chorus_level": 45},
            },
            "drum1": {"sample": 33, "decay": 115},
            "drum2": {"sample": 35, "decay": 105},
            "drum3": {"sample": 37, "decay": 76, "level": 84},
            "drum4": {"sample": 46, "pitch": 70, "level": 80},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": keys1}},
            "p2": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": keys2}},
        },
        order=["p1", "p1", "p2", "p2"],
        fx={
            "reverb_preset": 1,
            "delay_preset": 12,
            "reverb_sends": {"drum2": 30, "drum4": 40},
            "delay_sends": {"synth2": 15},
        },
        swing=62,
    )


def _crunch_time():
    """Big beat: heavy break, power-chord stabs, distortion lane driving the
    kick harder through the second pattern, zap FX turnaround."""
    drums1 = {
        "drum1": _drums({0: 122, 5: 100, 16: 118, 21: 102, 26: (96, {"probability": 0.7})}),
        "drum2": _drums({8: 114, 24: 116}),
        "drum3": _drums({**_every(2, 4, 84), 18: (88, {"sample": 38})}),
        "drum4": _drums({30: (90, {"sample": 47})}),
    }
    drums2 = dict(drums1) | {
        "drum1": _drums(
            {0: 122, 5: 100, 16: 118, 21: 102, 26: 96}, params={"distortion": {"0": 20, "16": 60, "28": 100}}
        ),
        "drum2": _drums({8: 114, 24: 116, 28: 80, 29: 88, 30: 100, 31: 112}),
    }
    stabs = _synth(
        {
            0: ([43, 50, 55], 104, 1.5),
            11: ([43, 50, 55], 88, 0.8),
            16: ([41, 48, 53], 104, 1.5),
            27: ([46, 53, 58], 92, 0.8),
        }
    )
    bass = _synth({0: (31, 106, 2), 11: (31, 88, 1), 16: (29, 106, 2), 27: (34, 92, 1)})
    return _project(
        "Crunch Time",
        126,
        4,
        sounds={
            "synth1": {
                "preset": "bass",
                "name": "Crunch Bass",
                "params": {"filter_frequency": 36, "distortion_level": 35},
            },
            "synth2": {
                "preset": "pluck",
                "name": "Power Stab",
                "params": {"distortion_level": 45, "env1_release": 35, "filter_frequency": 50},
            },
            "drum1": {"sample": 32, "decay": 108},
            "drum2": {"sample": 34},
            "drum3": {"sample": 36, "decay": 80},
            "drum4": {"sample": 47},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums1 | {"synth1": bass, "synth2": stabs}},
            "p2": {"length": 32, "tracks": drums2 | {"synth1": bass, "synth2": stabs}},
        },
        order=["p1", "p2"],
        fx={"reverb_preset": 2, "delay_preset": 0, "reverb_sends": {"drum2": 28}, "delay_sends": {"synth2": 12}},
    )


def _slow_aurora():
    """Ambient: a real four-chord progression across two patterns, bell
    melody with sample flips and a pitch lane, slow pad filter sweep."""
    pads1 = _synth(
        {0: ([48, 55, 64, 71], 74, 16), 16: ([45, 52, 60, 67], 72, 16)},
        macros={"5": {"0": 25, "8": 45, "16": 60, "24": 40}},
    )
    pads2 = _synth(
        {0: ([41, 48, 57, 65], 74, 16), 16: ([43, 50, 59, 67], 76, 16)},
        macros={"5": {"0": 45, "8": 65, "16": 80, "24": 55}},
    )
    bells = {
        "drum4": _drums(
            {
                4: (62, {"sample": 57}),
                13: (54, {"sample": 62, "probability": 0.6}),
                20: (58, {"sample": 57, "probability": 0.7}),
                27: (50, {"sample": 62}),
            },
            params={"pitch": {"0": 70, "16": 76}},
        ),
    }
    drums = {
        "drum1": _drums({0: 88, 20: (72, {"probability": 0.8})}),
        "drum2": _drums({24: (52, {"sample": 51})}),
        "drum3": _drums({i: (38 + (i % 8), {"probability": 0.6}) for i in range(2, 32, 4)}),
        **bells,
    }
    sub = _synth({0: (36, 80, 14, {"tie": True}), 16: (33, 78, 14)})
    return _project(
        "Slow Aurora",
        84,
        8,
        sounds={
            "synth1": {
                "preset": "pad",
                "name": "Aurora Pad",
                "params": {"filter_frequency": 18, "env1_attack": 70, "chorus_level": 50},
            },
            "synth2": {"preset": "bass", "name": "Aurora Sub", "params": {"filter_frequency": 22}},
            "drum1": {"sample": 49, "decay": 115},
            "drum2": {"sample": 51, "decay": 110},
            "drum3": {"sample": 53, "level": 70},
            "drum4": {"sample": 57, "level": 76, "decay": 120},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": pads1, "synth2": sub}},
            "p2": {"length": 32, "tracks": drums | {"synth1": pads2, "synth2": sub}},
        },
        order=["p1", "p2"],
        fx={
            "reverb_preset": 5,
            "delay_preset": 14,
            "reverb_sends": {"synth1": 55, "drum4": 70, "drum2": 60},
            "delay_sends": {"drum4": 35},
        },
    )


def _fog_lines():
    """Trip-hop: brushed snare, sparse kick, sub slides, dark slow-LFO pad,
    rim ghosts low in the mix."""
    drums = {
        "drum1": _drums({0: 104, 9: (84, {"probability": 0.7}), 16: 100}),
        "drum2": _drums({8: 92, 24: 94}),
        "drum3": _drums({**_every(2, 4, 44), 14: (70, {"sample": 54})}),
        "drum4": _drums({5: (40, {"probability": 0.5, "sample": 56}), 21: (44, {"probability": 0.5, "sample": 56})}),
    }
    bass = _synth({0: (43, 96, 3, {"tie": True}), 6: (41, 84, 2), 16: (39, 96, 4, {"tie": True}), 24: (38, 88, 3)})
    pad = _synth({0: ([55, 62, 70], 66, 16), 16: ([53, 60, 68], 64, 16)}, mixer={"reverb_send": {"0": 40, "16": 70}})
    return _project(
        "Fog Lines",
        86,
        6,
        sounds={
            "synth1": {"preset": "bass", "name": "Fog Sub", "params": {"filter_frequency": 26, "portamento_rate": 50}},
            "synth2": {
                "preset": "pad",
                "name": "Fog Pad",
                "params": {"filter_frequency": 32, "lfo1_rate": 18, "env1_attack": 55},
            },
            "drum1": {"sample": 48, "decay": 100},
            "drum2": {"sample": 51, "pitch": 60},
            "drum3": {"sample": 52, "decay": 70, "level": 78},
            "drum4": {"sample": 56, "level": 64, "pan": 44},
        },
        patterns={"p1": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": pad}}},
        order=["p1"],
        fx={
            "reverb_preset": 4,
            "delay_preset": 13,
            "reverb_sends": {"synth2": 50, "drum2": 45},
            "delay_sends": {"synth1": 10},
        },
        swing=56,
    )


def _still_water():
    """Near-beatless ambient: heartbeat kick, level-lane pad swells, bell
    arpeggio on coin-flip probability, long noise wash."""
    pad1 = _synth({0: ([50, 57, 64, 72], 70, 16)}, mixer={"level": {"0": 60, "8": 90, "16": 110, "24": 75}})
    pad2 = _synth({0: ([48, 55, 64, 71], 70, 16)}, mixer={"level": {"0": 70, "8": 100, "16": 85, "24": 60}})
    arp = {i: (62 + [0, 5, 7, 12][(i // 2) % 4], 48, 0.8, {"probability": 0.5}) for i in range(0, 32, 2)}
    drums = {
        "drum1": _drums({0: 72, 2: 48}),
        "drum2": _drums({16: (60, {"sample": 55})}),
        "drum3": _drums({}),
        "drum4": _drums({}),
    }
    return _project(
        "Still Water",
        72,
        12,
        sounds={
            "synth1": {
                "preset": "pad",
                "name": "Water Pad",
                "params": {"filter_frequency": 24, "env1_attack": 85, "env1_release": 110},
            },
            "synth2": {
                "preset": "pluck",
                "name": "Droplet",
                "params": {"osc1_wave": 0, "env1_release": 80, "filter_frequency": 55},
            },
            "drum1": {"sample": 49, "decay": 127, "pitch": 56, "level": 86},
            "drum2": {"sample": 55, "decay": 127, "level": 60},
            "drum3": {"sample": 53},
            "drum4": {"sample": 57},
        },
        patterns={
            "p1": {"length": 32, "tracks": drums | {"synth1": pad1, "synth2": _synth(arp)}},
            "p2": {"length": 32, "tracks": drums | {"synth1": pad2, "synth2": _synth(arp)}},
        },
        order=["p1", "p2"],
        fx={
            "reverb_preset": 5,
            "delay_preset": 15,
            "reverb_sends": {"synth2": 70, "drum1": 25, "drum2": 80},
            "delay_sends": {"synth2": 40},
        },
    )


def _drift_field():
    """Dub techno: soft four-floor, dub chord on 2 with a delay-send spike
    that echoes into the bar, half-note bass, shaker air."""
    chord = _synth(
        {2: ([57, 60, 64, 67], 88, 1.5), 18: ([57, 60, 64, 67], 80, 1.5, {"probability": 0.85})},
        mixer={"delay_send": {"2": 100, "6": 30, "18": 95, "22": 25}},
    )
    drums = {
        "drum1": _drums(_every(0, 8, 100)),
        "drum2": _drums({8: (70, {"sample": 59}), 24: (72, {"sample": 59})}),
        "drum3": _drums(_every(4, 8, 64)),
        "drum4": _drums({i: (36 + (i % 8) * 2, {"probability": 0.65}) for i in range(2, 32, 4)}),
    }
    bass = _synth({0: (33, 92, 7, {"tie": True}), 16: (33, 88, 7), 30: (40, 70, 1)})
    return _project(
        "Drift Field",
        118,
        1,
        sounds={
            "synth1": {"preset": "bass", "name": "Drift Sub", "params": {"filter_frequency": 20}},
            "synth2": {
                "preset": "pluck",
                "name": "Dub Chord",
                "params": {"filter_frequency": 38, "filter_resonance": 30, "chorus_level": 35},
            },
            "drum1": {"sample": 48, "decay": 95, "level": 96},
            "drum2": {"sample": 59, "level": 80},
            "drum3": {"sample": 52, "decay": 72, "level": 72},
            "drum4": {"sample": 53, "level": 68},
        },
        patterns={"p1": {"length": 32, "tracks": drums | {"synth1": bass, "synth2": chord}}},
        order=["p1"],
        fx={
            "reverb_preset": 5,
            "delay_preset": 6,
            "reverb_sends": {"synth2": 45, "drum2": 55},
            "delay_sends": {"synth2": 60, "drum2": 30},
        },
    )


def build_projects():
    builders = [
        _neon_harbor,
        _velvet_loop,
        _glass_garden,
        _midnight_mall,
        _concrete_pulse,
        _voltage_run,
        _iron_orbit,
        _strobe_sector,
        _rust_funk,
        _circuit_breaker,
        _pixel_alley,
        _crunch_time,
        _slow_aurora,
        _fog_lines,
        _still_water,
        _drift_field,
    ]
    out = []
    for build in builders:
        spec = build()
        song = parse_song(spec)
        out.append((spec["name"], song_to_ncs(song)))
    return out


# --- Pack assembly ---------------------------------------------------------


def main():
    samples_dir = OUT / "samples"
    patches_dir = OUT / "patches"
    projects_dir = OUT / "projects"
    samples_dir.mkdir(parents=True, exist_ok=True)
    patches_dir.mkdir(parents=True, exist_ok=True)
    projects_dir.mkdir(parents=True, exist_ok=True)

    all_samples = bank_deep() + bank_punch() + bank_crunch() + bank_air()
    assert len(all_samples) == 64

    index_samples = []
    for i, (name, data) in enumerate(all_samples):
        write_wav(samples_dir / f"sample_{i}.wav", data)
        index_samples.append({"name": name, "url": f"samples/sample_{i}.wav"})
        print(f"  sample_{i:02d}  {name:14s} {len(data) / SR:.2f}s")

    all_patches = build_patches() + build_variation_patches()
    assert len(all_patches) == 128, len(all_patches)
    index_patches = []
    for i, builder in enumerate(all_patches):
        syx = builder.build_syx()
        (patches_dir / f"patch_{i}.syx").write_bytes(syx)
        name = builder.build()[:16].decode("ascii", "replace").strip()
        index_patches.append({"name": name, "url": f"patches/patch_{i}.syx"})
        print(f"  patch_{i:03d}  {name}")

    index_projects = []
    for i, (name, data) in enumerate(build_projects()):
        (projects_dir / f"project_{i}.ncs").write_bytes(data)
        index_projects.append({"name": name, "url": f"projects/project_{i}.ncs"})
        print(f"  project_{i:02d}  {name}")

    index = {
        "name": "Web Tracks Starter",
        "product": "circuit-tracks",
        "version": "1.0",
        "projects": index_projects,
        "samples": index_samples,
        "patches": index_patches,
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"\nPack written to {OUT}")


if __name__ == "__main__":
    main()
