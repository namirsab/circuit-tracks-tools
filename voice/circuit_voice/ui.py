"""Terminal status view.

Append-only lines rather than a redrawn dashboard, so a helper using a screen
reader can follow along too. The listening banner is loud and distinct; every
transcript, action and latency gets its own line.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

LISTENING = "●●● HÖRT ZU ●●●   (loslassen zum Senden)"
IDLE = "— — —  Pause  — — —   (Taste halten zum Sprechen)"
OPEN = "●●● OFFEN ●●●   (hört immer zu)"


@dataclass
class UIState:
    mode: str = "ptt"
    listening: bool = False
    connected: bool = False
    lines: list[str] = field(default_factory=list)


def banner(state: UIState) -> str:
    if state.mode == "open":
        return OPEN
    return LISTENING if state.listening else IDLE


def render(event: dict[str, Any], state: UIState) -> str | None:
    """Turn one session event into a terminal line (or None for noise)."""
    kind = event.get("type")
    if kind == "listen_start":
        state.listening = True
        return banner(state)
    if kind == "listen_stop":
        state.listening = False
        return banner(state)
    if kind == "connected":
        state.connected = True
        return "verbunden" + (" (fortgesetzt)" if event.get("handle") else "")
    if kind == "reconnect":
        state.connected = False
        return "Verbindung wird erneuert …"
    if kind == "input_transcript":
        return f"Du:    {event.get('text', '')}"
    if kind == "output_transcript":
        return f"Agent: {event.get('text', '')}"
    if kind == "tool_call":
        latency = event.get("latency")
        tail = f"   ({latency:.2f} s nach Loslassen)" if isinstance(latency, int | float) else ""
        return f"→ {event.get('name')} {event.get('args') or ''}{tail}"
    if kind == "tool_result":
        resp = event.get("response") or {}
        text = resp.get("error") or resp.get("result")
        return f"   {'✗' if 'error' in resp else '✓'} {str(text)[:160]}"
    if kind == "interrupted":
        return "   (unterbrochen)"
    if kind == "error":
        return f"Fehler: {event.get('message')}"
    if kind == "go_away":
        return "Server beendet die Verbindung gleich; ich verbinde neu."
    if kind == "goodbye":
        return event.get("text", "")
    if kind == "hint":
        return f"Hinweis: {event.get('text')}"
    return None


class TerminalUI:
    def __init__(self, mode: str, out: TextIO = sys.stdout, quiet: bool = False):
        self.state = UIState(mode=mode)
        self.out = out
        self.quiet = quiet

    def handle(self, event: dict[str, Any]) -> None:
        line = render(event, self.state)
        if line is not None and not self.quiet:
            print(line, file=self.out, flush=True)
