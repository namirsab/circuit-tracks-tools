"""Events log: one JSON line per event, never audio, never the API key."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TextIO


class EventLog:
    def __init__(self, path: Path | None, redact: list[str] | None = None):
        self.path = path
        self._redact = [r for r in (redact or []) if r]
        self._fh: TextIO | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def write(self, event: dict[str, Any]) -> str | None:
        line = json.dumps({"ts": time.time(), **event}, ensure_ascii=False, default=str)
        for secret in self._redact:
            line = line.replace(secret, "<redacted>")
        if self._fh is not None:
            self._fh.write(line + "\n")
            self._fh.flush()
        return line

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
