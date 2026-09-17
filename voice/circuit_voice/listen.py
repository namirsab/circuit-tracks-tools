"""When the agent listens.

Three modes: ``ptt`` (listening while the key is held), ``toggle`` (one press
starts, the next press or the auto-stop timer stops) and ``open`` (always
forwarding; the server's voice activity detection decides). The controller
is a pure state machine fed by ``key_down``/``key_up``; in production a pynput
global listener feeds it from its own thread, in tests the test does. Every
transition emits exactly one ``listen_start`` or ``listen_stop`` event, which
the session uses for earcons, activity signals and the terminal view.
"""

from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable
from typing import Any

MODES = ("ptt", "toggle", "open")
TAIL_S = 0.35  # speech still forwarded after the stop key

PERMISSION_HINT_DE = (
    "Die Taste kann nicht abgefangen werden. Erlaube dem Terminal den Zugriff unter "
    "Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen, oder starte mit --mode open."
)


def key_name(key: Any) -> str | None:
    """pynput gives Key objects (.name) or KeyCode objects (.char)."""
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return name
    char = getattr(key, "char", None)
    return char if isinstance(char, str) else None


class ListenController:
    def __init__(
        self,
        mode: str,
        key: str = "alt_r",
        max_utterance_s: float = 20.0,
        on_event: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        listener_factory: Callable[..., Any] | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        self.key = key
        self.max_utterance_s = max_utterance_s
        self._on_event = on_event or (lambda _e: None)
        self._clock = clock
        self._listener_factory = listener_factory
        self._listener: Any = None
        self._terminal_thread: Any = None
        self._terminal_stop: Any = None
        self._listening = False
        self._key_held = False
        self._started_at = 0.0
        self._stopped_at: float | None = None
        self.tail_s = TAIL_S

    # --- state ---

    @property
    def forwarding(self) -> bool:
        """True while speech should reach the model, including a short tail after the key.

        People press the key while the last word is still leaving their mouth, and the
        audio driver delivers it a little later; closing at once cut sentence ends off.
        """
        if self.mode == "open" or self._listening:
            return True
        return self._stopped_at is not None and self._clock() - self._stopped_at < self.tail_s

    def close_tail(self) -> None:
        """The session ends the utterance: nothing more is forwarded until the next start."""
        self._stopped_at = None

    def _start(self) -> None:
        if not self._listening:
            self._listening = True
            self._started_at = self._clock()
            self._on_event("listen_start")

    def _stop(self) -> None:
        if self._listening:
            self._listening = False
            self._stopped_at = self._clock()
            self._on_event("listen_stop")

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._stop()
        self._key_held = False
        self.mode = mode

    # --- key events (any thread) ---

    def _matches(self, name: str | None) -> bool:
        if name is None:
            return False
        if name == self.key:
            return True
        # macOS reports the right Option/Command/Shift keys as alt_r sometimes and as alt otherwise
        return self.key.endswith(("_r", "_l")) and name == self.key[:-2]

    def key_down(self, name: str | None) -> None:
        if not self._matches(name) or self.mode == "open" or self._key_held:
            return
        self._key_held = True
        if self.mode == "ptt":
            self._start()
        elif self._listening:
            self._stop()
        else:
            self._start()

    def key_up(self, name: str | None) -> None:
        if not self._matches(name) or self.mode == "open":
            return
        self._key_held = False
        if self.mode == "ptt":
            self._stop()

    def tick(self) -> None:
        """Call periodically: enforces the toggle-mode auto-stop."""
        if self.mode == "toggle" and self._listening and self._clock() - self._started_at >= self.max_utterance_s:
            self._stop()

    # --- global hotkey (production) ---

    def start(self) -> str | None:
        """Start the key listener; returns a German hint when it cannot (permissions)."""
        if self.mode == "open" and self._listener_factory is None:
            return None
        factory = self._listener_factory or self._pynput_factory
        try:
            self._listener = factory(
                on_press=lambda k: self.key_down(key_name(k)), on_release=lambda k: self.key_up(key_name(k))
            )
            if self._listener is not None:
                self._listener.start()
        except Exception:  # noqa: BLE001 - permissions or no display; report, keep running
            self._listener = None
            return PERMISSION_HINT_DE
        # The AX check can be false for a child of a trusted terminal while events still
        # arrive, so it is a warning, not a gate. pynput exposes its own verdict too.
        trusted = getattr(self._listener, "IS_TRUSTED", True) and (
            self._listener_factory is not None or self.process_trusted()
        )
        return None if trusted else PERMISSION_HINT_DE + " (Falls die Töne beim Drücken kommen, ignoriere das.)"

    def start_terminal(self, stream: Any = None) -> str | None:
        """Read the key from the terminal itself (no Accessibility permission needed).

        A terminal cannot report key releases, so ptt becomes toggle: press to
        start, press again to stop. Needs the terminal window to have focus.
        """
        import threading  # noqa: PLC0415

        stream = stream or sys.stdin
        if self.mode == "ptt":
            self.mode = "toggle"
        wanted = {"space": " ", "enter": "\n", "return": "\n", "tab": "\t"}.get(self.key, self.key)

        def reader() -> None:
            fd = None
            old = None
            try:
                import termios  # noqa: PLC0415
                import tty  # noqa: PLC0415

                fd = stream.fileno()
                old = termios.tcgetattr(fd)
                tty.setcbreak(fd)
            except Exception:  # noqa: BLE001 - not a tty (tests): read lines instead
                fd = None
            try:
                while not self._terminal_stop.is_set():
                    ch = stream.read(1)
                    if not ch:
                        return
                    if ch == wanted or (ch == "\r" and wanted == "\n"):
                        self.key_down(self.key)
                        self.key_up(self.key)
            finally:
                if fd is not None and old is not None:
                    import termios  # noqa: PLC0415

                    termios.tcsetattr(fd, termios.TCSADRAIN, old)

        self._terminal_stop = threading.Event()
        self._terminal_thread = threading.Thread(target=reader, daemon=True, name="terminal-key")
        self._terminal_thread.start()
        return f"Taste {self.key} im Terminalfenster: einmal drücken zum Sprechen, nochmal zum Beenden."

    @staticmethod
    def process_trusted() -> bool:
        """macOS: can this process see global key events? (Accessibility permission)"""
        if sys.platform != "darwin":
            return True
        try:
            lib = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            return bool(lib.AXIsProcessTrusted())
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _pynput_factory(**kw):
        from pynput import keyboard  # noqa: PLC0415 - lazy: needs a display session

        return keyboard.Listener(**kw)

    def stop(self) -> None:
        if self._terminal_stop is not None:
            self._terminal_stop.set()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
        self._stop()
