"""Token usage reports become events and an estimated price."""

from __future__ import annotations

import pytest
from google.genai import types
from test_session import FakeLiveSession, harness

from circuit_voice.ui import UIState, render
from circuit_voice.usage import UsageMeter, cost_usd, count


def live_usage():
    return types.UsageMetadata(
        prompt_token_count=11_000,
        response_token_count=500,
        total_token_count=11_500,
        prompt_tokens_details=[
            types.ModalityTokenCount(modality="TEXT", token_count=10_000),
            types.ModalityTokenCount(modality="AUDIO", token_count=1_000),
        ],
        response_tokens_details=[types.ModalityTokenCount(modality="AUDIO", token_count=500)],
    )


def test_live_turn_is_priced_per_modality():
    counted = count(live_usage())
    assert counted["in"] == {"text": 10_000, "audio": 1_000} and counted["out"] == {"audio": 500}
    # 10k text in at 0.75, 1k audio in at 3.00, 500 audio out at 12.00 (per million)
    assert cost_usd("gemini-3.8-live", counted) == pytest.approx(0.0075 + 0.003 + 0.006)


def test_text_model_uses_candidates_and_the_cached_price():
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=4_000,
        candidates_token_count=1_000,
        cached_content_token_count=3_000,
        total_token_count=5_000,
    )
    counted = count(usage)
    assert counted["in"] == {"text": 4_000} and counted["out"] == {"text": 1_000}
    assert cost_usd("gemini-3.8-flash", counted) == pytest.approx((1_000 * 0.75 + 3_000 * 0.075 + 1_000 * 3.75) / 1e6)


def test_unknown_model_counts_tokens_without_a_price():
    meter = UsageMeter()
    event = meter.add("some-other-model", live_usage())
    assert event["usd"] is None and event["total"] == 11_500
    assert meter.total_usd() is None
    assert "unbekannt" in render(meter.summary_event(60), UIState(mode="ptt"))


def test_summary_adds_up_and_is_spoken_for():
    meter = UsageMeter()
    for _ in range(10):
        meter.add("gemini-3.8-live", live_usage())
    total = meter.summary_event(seconds=600)
    assert total["usd"] == pytest.approx(0.165) and total["usd_per_hour"] == pytest.approx(0.99)
    assert total["models"]["gemini-3.8-live"]["tokens_in"] == 110_000
    line = render(total, UIState(mode="ptt"))
    assert "0.17 $" in line and "10 Minuten" in line


async def test_session_logs_the_usage_the_server_reports(server):
    fake = FakeLiveSession([types.LiveServerMessage(usage_metadata=live_usage())])
    async with harness(server, [fake]) as (session, connector, events, midi):
        session.usage = UsageMeter()
        await session.run(max_connections=1)
    usage = [e for e in events if e["type"] == "usage"]
    assert len(usage) == 1 and usage[0]["in"]["audio"] == 1_000 and usage[0]["usd"] > 0
    assert usage[0]["model"] == session.config.model
