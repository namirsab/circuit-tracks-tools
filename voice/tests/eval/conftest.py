"""Opt-in evals against the real Gemini API: CIRCUIT_VOICE_EVAL=1 and GEMINI_API_KEY."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("CIRCUIT_VOICE_EVAL") and os.environ.get("GEMINI_API_KEY"):
        return
    skip = pytest.mark.skip(reason="set CIRCUIT_VOICE_EVAL=1 and GEMINI_API_KEY to run the model evals")
    for item in items:
        if "/eval/" in str(item.fspath):
            item.add_marker(skip)
