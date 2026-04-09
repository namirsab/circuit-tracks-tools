# Song Format Reference

Complete annotated example for `load_song`. All sections are optional except `patterns`.

```json
{
  "name": "My Song",
  "bpm": 128,
  "swing": 50,
  "color": 3,
  "scale": {
    "root": "C",
    "type": "minor"
  },

  "sounds": {
    "synth1": {
      "preset": "pad",
      "name": "WarmPad",
      "params": {
        "osc1_wave": 2,
        "filter_frequency": 80,
        "filter_resonance": 30,
        "env1_attack": 60,
        "env1_release": 90
      },
      "mod_matrix": [
        {"source": "LFO 1+", "dest": "filter frequency", "depth": 40}
      ],
      "macros": {
        "1": [{"dest": "osc1_wave_interpolate", "start": 0, "end": 127}],
        "5": [{"dest": "filter_frequency", "start": 0, "end": 127}],
        "6": [{"dest": "filter_resonance", "start": 0, "end": 100}]
      }
    },
    "synth2": {
      "preset": "bass",
      "name": "SubBass"
    },
    "drum1": {"sample": 0},
    "drum2": {"sample": 2},
    "drum3": {"sample": 42},
    "drum4": {"sample": 46}
  },

  "fx": {
    "reverb_preset": 3,
    "delay_preset": 5,
    "reverb": {
      "type": 2,
      "decay": 80,
      "damping": 60
    },
    "delay": {
      "time": 64,
      "feedback": 70,
      "width": 100
    },
    "reverb_sends": {
      "synth1": 40,
      "synth2": 10,
      "drum2": 20
    },
    "delay_sends": {
      "synth1": 30,
      "synth2": 15
    },
    "sidechain": {
      "synth1": {
        "source": "drum1",
        "depth": 80,
        "attack": 20,
        "hold": 40,
        "decay": 60
      }
    }
  },

  "mixer": {
    "synth1": {"level": 100, "pan": 64},
    "synth2": {"level": 110, "pan": 64},
    "drum1": {"level": 100, "pan": 64},
    "drum2": {"level": 90, "pan": 40},
    "drum3": {"level": 80, "pan": 90},
    "drum4": {"level": 85, "pan": 64}
  },

  "patterns": {
    "intro": {
      "length": 16,
      "tracks": {
        "synth1": {
          "steps": {
            "0": {"note": 60, "velocity": 100, "gate": 0.8},
            "8": {"note": 63, "velocity": 90, "gate": 0.5}
          }
        },
        "drum1": {
          "steps": {
            "0": {},
            "4": {},
            "8": {},
            "12": {}
          }
        },
        "drum3": {
          "steps": {
            "2": {"velocity": 80},
            "6": {"velocity": 60},
            "10": {"velocity": 80},
            "14": {"velocity": 60}
          }
        }
      }
    },
    "drop": {
      "length": 32,
      "tracks": {
        "synth1": {
          "steps": {
            "0": {"note": 60, "velocity": 127, "gate": 0.9},
            "4": {"note": 60, "velocity": 110, "gate": 0.5},
            "8": {"note": 63, "velocity": 120, "gate": 0.9},
            "12": {"note": 65, "velocity": 100, "gate": 0.5}
          }
        },
        "synth2": {
          "steps": {
            "0": {"note": 36, "velocity": 120, "gate": 0.95},
            "8": {"note": 36, "velocity": 110, "gate": 0.8},
            "16": {"note": 36, "velocity": 120, "gate": 0.95},
            "24": {"note": 39, "velocity": 100, "gate": 0.8}
          }
        },
        "drum1": {
          "steps": {
            "0": {}, "4": {}, "8": {}, "12": {},
            "16": {}, "20": {}, "24": {}, "28": {}
          }
        }
      }
    }
  },

  "song": ["intro", "intro", "drop", "drop", "intro", "drop"]
}
```

## Field Reference

### Top Level
| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `name` | string | "" | Song name (max 16 chars for NCS export) |
| `bpm` | int | 120 | Tempo in beats per minute (40-240) |
| `swing` | int | 50 | Swing amount (0-100, 50 = straight) |
| `color` | int | 0 | Project color on device display (0-15) |
| `scale` | object | none | Scale quantization applied on hardware playback |

### Scale
| Field | Values |
|-------|--------|
| `root` | `C`, `C#`, `D`, `D#`, `E`, `F`, `F#`, `G`, `G#`, `A`, `A#`, `B` |
| `type` | `natural_minor`, `major`, `dorian`, `phrygian`, `mixolydian`, `melodic_minor`, `harmonic_minor`, `bebop_dorian`, `blues`, `minor_pentatonic`, `hungarian_minor`, `ukrainian_dorian`, `marva`, `todi`, `whole_tone`, `chromatic` |

### Sounds
- **Synth presets**: `pad`, `bass`, `lead`, `pluck` (or omit for init patch)
- **Synth `params`**: same names as `create_synth_patch` / `get_parameter_reference()`
- **Synth `mod_matrix`**: list of `{source, dest, depth}` entries
- **Synth `macros`**: knob number (1-8) -> list of `{dest, start, end}` targets
- **Drum `sample`**: sample index (0-63 per drum track)

### Step Format
| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `note` | int | 60 | MIDI note number (ignored for drums) |
| `velocity` | int | 100 | Note velocity (1-127) |
| `gate` | float | 0.5 | Gate length as fraction of step (0.0-1.0) |
| `micro_step` | int | 0 | Sub-step offset for micro-timing (0-5) |

Drum steps: `{}` triggers at default velocity. The `note` field is ignored for drum tracks.

### FX
- `reverb_preset` / `delay_preset`: hardware preset index
- `reverb` / `delay`: parameter overrides (see `get_parameter_reference()` for names)
- `reverb_sends` / `delay_sends`: per-track send levels (0-127)
- `sidechain`: per-synth compression from a drum source

### Song Order
The `song` array lists pattern names in playback order. Patterns can repeat.
If omitted, the sequencer plays patterns in definition order.
