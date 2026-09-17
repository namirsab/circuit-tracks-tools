"""Token usage and an estimated price, from the counts the API reports.

The Live API sends ``usage_metadata`` with its messages; each one describes one
model turn, including the context that was billed again for it. The text model
(sound design, track and song generation) reports usage per response. Prices
are US dollars per million tokens from ai.google.dev/gemini-api/docs/pricing
(read on 2026-09-17); they are an estimate, the invoice is what counts.
"""

from __future__ import annotations

from typing import Any

# model name prefix -> {(direction, modality): $ per 1M tokens}
PRICES: dict[str, dict[tuple[str, str], float]] = {
    "gemini-3.8-live": {
        ("in", "text"): 0.75,
        ("in", "audio"): 3.00,
        ("in", "image"): 1.00,
        ("in", "video"): 1.00,
        ("out", "text"): 4.50,
        ("out", "audio"): 12.00,
    },
    "gemini-3.8-flash": {("in", "text"): 0.75, ("in", "cached"): 0.075, ("out", "text"): 3.75},
}


def _prices(model: str) -> dict[tuple[str, str], float] | None:
    return next((table for prefix, table in PRICES.items() if model.startswith(prefix)), None)


def _by_modality(details: Any, total: int) -> dict[str, int]:
    """``[ModalityTokenCount]`` -> {"text": n, "audio": m}; tokens without details count as text."""
    out: dict[str, int] = {}
    for d in details or []:
        modality = getattr(d.modality, "value", d.modality)
        name = str(modality or "text").lower()
        out[name] = out.get(name, 0) + int(d.token_count or 0)
    rest = total - sum(out.values())
    if rest > 0:
        out["text"] = out.get("text", 0) + rest
    return out


def count(usage: Any) -> dict[str, Any]:
    """Flatten a ``usage_metadata`` object (Live or generate_content) into plain numbers."""

    def n(*names: str) -> int:
        return next((int(v) for name in names if (v := getattr(usage, name, None))), 0)

    prompt = n("prompt_token_count")
    out_total = n("response_token_count", "candidates_token_count")
    out_details = getattr(usage, "response_tokens_details", None) or getattr(usage, "candidates_tokens_details", None)
    return {
        "in": _by_modality(getattr(usage, "prompt_tokens_details", None), prompt),
        "out": _by_modality(out_details, out_total),
        "cached": n("cached_content_token_count"),
        "tool_prompt": n("tool_use_prompt_token_count"),
        "thoughts": n("thoughts_token_count"),
        "total": n("total_token_count"),
    }


def cost_usd(model: str, counted: dict[str, Any]) -> float | None:
    table = _prices(model)
    if table is None:
        return None
    tokens_in = dict(counted["in"])
    cached = min(counted["cached"], tokens_in.get("text", 0)) if ("in", "cached") in table else 0
    tokens_in["text"] = tokens_in.get("text", 0) - cached + counted["tool_prompt"]
    dollars = cached * table.get(("in", "cached"), 0.0)
    for modality, tokens in tokens_in.items():
        dollars += tokens * table.get(("in", modality), table[("in", "text")])
    for modality, tokens in counted["out"].items():
        dollars += tokens * table.get(("out", modality), table[("out", "text")])
    dollars += counted["thoughts"] * table[("out", "text")]  # thinking is billed as output
    return dollars / 1_000_000


class UsageMeter:
    """Running totals per model for one run of the agent."""

    def __init__(self) -> None:
        self.models: dict[str, dict[str, Any]] = {}

    def add(self, model: str, usage: Any) -> dict[str, Any]:
        """Record one report; returns the event to log."""
        counted = count(usage)
        dollars = cost_usd(model, counted)
        entry = self.models.setdefault(model, {"reports": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0})
        entry["reports"] += 1
        entry["tokens_in"] += sum(counted["in"].values()) + counted["tool_prompt"]
        entry["tokens_out"] += sum(counted["out"].values()) + counted["thoughts"]
        if dollars is None:
            entry["usd"] = None
        elif entry["usd"] is not None:
            entry["usd"] += dollars
        return {"type": "usage", "model": model, **counted, "usd": None if dollars is None else round(dollars, 6)}

    def total_usd(self) -> float | None:
        if any(m["usd"] is None for m in self.models.values()):
            return None
        return sum(m["usd"] for m in self.models.values())

    def summary_event(self, seconds: float) -> dict[str, Any]:
        total = self.total_usd()
        models = {
            name: {**m, "usd": None if m["usd"] is None else round(m["usd"], 4)} for name, m in self.models.items()
        }
        per_hour = None if total is None or seconds <= 0 else round(total * 3600 / seconds, 2)
        return {
            "type": "usage_total",
            "seconds": round(seconds),
            "models": models,
            "usd": None if total is None else round(total, 4),
            "usd_per_hour": per_hour,
        }
