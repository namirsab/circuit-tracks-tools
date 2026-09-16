"""German utterance eval: does the model pick the right tool and arguments?

Runs each utterance as a single text turn through generate_content with the
real declarations and system instruction (cheaper and more deterministic than
a live audio session; the hardware checklist is the final word). A small
German state preamble tells the model what is loaded or playing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from google.genai import types

from circuit_voice.bridge import Bridge
from circuit_voice.mcp_client import McpBackend
from circuit_voice.prompt_de import SYSTEM_INSTRUCTION_DE

CASES = [json.loads(line) for line in (Path(__file__).parent / "utterances_de.jsonl").read_text().splitlines() if line]
STATE_DE = {
    None: "",
    "loaded": "(Kontext: Der Song 'Nachtfahrt' ist geladen, Patterns intro, chorus, outro, 120 BPM, gestoppt.)\n",
    "playing": "(Kontext: Der Song 'Nachtfahrt' läuft, Pattern intro, 120 BPM, keine Spur stumm.)\n",
}
GATE = 0.9


def args_match(expected, actual) -> bool:
    if expected is None:
        return True
    for key, want in expected.items():
        got = actual.get(key)
        if isinstance(want, dict):
            if not isinstance(got, dict) or not args_match(want, got):
                return False
        elif (
            isinstance(want, list)
            and len(want) == 2
            and all(isinstance(x, int | float) for x in want)
            and key != "patterns"
        ):
            if not isinstance(got, int | float) or not want[0] <= got <= want[1]:
                return False
        elif want == "*":
            if got is None:
                return False
        elif isinstance(want, str) and want.endswith("*"):
            if not isinstance(got, str) or not got.startswith(want[:-1]):
                return False
        elif got != want:
            return False
    return True


async def test_german_utterances(server):
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("CIRCUIT_VOICE_EVAL_MODEL", "gemini-3.8-flash")
    async with McpBackend("inproc") as backend:
        bridge = Bridge(backend)
        await bridge.prepare()
        tools = [types.Tool(function_declarations=bridge.declarations())]
    rows = []
    hits = 0
    for case in CASES:
        prompt = STATE_DE[case.get("state")] + case["say"]
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION_DE, tools=tools, temperature=0),
        )
        calls = response.function_calls or []
        got = (calls[0].name, dict(calls[0].args or {})) if calls else (None, {})
        want = case["tool"]
        want_names = want if isinstance(want, list) else [want]
        ok = got[0] in want_names and args_match(case.get("args"), got[1])
        hits += ok
        rows.append((ok, case["say"], want_names[0], got[0], json.dumps(got[1], ensure_ascii=False)[:60]))
    accuracy = hits / len(CASES)
    print(f"\nmodel {model}: {hits}/{len(CASES)} = {accuracy:.0%}")
    for ok, say, want, got, args in rows:
        print(f"{'OK ' if ok else 'BAD'} {say!r:50} want {want!s:20} got {got!s:20} {args}")
    assert accuracy >= GATE, f"accuracy {accuracy:.0%} below gate {GATE:.0%}"


def test_every_m1_card_is_covered():
    from circuit_voice.tool_cards import cards_for_profile

    covered = {t for c in CASES for t in (c["tool"] if isinstance(c["tool"], list) else [c["tool"]])}
    missing = {c.name for c in cards_for_profile("full")} - covered
    assert missing <= {"play_drum", "set_song"}, missing
    assert sum(1 for c in CASES if c["tool"] is None) >= 5


@pytest.fixture(autouse=True)
def _need_key():
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
