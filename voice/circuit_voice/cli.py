"""Command line entry point and configuration for circuit-voice.

``Config.from_args`` is pure (argv + environment in, dataclass out) so it can be
unit-tested; ``main`` is the only place that touches devices or the network.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__

MODES = ("ptt", "toggle", "open")
TOOL_PROFILES = ("control", "creative", "full", "perform")
DEFAULT_LOG = Path.home() / ".circuit-voice" / "events.jsonl"


class ConfigError(ValueError):
    """Invalid or missing configuration; the message is meant for the terminal."""


@dataclass(frozen=True)
class Config:
    api_key: str | None
    model: str = "gemini-3.8-live"
    composer_model: str = "gemini-3.8-flash"
    voice: str = "Kore"
    mode: str = "ptt"
    ptt_key: str = "alt_r"
    silence_ms: int = 1200
    max_utterance_s: int = 60
    key_source: str = "global"
    mcp: str = "inproc"
    midi_port: str | None = None
    no_midi: bool = False
    tools: str = "creative"
    song: str | None = None
    songs_dir: str | None = None
    patch_cache: Path | None = Path.home() / ".circuit-voice" / "patches.json"
    output_device: str | None = None
    input_device: str | None = None
    log_path: Path | None = DEFAULT_LOG
    max_minutes: int | None = None
    quiet: bool = False
    dry_run: str | None = None
    list_devices: bool = False

    @classmethod
    def from_args(cls, argv: list[str], env: dict[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        ns = build_parser().parse_args(argv)
        if ns.mode not in MODES:
            raise ConfigError(f"--mode must be one of {', '.join(MODES)}")
        if ns.tools not in TOOL_PROFILES:
            raise ConfigError(f"--tools must be one of {', '.join(TOOL_PROFILES)}")
        if ns.key_source not in ("global", "terminal"):
            raise ConfigError("--key-source must be global or terminal")
        if not 500 <= ns.silence_ms <= 3000:
            raise ConfigError("--silence-ms must be between 500 and 3000")
        api_key = env.get("GEMINI_API_KEY") or None
        if api_key is None and not ns.list_devices:
            raise ConfigError("GEMINI_API_KEY is not set. Export it or put it in your shell profile.")
        log_path = None if ns.no_log else Path(ns.log)
        return cls(
            api_key=api_key,
            model=ns.model,
            composer_model=ns.composer_model,
            voice=ns.voice,
            mode=ns.mode,
            ptt_key=ns.ptt_key,
            silence_ms=ns.silence_ms,
            max_utterance_s=ns.max_utterance,
            key_source=ns.key_source,
            mcp=ns.mcp,
            midi_port=ns.midi_port,
            no_midi=ns.no_midi,
            tools={"full": "creative", "perform": "control"}.get(ns.tools, ns.tools),
            song=ns.song,
            songs_dir=ns.songs_dir,
            patch_cache=Path(ns.patch_cache) if ns.patch_cache else None,
            output_device=ns.output_device,
            input_device=ns.input_device,
            log_path=log_path,
            max_minutes=ns.max_minutes,
            quiet=ns.quiet,
            dry_run=ns.dry_run,
            list_devices=ns.list_devices,
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="circuit-voice",
        description="German voice control for the Novation Circuit Tracks (Gemini Live + circuit-tracks MCP tools).",
    )
    p.add_argument("--version", action="version", version=f"circuit-voice {__version__}")
    p.add_argument("--list-devices", action="store_true", help="print audio devices and exit (no API key needed)")
    p.add_argument("--dry-run", metavar="TEXT", help="send TEXT to a text-only session, print tool calls, exit")

    g = p.add_argument_group("listening")
    g.add_argument("--mode", default="ptt", help="ptt (hold key), toggle (press to start/stop) or open (server VAD)")
    g.add_argument("--ptt-key", default="alt_r", help="key for ptt/toggle modes (pynput name, e.g. alt_r, f13, space)")
    g.add_argument("--silence-ms", type=int, default=1200, help="end-of-speech silence for open mode (500-3000)")
    g.add_argument("--max-utterance", type=int, default=60, help="auto-stop seconds for toggle mode")
    g.add_argument(
        "--key-source",
        default="global",
        help="global (hotkey via Accessibility permission) or terminal (key read from this window; toggle only)",
    )

    g = p.add_argument_group("audio")
    g.add_argument("--input-device", help="microphone name or index (default: system default)")
    g.add_argument("--output-device", help="speaker name or index for the agent's voice (default: system default)")
    g.add_argument("--voice", default="Kore", help="Gemini prebuilt voice name")

    g = p.add_argument_group("models")
    g.add_argument("--model", default="gemini-3.8-live", help="Live model id")
    g.add_argument("--composer-model", default="gemini-3.8-flash", help="text model for compose/revise")

    g = p.add_argument_group("instrument")
    g.add_argument("--mcp", default="inproc", help="inproc (default), stdio:<command> or an http(s) MCP URL")
    g.add_argument("--midi-port", help="MIDI port name (default: first port containing 'Circuit Tracks')")
    g.add_argument("--no-midi", action="store_true", help="skip the MIDI connection (development)")
    g.add_argument(
        "--tools", default="creative", help="tool profile: creative (everything, default) or control (no model jobs)"
    )
    g.add_argument("--song", help="song JSON file to load at start")
    g.add_argument("--songs-dir", help="folder of song JSON files the user can load by voice")
    g.add_argument(
        "--patch-cache", default=str(Path.home() / ".circuit-voice" / "patches.json"), help="cached patch names"
    )

    g = p.add_argument_group("session")
    g.add_argument("--log", default=str(DEFAULT_LOG), help="events log (JSON lines)")
    g.add_argument("--no-log", action="store_true", help="disable the events log")
    g.add_argument("--max-minutes", type=int, help="end the session after this many minutes")
    g.add_argument("--quiet", action="store_true", help="no terminal status view")
    return p


def main(argv: list[str] | None = None) -> None:
    try:
        cfg = Config.from_args(sys.argv[1:] if argv is None else argv)
    except ConfigError as e:
        print(f"circuit-voice: {e}", file=sys.stderr)
        sys.exit(2)
    if cfg.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    from .app import main_async  # noqa: PLC0415 - keep --help fast

    try:
        code = main_async(cfg)
    except KeyboardInterrupt:
        print("\ncircuit-voice: beendet.", file=sys.stderr)
        code = 0
    # Audio and MIDI libraries leave non-daemon threads behind; the session has
    # already shut everything down cleanly, so leave without waiting for them.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
